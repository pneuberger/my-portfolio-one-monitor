"""PNG chart for notifications and the dashboard: index with 3y high/low, regime background,
active trigger levels; drawdown from the 3y high below."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .regime import LABEL, regime_path, trigger_levels  # noqa: E402

COLORS = {"normal": "#2e7d32", "crisis": "#ef6c00", "escalation": "#c62828"}


def render(df: pd.DataFrame, rules: dict, out: Path, years: int = 5) -> Path:
    path = regime_path(df, rules)
    d = path.df
    d["dd"] = d["close"] / d["high3y"] - 1.0
    view = d[d["date"] >= d["date"].max() - pd.DateOffset(years=years)].reset_index(drop=True)
    last = d.iloc[-1]
    regime = path.regime
    levels = trigger_levels(regime, last["high3y"], last["low3y"], path.anchor, rules)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]}, dpi=110)
    # Regime background
    runs = (view["regime"] != view["regime"].shift()).cumsum()
    for _, seg in view.groupby(runs):
        r = seg["regime"].iloc[0]
        if r != "normal":
            for ax in (ax1, ax2):
                ax.axvspan(seg["date"].iloc[0], seg["date"].iloc[-1], color=COLORS[r], alpha=0.12, lw=0)

    ax1.plot(view["date"], view["close"], color="#1565c0", lw=1.4, label="MSCI World (USD)")
    ax1.plot(view["date"], view["high3y"], color="#555", lw=0.9, ls="--", label="3y high")
    ax1.plot(view["date"], view["low3y"], color="#888", lw=0.9, ls=":", label="3y low")
    for lv in levels:
        ax1.axhline(lv["level"], color=COLORS[lv["to"]], lw=1.0, ls="-.",
                    label=f"{LABEL[lv['to']]} {lv['op']} {lv['level']:,.0f}")
    ax1.set_title(f"MSCI World - {last['date']:%Y-%m-%d}: {last['close']:,.0f}, "
                  f"{last['dd']:+.1%} vs 3y high - {LABEL[regime]}",
                  color=COLORS[regime], fontsize=11, loc="left")
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    ax1.grid(alpha=0.25)

    th = rules["normal_to_crisis_drop_from_3y_high"]
    lo = min(-0.5, view["dd"].min() - 0.05)
    ax2.axhline(-th, color=COLORS["crisis"], lw=0.9, ls=":")
    ax2.fill_between(view["date"], view["dd"], 0, color="#1565c0", alpha=0.35, lw=0)
    ax2.set_ylim(lo, 0.02)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax2.set_ylabel("vs 3y high")
    ax2.grid(alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, metadata={"Software": None})
    plt.close(fig)
    return out
