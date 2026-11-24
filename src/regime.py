"""Regime state machine, as defined in "Erfolgreich wissenschaftlich investieren" (2nd ed., p. 46,
"Steuerung der Regimes in Krisen (vereinfacht)"). Only the price conditions are automated:

    Normal -> Crisis       loss vs. 3-year high          > 20%   (close < 0.80 * high3y)
    Crisis -> Escalation   loss since Crisis started     > 25%   (close < 0.75 * anchor)
    Escalation -> Crisis   gain vs. 3-year low           > 50%   (close > 1.50 * low3y)
    Crisis -> Normal       gain since Crisis started     > 25%   (close > 1.25 * anchor)

The anchor is the close on the day Crisis was entered (from Normal or from Escalation).
The regime is path-dependent, so it is computed by replaying the full daily history.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

REGIMES = ("normal", "crisis", "escalation")
RANK = {r: i for i, r in enumerate(REGIMES)}
LABEL = {"normal": "Normal", "crisis": "Crisis", "escalation": "Escalation"}
ASSETS = ("acwi", "small_cap", "bonds", "cash")
EQUITY = ("acwi", "small_cap")
DEFENSIVE = ("bonds", "cash")
EPS = 1e-10  # float noise guard: exactly -20% must not count as "more than 20%"


def window_days(years: float) -> int:
    return int(round(365.25 * years))


def rolling_extremes(df: pd.DataFrame, years: float) -> pd.DataFrame:
    """Add high3y/low3y: max/min close over the trailing window (t - N days, t], today included."""
    s = df.set_index("date")["close"]
    win = f"{window_days(years)}D"
    out = df.copy()
    out["high3y"] = s.rolling(win).max().to_numpy()
    out["low3y"] = s.rolling(win).min().to_numpy()
    return out


def window_extreme_dates(df: pd.DataFrame, years: float) -> tuple[str, str]:
    """Dates of the current window's high and low (for display)."""
    last = df["date"].iloc[-1]
    w = df[df["date"] > last - pd.Timedelta(days=window_days(years))]
    return (w.loc[w["close"].idxmax(), "date"].strftime("%Y-%m-%d"),
            w.loc[w["close"].idxmin(), "date"].strftime("%Y-%m-%d"))


@dataclass
class PathResult:
    df: pd.DataFrame  # date, close, high3y, low3y, regime, anchor
    events: list = field(default_factory=list)

    @property
    def regime(self) -> str:
        return self.df["regime"].iloc[-1]

    @property
    def anchor(self) -> float | None:
        a = self.df["anchor"].iloc[-1]
        return None if pd.isna(a) else float(a)

    @property
    def since(self) -> str:
        return self.events[-1]["date"] if self.events else self.df["date"].iloc[0].strftime("%Y-%m-%d")


def regime_path(df: pd.DataFrame, rules: dict) -> PathResult:
    nc = rules["normal_to_crisis_drop_from_3y_high"]
    ce = rules["crisis_to_escalation_drop_from_anchor"]
    ec = rules["escalation_to_crisis_rise_from_3y_low"]
    cn = rules["crisis_to_normal_rise_from_anchor"]

    ext = rolling_extremes(df, rules["window_years"])
    regime, anchor = "normal", None
    regimes, anchors, events = [], [], []
    for d, c, h, lo in zip(ext["date"], ext["close"], ext["high3y"], ext["low3y"]):
        new = ref_name = ref = None
        if regime == "normal":
            if c / h - 1 < -nc - EPS:
                new, ref_name, ref = "crisis", "3y high", h
        elif regime == "crisis":
            if c / anchor - 1 < -ce - EPS:
                new, ref_name, ref = "escalation", "Crisis start", anchor
            elif c / anchor - 1 > cn + EPS:
                new, ref_name, ref = "normal", "Crisis start", anchor
        else:  # escalation
            if c / lo - 1 > ec + EPS:
                new, ref_name, ref = "crisis", "3y low", lo

        if new:
            move = c / ref - 1
            events.append({
                "date": d.strftime("%Y-%m-%d"), "from": regime, "to": new,
                "direction": "down" if RANK[new] > RANK[regime] else "up",
                "trigger": f"{move * 100:+.1f}% vs {ref_name} {ref:,.2f}",
                "reference": ref_name, "reference_level": round(float(ref), 2),
                "move": round(float(move), 4), "close": round(float(c), 2),
            })
            regime = new
            anchor = float(c) if new == "crisis" else None
        regimes.append(regime)
        anchors.append(anchor)

    ext["regime"] = regimes
    ext["anchor"] = anchors
    return PathResult(ext, events)


def trigger_levels(regime: str, high3y: float, low3y: float, anchor: float | None, rules: dict) -> list[dict]:
    """The exit conditions active in the current regime, as index levels."""
    if regime == "normal":
        p = rules["normal_to_crisis_drop_from_3y_high"]
        return [{"to": "crisis", "op": "below", "level": high3y * (1 - p), "basis": "3y high",
                 "basis_level": high3y, "pct": -p}]
    if regime == "crisis":
        pe, pn = rules["crisis_to_escalation_drop_from_anchor"], rules["crisis_to_normal_rise_from_anchor"]
        return [{"to": "escalation", "op": "below", "level": anchor * (1 - pe), "basis": "Crisis start",
                 "basis_level": anchor, "pct": -pe},
                {"to": "normal", "op": "above", "level": anchor * (1 + pn), "basis": "Crisis start",
                 "basis_level": anchor, "pct": pn}]
    p = rules["escalation_to_crisis_rise_from_3y_low"]
    return [{"to": "crisis", "op": "above", "level": low3y * (1 + p), "basis": "3y low",
             "basis_level": low3y, "pct": p}]


def defensive_pct(alloc: dict) -> float:
    return sum(alloc[a] for a in DEFENSIVE)


@dataclass
class Transition:
    old: str
    new: str
    direction: str  # "down" (worse regime), "up" (recovery) or "none"
    deltas_pp: dict = field(default_factory=dict)  # per asset, new - old target
    deltas_eur: dict = field(default_factory=dict)


def transition(old: str | None, new: str, allocations: dict, portfolio_value: float) -> Transition | None:
    """Compare the current regime with the last *notified* regime. None on first run."""
    if old is None:
        return None
    if old == new:
        return Transition(old, new, "none")
    direction = "down" if RANK[new] > RANK[old] else "up"
    pp = {a: allocations[new][a] - allocations[old][a] for a in ASSETS}
    eur = {a: round(portfolio_value * v / 100.0, 2) for a, v in pp.items()}
    return Transition(old, new, direction, pp, eur)
