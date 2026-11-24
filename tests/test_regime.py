import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src import backtest, refill as rf
from src.check import RULES_VERSION, default_state, evaluate
from src.data import drop_incomplete, fetch_history
from src.regime import regime_path, rolling_extremes, transition, trigger_levels

CFG = {**json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text()),
       "portfolio_value_eur": 40000, "monthly_contribution_eur": 800}
RULES = CFG["rules"]


def df_of(closes, start="2024-01-01"):
    return pd.DataFrame({"date": pd.bdate_range(start, periods=len(closes)), "close": [float(c) for c in closes]})


def kinds(msgs):
    return [m.kind for m in msgs]


def run(closes, state, today=date(2025, 3, 20), ok=True, err=None):
    return evaluate(df_of(closes), CFG, state, today, ok, err, None)


def state_in(regime):
    s = default_state()
    s["last_notified_regime"] = regime
    s["rules_version"] = RULES_VERSION
    return s


def regimes(closes, start="2024-01-01"):
    return list(regime_path(df_of(closes, start), RULES).df["regime"])


# --- the four book transitions, strict boundaries -----------------------------------

def test_normal_to_crisis_strictly_more_than_20pct_below_3y_high():
    assert regimes([100, 80])[-1] == "normal"        # exactly -20% is not "more than 20%"
    assert regimes([100, 79.99])[-1] == "crisis"


def test_crisis_to_escalation_25pct_below_anchor_not_high():
    # Anchor = 79 (close on the day Crisis started). 0.75 * 79 = 59.25
    assert regimes([100, 79, 59.25])[-1] == "crisis"
    assert regimes([100, 79, 59.24])[-1] == "escalation"
    # 61 is -39% vs the high but only -22.8% vs the anchor -> still Crisis
    assert regimes([100, 79, 61])[-1] == "crisis"


def test_escalation_to_crisis_50pct_above_3y_low():
    # 3y low = 59 -> 1.5 * 59 = 88.5
    assert regimes([100, 79, 59, 88.5])[-1] == "escalation"
    assert regimes([100, 79, 59, 88.51])[-1] == "crisis"


def test_crisis_to_normal_25pct_above_anchor():
    # Anchor 79 -> 1.25 * 79 = 98.75
    assert regimes([100, 79, 98.75])[-1] == "crisis"
    assert regimes([100, 79, 98.76])[-1] == "normal"


def test_hysteresis_stays_crisis_on_partial_recovery():
    # Back to -10% vs the high, but not +25% vs the anchor -> still Crisis
    assert regimes([100, 79, 85, 90]) == ["normal", "crisis", "crisis", "crisis"]


def test_full_cycle_and_anchor_reset():
    path = regime_path(df_of([100, 79, 59, 89, 70, 112]), RULES)
    assert list(path.df["regime"]) == ["normal", "crisis", "escalation", "crisis", "crisis", "normal"]
    assert [e["to"] for e in path.events] == ["crisis", "escalation", "crisis", "normal"]
    # Re-entering Crisis from Escalation resets the anchor to 89:
    # 70 is -21% vs 89 (no Escalation), 112 is +25.8% vs 89 (Normal).
    assert list(path.df["anchor"])[3:5] == [89.0, 89.0]
    assert path.events[1]["reference"] == "Crisis start" and path.events[2]["reference"] == "3y low"


def test_trigger_levels_per_regime():
    lv = trigger_levels("normal", 100, 60, None, RULES)
    assert [(x["to"], x["op"], round(x["level"], 2)) for x in lv] == [("crisis", "below", 80.0)]
    lv = trigger_levels("crisis", 100, 60, 79, RULES)
    assert [(x["to"], round(x["level"], 2)) for x in lv] == [("escalation", 59.25), ("normal", 98.75)]
    lv = trigger_levels("escalation", 100, 59, None, RULES)
    assert [(x["to"], round(x["level"], 2)) for x in lv] == [("crisis", 88.5)]


# --- 3-year window --------------------------------------------------------------------

