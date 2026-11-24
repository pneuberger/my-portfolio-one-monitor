"""ntfy.sh messages. Only automates the user's own rules; no investment advice."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .regime import ASSETS, LABEL


@dataclass
class Message:
    kind: str
    title: str
    body: str
    priority: str = "default"  # min, low, default, high, urgent
    tags: str = ""
    attach: Path | None = None


def eur(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}EUR {abs(x):,.0f}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _status(ctx: dict) -> str:
    return (f"Drawdown: -{pct(ctx['drawdown'])} | Close: {ctx['close']:,.2f} ({ctx['date']})\n"
            f"ATH: {ctx['ath']:,.2f} ({ctx['ath_date']})")


def _target(cfg: dict, regime: str) -> str:
    a = cfg["allocations"][regime]
    lab = cfg["asset_labels"]
    return "Target " + LABEL[regime] + ": " + ", ".join(f"{lab[k]} {a[k]}%" for k in ASSETS)


def started(ctx: dict, cfg: dict) -> Message:
    r = ctx["regime"]
    return Message("started", f"Monitoring started - regime {LABEL[r]}",
                   f"{_status(ctx)}\n{_target(cfg, r)}\n"
                   f"Crisis below {ctx['levels']['crisis']:,.0f}, Escalation below {ctx['levels']['escalation']:,.0f}.",
                   "default", "chart_with_upwards_trend")


def regime_down(ctx: dict, cfg: dict, tr) -> Message:
    lab = cfg["asset_labels"]
    lines = []
    for a in ASSETS:
        pp, e = tr.deltas_pp[a], tr.deltas_eur[a]
        if pp:
            verb = "buy" if pp > 0 else "use"
            lines.append(f"  {lab[a]}: {pp:+d} pp ({verb} {eur(abs(e))})")
    return Message(
        "regime_down", f"Regime {LABEL[tr.old]} -> {LABEL[tr.new]}: buy equities with bonds+cash",
        f"{_status(ctx)}\nRule: shift bonds and cash into equities.\n" + "\n".join(lines) +
        f"\n(EUR based on configured portfolio value {eur(cfg['portfolio_value_eur'])}.)\n{_target(cfg, tr.new)}",
        "high", "rotating_light,chart_with_downwards_trend", ctx.get("chart"))


def regime_up(ctx: dict, cfg: dict, tr, refill: dict) -> Message:
    c = cfg["monthly_contribution_eur"]
    return Message(
        "regime_up", f"Regime {LABEL[tr.old]} -> {LABEL[tr.new]}: refill phase starts",
        f"{_status(ctx)}\nRule: no sales. Send 100% of monthly contributions to bonds and cash "
        f"(50/50, {eur(c / 2)} each) until they are back at target.\n"
        f"Refill gap (estimate): {eur(refill['gap_eur'])} - about {-(-(refill['gap_eur'] - refill['filled_eur']) // c):.0f} months of contributions. "
        f"Safety net: sell the remainder after {cfg['refill_cap_months']} months "
        f"(from {refill['started']}).\n{_target(cfg, tr.new)}",
        "high", "white_check_mark,chart_with_upwards_trend", ctx.get("chart"))


def refill_reminder(ctx: dict, cfg: dict, refill: dict, cap: str) -> Message:
    c = cfg["monthly_contribution_eur"]
    return Message(
        "refill_reminder", "Payday: contribution to bonds + cash",
        f"Refill phase active. This month: {eur(c / 2)} to bonds, {eur(c / 2)} to cash, nothing to equities.\n"
        f"Progress (estimate, incl. this month): {eur(refill['filled_eur'])} of {eur(refill['gap_eur'])}. "
        f"Safety-net date: {cap}.\nUpdate holdings in the dashboard for the exact gap.\n{_status(ctx)}",
        "default", "moneybag")


def refill_complete(ctx: dict, cfg: dict) -> Message:
    mode = cfg.get("normal_contribution_mode", "by_target")
    return Message("refill_complete", "Refill complete - bonds and cash back at target",
                   f"Contributions return to normal mode ({mode}).\n{_target(cfg, ctx['regime'])}\n{_status(ctx)}",
                   "low", "tada")


def sell_remainder(ctx: dict, cfg: dict, remaining: float) -> Message:
    return Message(
        "sell_remainder", "Refill not done after 12 months: sell the remainder",
        f"Safety net reached. Rule: sell equities to bring bonds and cash back to target. "
        f"Remaining gap (estimate): {eur(remaining)} - check the dashboard with current holdings.\n"
        f"{_target(cfg, ctx['regime'])}\n{_status(ctx)}",
        "high", "warning")


def data_problem(failures: int, error: str | None, last_date: str | None) -> Message:
    reason = error or "no new close received"
    return Message("data_problem", "Data problem: MSCI World close missing",
                   f"{failures} consecutive runs without a new close. Last close: {last_date or 'none'}.\n"
                   f"Reason: {reason}\nCheck the GitHub Actions log.", "high", "warning")


def weekly(ctx: dict, cfg: dict, refill: dict) -> Message:
    extra = ""
    if refill.get("active"):
        extra = f"\nRefill: {eur(refill['filled_eur'])} of {eur(refill['gap_eur'])}."
    return Message("weekly", f"Weekly: regime {LABEL[ctx['regime']]}, -{pct(ctx['drawdown'])}",
                   f"{_status(ctx)}\n{_target(cfg, ctx['regime'])}{extra}", "low", "calendar")


def test_message(dashboard_url: str) -> Message:
    return Message("test", "Test notification", f"ntfy works. Dashboard: {dashboard_url}", "default", "bell")


def send(msg: Message, click_url: str, dry_run: bool = False) -> None:
    if dry_run:
        print(f"--- [{msg.kind}] priority={msg.priority} tags={msg.tags} "
              f"attach={msg.attach.name if msg.attach else '-'} click={click_url}")
        print(f"TITLE: {msg.title}\n{msg.body}\n")
        return
    import requests

    server = os.environ.get("NTFY_SERVER") or "https://ntfy.sh"
    topic = os.environ["NTFY_TOPIC"]
    url = f"{server.rstrip('/')}/{topic}"
    headers = {"Title": msg.title, "Priority": msg.priority, "Click": click_url, "Markdown": "no"}
    if msg.tags:
        headers["Tags"] = msg.tags
    if msg.attach and Path(msg.attach).exists():
        # With an attachment the body is the file; the text goes in the Message header.
        headers["Filename"] = Path(msg.attach).name
        headers["Message"] = msg.body.replace("\n", "\\n")
        data = Path(msg.attach).read_bytes()
    else:
        data = msg.body.encode("utf-8")
    r = requests.put(url, data=data, headers=headers, timeout=30)
    r.raise_for_status()
