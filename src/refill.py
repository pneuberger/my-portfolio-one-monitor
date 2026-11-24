"""Refill phase after an upward crossing: no sales, contributions go to bonds and cash.

The public repo must not reveal portfolio size, so the state stores the gap in
percentage points of the portfolio and the number of paid months. EUR amounts are
computed at runtime from PORTFOLIO_VALUE_EUR / MONTHLY_CONTRIBUTION_EUR (secrets)
and only appear in notifications.

Server-side progress is an estimate: each payday is assumed to add one monthly
contribution. The dashboard uses real holdings (localStorage) when available.

State shape (state["refill"]):
    active, started (YYYY-MM-DD), gap_pp, paid_months, last_reminder_month (YYYY-MM)
"""
from __future__ import annotations

import calendar
import math
from datetime import date

from .regime import ASSETS, DEFENSIVE, EQUITY


def empty() -> dict:
    return {"active": False, "started": None, "gap_pp": 0.0, "paid_months": 0, "last_reminder_month": None}


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cap_date(refill: dict, cap_months: int) -> date | None:
    if not refill.get("started"):
        return None
    return add_months(date.fromisoformat(refill["started"]), cap_months)


def amounts(refill: dict, cfg: dict) -> tuple[float, float]:
    """(gap_eur, filled_eur) using the private config values."""
    gap = refill["gap_pp"] / 100.0 * cfg["portfolio_value_eur"]
    filled = refill["paid_months"] * cfg["monthly_contribution_eur"]
    return round(gap, 2), round(filled, 2)


def progress(refill: dict, cfg: dict) -> float:
    gap, filled = amounts(refill, cfg)
    return min(1.0, filled / gap) if gap > 0 else 0.0


def months_left(refill: dict, cfg: dict) -> int:
    gap, filled = amounts(refill, cfg)
    return max(0, math.ceil((gap - filled) / cfg["monthly_contribution_eur"] - 1e-9))


def start_or_extend(refill: dict, gap_pp: float, today: date, payday_day: int) -> dict:
    """Start a refill, or add to an open one (keeping the original start date)."""
    if refill.get("active"):
        refill["gap_pp"] = refill["gap_pp"] + gap_pp
        return refill
    r = empty()
    r.update(active=True, started=today.isoformat(), gap_pp=gap_pp)
    # This month's contribution has already gone out if payday has passed.
    if today.day >= payday_day:
        r["last_reminder_month"] = today.strftime("%Y-%m")
    return r


def cancel(refill: dict) -> dict:
    return empty()


def contribution_split_pct(cfg: dict, refill_active: bool, regime: str) -> dict:
    """Share of the monthly contribution per asset, in percent."""
    if refill_active:
        return {a: (100.0 / len(DEFENSIVE) if a in DEFENSIVE else 0.0) for a in ASSETS}
    alloc = cfg["allocations"][regime]
    if cfg.get("normal_contribution_mode", "by_target") == "all_equity":
        eq = sum(alloc[a] for a in EQUITY)
        return {a: (100.0 * alloc[a] / eq if a in EQUITY else 0.0) for a in ASSETS}
    return {a: float(alloc[a]) for a in ASSETS}


def step(refill: dict, today: date, cfg: dict) -> list[dict]:
    """Run the daily refill checks. Mutates refill; returns events in order."""
    events: list[dict] = []
    if not refill.get("active"):
        return events

    month = today.strftime("%Y-%m")
    if today.day >= cfg["payday_day"] and refill.get("last_reminder_month") != month:
        refill["paid_months"] += 1
        refill["last_reminder_month"] = month
        gap, filled = amounts(refill, cfg)
        events.append({"type": "refill_reminder", "filled_eur": filled, "gap_eur": gap,
                       "started": refill["started"]})

    gap, filled = amounts(refill, cfg)
    if filled >= gap - 0.005:
        events.append({"type": "refill_complete"})
        refill.update(empty())
        return events

    cap = cap_date(refill, cfg["refill_cap_months"])
    if cap and today >= cap:
        events.append({"type": "sell_remainder", "remaining_eur": round(gap - filled, 2)})
        refill.update(empty())
    return events