def test_window_is_three_years_including_today():
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=1200, freq="D"),
                       "close": [float(i) for i in range(1200)]})
    ext = rolling_extremes(df, 3)
    assert ext["high3y"].iloc[-1] == 1199          # today included
    assert ext["low3y"].iloc[-1] == 1199 - 1095    # 1096-day window


def test_old_high_rolls_out_of_window():
    # 100, then 4 years at 85 (-15%, Normal), then 79: vs the 3y high (85) that is only -7%.
    closes = [100] + [85] * (4 * 261) + [79]
    assert regimes(closes)[-1] == "normal"
    # Same drop within 3 years of the high -> Crisis
    closes = [100] + [85] * (2 * 261) + [79]
    assert regimes(closes)[-1] == "crisis"


# --- notifications ------------------------------------------------------------------

def test_downward_alert_crisis():
    s = state_in("normal")
    msgs = run([100, 79], s)
    assert kinds(msgs) == ["regime_down"] and msgs[0].priority == "high"
    assert "-21.0% vs 3y high 100.00" in msgs[0].body
    assert s["last_notified_regime"] == "crisis"


def test_upward_alert_starts_refill():
    s = state_in("escalation")
    msgs = run([100, 79, 59, 89], s)
    assert kinds(msgs) == ["regime_up"] and "no sales" in msgs[0].body
    assert s["refill"]["active"] and rf.amounts(s["refill"], CFG)[0] == 4000


def test_deltas_normal_to_crisis():
    tr = transition("normal", "crisis", CFG["allocations"], 40000)
    assert tr.deltas_pp == {"acwi": 8, "small_cap": 2, "bonds": -5, "cash": -5}
    assert tr.deltas_eur["acwi"] == 3200 and tr.deltas_eur["bonds"] == -2000


def test_no_change_no_message():
    assert run([100, 95], state_in("normal")) == []


def test_first_run_sends_started_only():
    s = default_state()
    msgs = run([100, 79], s)
    assert kinds(msgs) == ["started"]
    assert s["last_notified_regime"] == "crisis" and s["rules_version"] == RULES_VERSION
    assert "Escalation if close below 59" in msgs[0].body


def test_rules_change_restarts_instead_of_crossing_alert():
    s = state_in("normal")
    s["rules_version"] = None  # state written by the old ATH rules
    msgs = run([100, 79], s)
    assert kinds(msgs) == ["restarted"]
    assert s["last_notified_regime"] == "crisis" and s["rules_version"] == RULES_VERSION
    assert not s["refill"]["active"]


def test_missed_days_one_alert_with_all_triggers():
    s = state_in("normal")
    s["last_close_date"] = "2024-01-01"
    msgs = run([100, 79, 59], s)   # Normal -> Crisis -> Escalation while not running
    assert kinds(msgs) == ["regime_down"]
    assert "Normal -> Escalation" in msgs[0].title
    assert msgs[0].body.count("on 2024-01-0") == 2


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

def test_backtest_matches_live_engine():
    ev = backtest.regime_events(df_of([100, 79, 59, 89, 112]), RULES)
    assert [(e["from"], e["to"]) for e in ev] == [
        ("normal", "crisis"), ("crisis", "escalation"), ("escalation", "crisis"), ("crisis", "normal")]
    assert all(e["source"] == "backtest" for e in ev)


def test_expected_close_date():
    from src.check import expected_close_date
    utc = ZoneInfo("UTC")
    assert expected_close_date(datetime(2026, 11, 24, 6, 15, tzinfo=utc)) == date(2026, 11, 23)  # Tue -> Mon
    assert expected_close_date(datetime(2026, 11, 28, 6, 15, tzinfo=utc)) == date(2026, 11, 27)  # Sat -> Fri
    assert expected_close_date(datetime(2026, 11, 24, 20, 30, tzinfo=utc)) == date(2026, 11, 23)  # session open
    assert expected_close_date(datetime(2026, 11, 24, 23, 30, tzinfo=utc)) == date(2026, 11, 24)  # after close
    assert expected_close_date(datetime(2026, 11, 30, 6, 15, tzinfo=utc)) == date(2026, 11, 27)  # Mon -> Fri
