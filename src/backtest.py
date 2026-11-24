"""Regime history since the start of the data, using the same rules as the live check.

Each day's drawdown is measured against the running ATH (max close up to that day).
"""
from __future__ import annotations

import pandas as pd

from .regime import RANK, classify


def regime_events(df: pd.DataFrame, thresholds: dict) -> list[dict]:
    events: list[dict] = []
    if df.empty:
        return events
    running_ath = df["close"].cummax()
    current = None
    for d, close, a in zip(df["date"], df["close"], running_ath):
        dd = 1.0 - close / a
        r = classify(dd, thresholds)
        if current is not None and r != current:
            events.append({
                "date": d.strftime("%Y-%m-%d"),
                "from": current,
                "to": r,
                "direction": "down" if RANK[r] > RANK[current] else "up",
                "drawdown": round(dd, 4),
                "close": round(float(close), 2),
                "source": "backtest",
            })
        current = r
    return events
