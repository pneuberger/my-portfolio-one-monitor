"""Regime history since the start of the data, using the same engine as the live check."""
from __future__ import annotations

import pandas as pd

from .regime import regime_path


def regime_events(df: pd.DataFrame, rules: dict) -> list[dict]:
    if df.empty:
        return []
    return [{**e, "source": "backtest"} for e in regime_path(df, rules).events]
