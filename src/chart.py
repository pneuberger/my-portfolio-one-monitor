"""PNG chart for notifications and the dashboard: index + ATH, drawdown with regime bands."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

COLORS = {"normal": "#2e7d32", "crisis": "#ef6c00", "escalation": "#c62828"}


def render(df: pd.DataFrame, thresholds: dict, regime: str, out: Path, years: int = 5) -> Path:
    df = df.copy()
    df["ath"] = df["close"].cummax()
    df["dd"] = df["close"] / df["ath"] - 1.0
    view = df[df["date"] >= df["date"].max() - pd.DateOffset(years=years)]
    last = df.iloc[-1]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]}, dpi=110)
    ax1.plot(view["date"], view["close"], color="#1565c0", lw=1.4, label="MSCI World (USD)")
    ax1.plot(view["date"], view["ath"], color="#555", lw=0.9, ls="--", label="ATH")
    for key, ls in (("crisis", ":"), ("escalation", "-.")):
        ax1.axhline(last["ath"] * (1 - thresholds[key]), color=COLORS[key], lw=0.9, ls=ls,
                    label=f"-{thresholds[key]:.0%}")
    ax1.set_title(f"MSCI World - {last['date']:%Y-%m-%d}: {last['close']:,.0f}, "
                  f"{last['dd']:.1%} vs ATH - {regime.capitalize()}",
                  color=COLORS[regime], fontsize=11, loc="left")
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    ax1.grid(alpha=0.25)

    lo = min(-0.5, view["dd"].min() - 0.05)
    ax2.axhspan(-thresholds["escalation"], -thresholds["crisis"], color=COLORS["crisis"], alpha=0.12)
    ax2.axhspan(lo, -thresholds["escalation"], color=COLORS["escalation"], alpha=0.12)
    ax2.fill_between(view["date"], view["dd"], 0, color="#1565c0", alpha=0.35, lw=0)
    ax2.set_ylim(lo, 0.02)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax2.set_ylabel("Drawdown")
    ax2.grid(alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, metadata={"Software": None})
    plt.close(fig)
    return out
