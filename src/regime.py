"""Regime classification: drawdown from the all-time high (ATH) of daily closes."""
from __future__ import annotations

from dataclasses import dataclass, field

REGIMES = ("normal", "crisis", "escalation")
RANK = {r: i for i, r in enumerate(REGIMES)}
LABEL = {"normal": "Normal", "crisis": "Crisis", "escalation": "Escalation"}
ASSETS = ("acwi", "small_cap", "bonds", "cash")
EQUITY = ("acwi", "small_cap")
DEFENSIVE = ("bonds", "cash")


def ath(closes) -> float:
    """All-time high = max close in the full history."""
    return float(max(closes))


def drawdown(close: float, ath_value: float) -> float:
    """Drawdown as a positive fraction: 0.25 means 25% below the ATH."""
    if ath_value <= 0:
        raise ValueError("ATH must be positive")
    return max(0.0, 1.0 - close / ath_value)


def classify(dd: float, thresholds: dict) -> str:
    """Normal: dd < crisis. Crisis: crisis <= dd <= escalation. Escalation: dd > escalation.

    Values are rounded to 1e-9 so that e.g. a close of exactly 80.0 vs ATH 100.0
    counts as exactly 20% despite float noise.
    """
    d = round(dd, 9)
    crisis = round(thresholds["crisis"], 9)
    esc = round(thresholds["escalation"], 9)
    if d < crisis:
        return "normal"
    if d <= esc:
        return "crisis"
    return "escalation"


def threshold_levels(ath_value: float, thresholds: dict) -> dict:
    return {
        "crisis": ath_value * (1 - thresholds["crisis"]),
        "escalation": ath_value * (1 - thresholds["escalation"]),
    }


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
