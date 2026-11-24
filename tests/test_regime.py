import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src import backtest, refill as rf
from src.check import default_state, evaluate
from src.data import drop_incomplete, fetch_history
from src.regime import classify, drawdown, transition

CFG = {**json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text()),
       "portfolio_value_eur": 40000, "monthly_contribution_eur": 800}
TH = CFG["thresholds"]


def df_of(closes, start="2024-01-01"):
    return pd.DataFrame({"date": pd.bdate_range(start, periods=len(closes)), "close": [float(c) for c in closes]})


def kinds(msgs):
    return [m.kind for m in msgs]


def run(closes, state, today=date(2025, 3, 20), ok=True, err=None):
    return evaluate(df_of(closes), CFG, state, today, ok, err, None)


def state_in(regime):
    s = default_state()
    s["last_notified_regime"] = regime
    return s


# --- classification and boundaries -------------------------------------------------

@pytest.mark.parametrize("close,expected", [
    (100, "normal"), (80.01, "normal"), (80, "crisis"), (70, "crisis"),
    (60, "crisis"), (59.99, "escalation"), (30, "escalation"),
])
def test_boundaries(close, expected):
    assert classify(drawdown(close, 100.0), TH) == expected


def test_ath_is_max_close_not_last():
    msgs = run([100, 120, 96], default_state())  # 96/120 = -20% exactly
    assert "Crisis" in msgs[0].title


# --- transitions --------------------------------------------------------------------

DOWN = [("normal", "crisis"), ("crisis", "escalation"), ("normal", "escalation")]
UP = [("escalation", "crisis"), ("crisis", "normal"), ("escalation", "normal")]
LEVEL = {"normal": 100, "crisis": 70, "escalation": 50}


@pytest.mark.parametrize("old,new", DOWN)
def test_downward_crossings(old, new):
    s = state_in(old)
    msgs = run([100, LEVEL[new]], s)
    assert kinds(msgs) == ["regime_down"]
    assert msgs[0].priority == "high"
    assert s["last_notified_regime"] == new
    assert not s["refill"]["active"]


@pytest.mark.parametrize("old,new,gap", [("escalation", "crisis", 4000), ("crisis", "normal", 4000),
                                         ("escalation", "normal", 8000)])
def test_upward_crossings_start_refill(old, new, gap):
    s = state_in(old)
    msgs = run([100, LEVEL[new]], s)
    assert kinds(msgs) == ["regime_up"]
    assert s["refill"]["active"] and rf.amounts(s["refill"], CFG)[0] == gap
    assert "no sales" in msgs[0].body


def test_deltas_normal_to_crisis():
    tr = transition("normal", "crisis", CFG["allocations"], 40000)
    assert tr.deltas_pp == {"acwi": 8, "small_cap": 2, "bonds": -5, "cash": -5}
    assert tr.deltas_eur["acwi"] == 3200 and tr.deltas_eur["bonds"] == -2000


def test_no_change_no_message():
    s = state_in("normal")
    assert run([100, 95], s) == []


# --- first run, missed days, state ----------------------------------------------------

def test_first_run_sends_started_only():
    s = default_state()
    msgs = run([100, 70], s)
    assert kinds(msgs) == ["started"]
    assert s["last_notified_regime"] == "crisis"
    assert not s["refill"]["active"]


def test_compares_with_last_notified_not_yesterday():
    # Missed days: the series went Normal -> Crisis -> Escalation while we were not running.
    s = state_in("normal")
    msgs = run([100, 75, 55], s)
    assert kinds(msgs) == ["regime_down"]
    assert "Normal -> Escalation" in msgs[0].title


def test_bounce_alerts_every_crossing():
    s = state_in("normal")
    assert kinds(run([100, 79], s)) == ["regime_down"]
    assert kinds(run([100, 79, 81], s)) == ["regime_up"]
    assert kinds(run([100, 79, 81, 80], s)) == ["regime_down"]
    assert not s["refill"]["active"]  # the downward crossing cancelled the refill


# --- refill ------------------------------------------------------------------------

def test_refill_payday_progress_and_complete():
    s = state_in("crisis")
    run([100, 90], s, today=date(2025, 3, 20))  # Crisis -> Normal, gap 4000 = 5 contributions
    assert s["refill"]["last_reminder_month"] == "2025-03"  # March contribution already went out
    assert kinds(run([100, 90], s, today=date(2025, 3, 21))) == []
    for i, month in enumerate([4, 5, 6, 7], start=1):
        assert kinds(run([100, 90], s, today=date(2025, month, 1))) == ["refill_reminder"]
        assert kinds(run([100, 90], s, today=date(2025, month, 2))) == []  # once per month
        assert s["refill"]["paid_months"] == i
    assert kinds(run([100, 90], s, today=date(2025, 8, 1))) == ["refill_reminder", "refill_complete"]
    assert not s["refill"]["active"]


