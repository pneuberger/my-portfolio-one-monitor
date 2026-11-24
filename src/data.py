"""Fetch MSCI World daily closes from Yahoo and merge them into data/history.csv.

Never falls back to another series: ETF prices have a different scale and ATH.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")


@dataclass
class FetchResult:
    history: pd.DataFrame  # columns: date (datetime64, naive), close (float)
    new_close: bool
    error: str | None = None


def load_history(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "close": pd.Series(dtype=float)})
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def save_history(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out["close"] = out["close"].round(4)
    out.to_csv(path, index=False)


def _download(ticker: str, start: str | None) -> pd.DataFrame:
    import yfinance as yf  # imported lazily so tests run without network

    t = yf.Ticker(ticker)
    if start:
        raw = t.history(start=start, interval="1d", auto_adjust=False, raise_errors=True)
    else:
        raw = t.history(period="max", interval="1d", auto_adjust=False, raise_errors=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no rows for {ticker}")
    idx = raw.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(NY).tz_localize(None)
    df = pd.DataFrame({"date": idx.normalize(), "close": raw["Close"].astype(float).values})
    return df.dropna()


def drop_incomplete(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop today's bar if the US session has not closed yet (before 18:00 New York)."""
    now = (now or datetime.now(NY)).astimezone(NY)
    today = pd.Timestamp(now.date())
    if now.hour < 18:
        df = df[df["date"] < today]
    else:
        df = df[df["date"] <= today]
    return df


def merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    both = pd.concat([old, new], ignore_index=True)
    both = both.drop_duplicates(subset="date", keep="last").sort_values("date")
    return both.reset_index(drop=True)


def fetch_history(path: Path, ticker: str, retries: int = 3, backoff: float = 10.0,
                  downloader=_download, now: datetime | None = None) -> FetchResult:
    old = load_history(path)
    last = old["date"].max() if not old.empty else None
    start = (last - timedelta(days=7)).strftime("%Y-%m-%d") if last is not None else None

    error = None
    for attempt in range(1, retries + 1):
        try:
            new = drop_incomplete(downloader(ticker, start), now)
            merged = merge(old, new)
            new_close = merged["date"].max() > last if last is not None else not merged.empty
            return FetchResult(merged, bool(new_close))
        except Exception as e:  # network, Yahoo blocking, parsing
            error = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(backoff * attempt)
    return FetchResult(old, False, error)
