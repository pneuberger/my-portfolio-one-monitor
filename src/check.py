"""Daily entry point: fetch -> classify -> refill -> notify -> write state and dashboard data.

    python -m src.check [--dry-run] [--history-csv PATH] [--state PATH] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import backtest, chart, notify, refill as rf
from .data import fetch_history, load_history, save_history
from .regime import ASSETS, LABEL, ath, classify, defensive_pct, drawdown, threshold_levels, transition

ROOT = Path(__file__).resolve().parent.parent
BERLIN = ZoneInfo("Europe/Berlin")
MAX_LIVE_EVENTS = 200


def default_state() -> dict:
    return {"last_notified_regime": None, "regime_since": None, "refill": rf.empty(),
            "failed_fetches": 0, "last_close_date": None, "last_error": None, "events": []}


def load_json(path: Path, default):
    if path.exists() and path.stat().st_size:
        return json.loads(path.read_text())
    return default


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(obj, separators=(",", ":")) + "\n")
    else:
        path.write_text(json.dumps(obj, indent=2) + "\n")


def evaluate(df: pd.DataFrame, cfg: dict, state: dict, today: date, fetch_ok: bool,
             fetch_error: str | None, chart_path: Path | None) -> list[notify.Message]:
    """Pure-ish core: mutates state, returns messages. Used by tests with synthetic data."""
    msgs: list[notify.Message] = []

    # Data health
    if fetch_ok:
        state["failed_fetches"] = 0
        state["last_error"] = None
    else:
        state["failed_fetches"] += 1
        state["last_error"] = fetch_error
        n, first, every = state["failed_fetches"], cfg["data_alert_after_failures"], cfg["data_alert_repeat_every"]
        if n == first or (n > first and (n - first) % every == 0):
            msgs.append(notify.data_problem(n, fetch_error, state.get("last_close_date")))

    if df.empty:
        return msgs

    th = cfg["thresholds"]
    a = ath(df["close"])
    ath_date = df.loc[df["close"].idxmax(), "date"]
    last = df.iloc[-1]
    dd = drawdown(float(last["close"]), a)
    regime = classify(dd, th)
    state["last_close_date"] = last["date"].strftime("%Y-%m-%d")
    ctx = {"close": float(last["close"]), "date": state["last_close_date"], "ath": a,
           "ath_date": ath_date.strftime("%Y-%m-%d"), "drawdown": dd, "regime": regime,
           "levels": threshold_levels(a, th), "chart": chart_path}

    old = state.get("last_notified_regime")
    tr = transition(old, regime, cfg["allocations"], cfg["portfolio_value_eur"])
    if tr is None:
        msgs.append(notify.started(ctx, cfg))
        state["last_notified_regime"] = regime
        state["regime_since"] = ctx["date"]
    elif tr.direction != "none":
        if tr.direction == "down":
            state["refill"] = rf.cancel(state["refill"])
            msgs.append(notify.regime_down(ctx, cfg, tr))
        else:
            gap_pp = defensive_pct(cfg["allocations"][tr.new]) - defensive_pct(cfg["allocations"][tr.old])
            state["refill"] = rf.start_or_extend(state["refill"], gap_pp, today, cfg["payday_day"])
            gap_eur, filled_eur = rf.amounts(state["refill"], cfg)
            msgs.append(notify.regime_up(ctx, cfg, tr, {**state["refill"], "gap_eur": gap_eur,
                                                        "filled_eur": filled_eur}))
        state["events"].append({"date": ctx["date"], "from": tr.old, "to": tr.new, "direction": tr.direction,
                                "drawdown": round(dd, 4), "close": round(ctx["close"], 2), "source": "live"})
        state["events"] = state["events"][-MAX_LIVE_EVENTS:]
        state["last_notified_regime"] = regime
        state["regime_since"] = ctx["date"]

    for ev in rf.step(state["refill"], today, cfg):
        if ev["type"] == "refill_reminder":
            cap = rf.cap_date(ev, cfg["refill_cap_months"])
            msgs.append(notify.refill_reminder(ctx, cfg, ev, cap.isoformat() if cap else "-"))
        elif ev["type"] == "refill_complete":
            msgs.append(notify.refill_complete(ctx, cfg))
        elif ev["type"] == "sell_remainder":
            msgs.append(notify.sell_remainder(ctx, cfg, ev["remaining_eur"]))

    if cfg.get("weekly_summary") and today.weekday() == 5:
        gap_eur, filled_eur = rf.amounts(state["refill"], cfg)
        msgs.append(notify.weekly(ctx, cfg, {**state["refill"], "gap_eur": gap_eur, "filled_eur": filled_eur}))
    return msgs


def dashboard_data(df: pd.DataFrame, cfg: dict, state: dict, today: date) -> dict:
    """Public file: no EUR amounts, nothing that reveals portfolio size."""
    th = cfg["thresholds"]
    out = {"generated": datetime.now(BERLIN).isoformat(timespec="seconds"), "today": today.isoformat(),
           "index_name": cfg["index_name"], "thresholds": th, "allocations": cfg["allocations"],
           "asset_labels": cfg["asset_labels"], "assets": list(ASSETS),
           "normal_contribution_mode": cfg.get("normal_contribution_mode", "by_target"),
           "refill_cap_months": cfg["refill_cap_months"],
           "payday_day": cfg["payday_day"], "failed_fetches": state["failed_fetches"],
           "last_error": state.get("last_error")}
    if df.empty:
        return out
    a = ath(df["close"])
    last = df.iloc[-1]
    dd = drawdown(float(last["close"]), a)
    r = state.get("last_notified_regime") or classify(dd, th)
    cap = rf.cap_date(state["refill"], cfg["refill_cap_months"])
    out.update({
        "series": {"dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
                   "closes": [round(float(c), 2) for c in df["close"]]},
        "close": round(float(last["close"]), 2), "close_date": last["date"].strftime("%Y-%m-%d"),
        "ath": round(a, 2), "ath_date": df.loc[df["close"].idxmax(), "date"].strftime("%Y-%m-%d"),
        "drawdown": round(dd, 5), "regime": r, "regime_label": LABEL[r],
        "regime_since": state.get("regime_since"),
        "levels": {k: round(v, 2) for k, v in threshold_levels(a, th).items()},
        "refill": {"active": state["refill"]["active"], "started": state["refill"]["started"],
                   "gap_pp": state["refill"]["gap_pp"], "cap_date": cap.isoformat() if cap else None,
                   "progress": round(rf.progress(state["refill"], cfg), 4) if state["refill"]["active"] else 0,
                   "months_left_estimate": rf.months_left(state["refill"], cfg) if state["refill"]["active"] else 0},
        "contribution_split_pct": rf.contribution_split_pct(cfg, state["refill"]["active"], r),
        "events": {"live": state["events"], "backtest": backtest.regime_events(df, th)},
    })
    return out


PRIVATE_KEYS = {"portfolio_value_eur": "PORTFOLIO_VALUE_EUR", "monthly_contribution_eur": "MONTHLY_CONTRIBUTION_EUR"}


def load_config(path: Path) -> dict:
    """Public config.json + private amounts from env (GitHub secrets) or config.local.json (gitignored)."""
    cfg = json.loads(path.read_text())
    local = path.with_name("config.local.json")
    if local.exists():
        cfg.update(json.loads(local.read_text()))
    for key, env in PRIVATE_KEYS.items():
        if os.environ.get(env):
            cfg[key] = float(os.environ[env])
    return cfg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MSCI World regime monitor")
    p.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    p.add_argument("--history-csv", type=Path, help="use this CSV (date,close) instead of fetching")
    p.add_argument("--state", type=Path, default=ROOT / "data" / "state.json")
    p.add_argument("--history", type=Path, default=ROOT / "data" / "history.csv")
    p.add_argument("--docs", type=Path, default=ROOT / "docs")
    p.add_argument("--config", type=Path, default=ROOT / "config.json")
    p.add_argument("--today", type=date.fromisoformat, help="override run date (testing)")
    p.add_argument("--test-notification", action="store_true", help="send one test push and exit")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.test_notification:
        notify.send(notify.test_message(cfg["dashboard_url"]), cfg["dashboard_url"], args.dry_run)
        return 0
    missing = [env for key, env in PRIVATE_KEYS.items() if not cfg.get(key)]
    if missing:
        print(f"Missing {', '.join(missing)}: set them as GitHub secrets or in config.local.json.", file=sys.stderr)
        return 2

    today = args.today or datetime.now(BERLIN).date()
    state = {**default_state(), **load_json(args.state, {})}
    state["refill"] = {**rf.empty(), **state.get("refill", {})}

    if args.history_csv:
        df = load_history(args.history_csv)
        prev = pd.Timestamp(state["last_close_date"]) if state.get("last_close_date") else None
        ok = not df.empty and (prev is None or df["date"].max() > prev)
        err = None
    else:
        res = fetch_history(args.history, cfg["ticker"])
        df, ok, err = res.history, res.new_close, res.error
        if res.error:
            print(f"Fetch error: {res.error}", file=sys.stderr)
        elif not df.empty:
            save_history(df, args.history)

    chart_path = args.docs / "chart.png"
    if not df.empty:
        tmp_regime = classify(drawdown(float(df["close"].iloc[-1]), ath(df["close"])), cfg["thresholds"])
        chart.render(df, cfg["thresholds"], tmp_regime, chart_path)

    msgs = evaluate(df, cfg, state, today, ok, err, chart_path if chart_path.exists() else None)

    failed = 0
    for m in msgs:
        try:
            notify.send(m, cfg["dashboard_url"], args.dry_run)
        except Exception as e:
            failed += 1
            print(f"Send failed for {m.kind}: {e}", file=sys.stderr)

    write_json(args.docs / "data.json", dashboard_data(df, cfg, state, today), compact=True)
    if failed:
        # Keep the previous state so the alert is retried on the next run.
        print(f"{failed} message(s) failed; state not advanced.", file=sys.stderr)
        return 1
    write_json(args.state, state)
    if not msgs:
        print("No notifications today.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