def test_payday_on_weekend_fires_next_run():
    s = state_in("crisis")
    run([100, 90], s, today=date(2025, 5, 20))
    # 1 June 2025 is a Sunday; the next run is Tuesday 3 June.
    assert kinds(run([100, 90], s, today=date(2025, 6, 3))) == ["refill_reminder"]


def test_refill_cap_triggers_when_gap_large():
    cfg_state = state_in("escalation")
    cfg_state["refill"] = rf.start_or_extend(rf.empty(), 40, date(2025, 1, 15), 1)  # 40pp = EUR 16,000
    events = []
    for m in range(2, 13):
        events += rf.step(cfg_state["refill"], date(2025, m, 3), CFG)
    assert cfg_state["refill"]["paid_months"] == 11
    events = rf.step(cfg_state["refill"], date(2026, 1, 15), CFG)
    types = [e["type"] for e in events]
    assert types == ["refill_reminder", "sell_remainder"]
    assert events[-1]["remaining_eur"] == 6400
    assert not cfg_state["refill"]["active"]


def test_second_upward_crossing_extends_refill_keeps_start():
    s = state_in("escalation")
    run([100, 70], s, today=date(2025, 2, 10))  # Esc -> Crisis, gap 4000
    assert kinds(run([100, 70], s, today=date(2025, 3, 4))) == ["refill_reminder"]
    run([100, 90], s, today=date(2025, 4, 10))  # Crisis -> Normal, +5000
    assert rf.amounts(s["refill"], CFG) == (8000, 1600)  # March + April paydays
    assert s["refill"]["started"] == "2025-02-10"


def test_contribution_split_modes():
    assert rf.contribution_split_pct(CFG, True, "normal") == {"acwi": 0, "small_cap": 0, "bonds": 50, "cash": 50}
    assert rf.contribution_split_pct(CFG, False, "normal") == {"acwi": 70, "small_cap": 10, "bonds": 10, "cash": 10}
    eq = rf.contribution_split_pct({**CFG, "normal_contribution_mode": "all_equity"}, False, "normal")
    assert eq["acwi"] == 87.5 and eq["bonds"] == 0


def test_public_dashboard_data_has_no_eur_amounts():
    from src.check import dashboard_data
    s = state_in("escalation")
    run([100, 90], s, today=date(2025, 3, 20))
    blob = json.dumps(dashboard_data(df_of([100, 90]), CFG, s, date(2025, 3, 20)))
    assert "40000" not in blob and "8000" not in blob and "eur" not in blob.lower().replace("eur inflation", "")
    assert "_eur" not in json.dumps(s)


# --- data problems -----------------------------------------------------------------

def test_data_problem_after_two_failures_then_every_five():
    s = state_in("normal")
    alerts = []
    for i in range(1, 13):
        alerts.append("data_problem" in kinds(run([100, 95], s, ok=False, err=None)))
    assert [i + 1 for i, a in enumerate(alerts) if a] == [2, 7, 12]
    run([100, 95], s, ok=True)
    assert s["failed_fetches"] == 0


def test_fetch_retries_then_reports(tmp_path):
    calls = []

    def boom(ticker, start):
        calls.append(start)
        raise ConnectionError("blocked")

    res = fetch_history(tmp_path / "h.csv", "^X", retries=3, backoff=0, downloader=boom)
    assert len(calls) == 3 and not res.new_close and "blocked" in res.error


def test_fetch_merges_and_detects_new_close(tmp_path):
    path = tmp_path / "h.csv"
    df_of([100, 101]).assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")).to_csv(path, index=False)
    now = datetime(2025, 1, 1, 6, 0, tzinfo=ZoneInfo("UTC"))
    res = fetch_history(path, "^X", downloader=lambda t, s: df_of([100, 101, 102]), backoff=0, now=now)
    assert res.new_close and len(res.history) == 3
    res2 = fetch_history(path, "^X", downloader=lambda t, s: df_of([100, 101]), backoff=0, now=now)
    assert not res2.new_close


def test_drop_incomplete_today_bar():
    df = pd.DataFrame({"date": pd.to_datetime(["2025-03-19", "2025-03-20"]), "close": [1.0, 2.0]})
    ny = ZoneInfo("America/New_York")
    assert len(drop_incomplete(df, datetime(2025, 3, 20, 11, 0, tzinfo=ny))) == 1
    assert len(drop_incomplete(df, datetime(2025, 3, 20, 19, 0, tzinfo=ny))) == 2


# --- backtest ----------------------------------------------------------------------

def test_backtest_uses_running_ath():
    ev = backtest.regime_events(df_of([100, 75, 55, 70, 85, 130, 100]), TH)
    assert [(e["from"], e["to"]) for e in ev] == [
        ("normal", "crisis"), ("crisis", "escalation"), ("escalation", "crisis"),
        ("crisis", "normal"), ("normal", "crisis")]
