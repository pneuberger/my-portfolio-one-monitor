# My Portfolio One Monitor

Watches the **MSCI World USD price index** (`^990100-USD-STRD`) every trading day. It works out the portfolio regime with the rules from *Erfolgreich wissenschaftlich investieren*, and sends **ntfy push notifications** (with a chart) to an Android phone when an action is due under the configured rules. A **PWA dashboard** on GitHub Pages shows the regime, charts, allocation vs. target and refill progress.

Runs entirely on **GitHub Actions + GitHub Pages + ntfy.sh**. There is no server, database or cost.

| Regime | ACWI | World Small Cap | EUR infl.-linked bonds | Cash |
|---|---|---|---|---|
| Normal (A) | 70% | 10% | 10% | 10% |
| Crisis (B) | 78% | 12% | 5% | 5% |
| Escalation (C) | 85% | 15% | 0% | 0% |

### Regime changes

From *Erfolgreich wissenschaftlich investieren* (2nd ed., p. 46, "Steuerung der Regimes in Krisen (vereinfacht)"):

| Change | Condition (MSCI World daily close) |
|---|---|
| Normal → Crisis | more than **20% below the 3-year high** |
| Crisis → Escalation | more than **25% below the Crisis start level** |
| Escalation → Crisis | more than **50% above the 3-year low** |
| Crisis → Normal | more than **25% above the Crisis start level** |

- The **Crisis start level** (the "anchor") is the close on the day Crisis was entered, from Normal or from Escalation. Normal → Crisis → Escalation therefore means about −40% from the 3-year high (0.80 × 0.75). Escalation → Crisis → Normal means about +87.5% from the 3-year low (1.5 × 1.25).
- The 3-year window is rolling: the last 1,096 calendar days including today.
- All conditions are strict ("more than"), as in the book.
- The regime is **path-dependent**. It's computed by replaying the full history since 1972 every day, so the live check and the backtest use the same engine. Since 1972 this gives 26 regime changes.
- The book also names qualitative signals (volatility jumps, corporate-bond risk premiums). These are **not automated**. Only the price conditions are.
- The allocations are my own, not the book's 80/20 / 90/10 / 100/0. The refill phase (below) is also my variant. The app only automates these rules. It gives no investment advice.

## How it works

- **Daily close only.** Intraday moves don't count.
- **Every regime change alerts.** There is no buffer and no confirmation days. Today's regime is compared with the **last notified** regime in `data/state.json`, so missed days and skipped regimes (Normal ↔ Escalation) produce exactly one alert.
- **First run:** sends "monitoring started" with the current regime, target and the next trigger levels. If the rule engine changes (`RULES_VERSION` in `src/check.py`), the next run sends "monitoring restarted – rules changed" instead of a crossing alert.
- **Downward crossing:** buy equities using bonds and cash. The alert lists the pp shifts and € amounts. The € figures are based on the `PORTFOLIO_VALUE_EUR` secret, which you update now and then.
- **Upward crossing:** no sales. A **refill phase** starts: 100% of each monthly contribution goes to bonds and cash (50/50) until they are back at target.
  - On payday you get a reminder: the first run on or after `payday_day`.
  - When the estimated gap is filled, you get "refill complete".
  - If it isn't filled after 12 months, you get "sell the remainder".
  - A second upward crossing adds to the open refill and keeps the original start date. A downward crossing cancels the refill.
- **Data problems:** if 2 runs in a row bring no new close or hit a fetch error, you get an alert, repeated every 5 failed runs after that. The app never switches to another series.
- **Privacy:** the repo is public (free Pages) and contains only index data and rules. Portfolio value and contribution are **GitHub secrets**: they appear only in notifications, never in committed files. Refill state is stored in percentage points and paid months. Holdings and the contribution amount are entered in the dashboard and stored **only in the phone's localStorage**.

| Notification | Trigger | Priority |
|---|---|---|
| Monitoring started | first run | default |
| Regime falls – buy equities | downward crossing | high + chart |
| Regime rises – refill starts | upward crossing | high + chart |
| Contribution reminder | payday during refill | default |
| Refill complete | estimated gap filled | low |
| Sell the remainder | refill open after 12 months | high |
| Data problem | 2 failed runs, then every 5 | high |
| Weekly summary | Saturday, if `weekly_summary: true` | low |

## Setup

1. **Repo:** push this folder to a **public** GitHub repo.
2. **Pages:** go to *Settings → Pages → Build and deployment*, choose *Deploy from a branch*, then `main` and `/docs`. The dashboard appears at `https://<user>.github.io/<repo>/`. Put that URL into `config.json` → `dashboard_url`.
3. **ntfy topic:** pick a long random topic name, since anyone who knows it can read your pushes:
   ```sh
   openssl rand -hex 16
   ```
   Then add these under *Settings → Secrets and variables → Actions → New repository secret*:
   - `NTFY_TOPIC`: the random topic
   - `PORTFOLIO_VALUE_EUR`: your portfolio value in € (number only), used for the € amounts in alerts and the refill gap estimate. Update it now and then.
   - `MONTHLY_CONTRIBUTION_EUR`: your monthly contribution in € (number only)
   - optionally `NTFY_SERVER` for a self-hosted server (default `https://ntfy.sh`)

   With the CLI: `gh secret set PORTFOLIO_VALUE_EUR --body <value>`.
4. **Phone:** install **ntfy** from Google Play or F-Droid, tap **+**, and subscribe to the same topic. For a reliable delivery, allow ntfy to ignore battery optimisation.
5. **Test:** go to *Actions → Test notification → Run workflow*. A push should arrive within seconds.
6. **First check:** go to *Actions → Daily check → Run workflow*. You should get "monitoring started", and the workflow commits `data/state.json`. After that it runs by itself Tue–Sat at 06:15 UTC (07:15 Berlin in winter, 08:15 in summer).
7. **Install the dashboard:** open the Pages URL in Chrome on Android, then choose *⋮ → Add to Home screen / Install app*. Enter your holdings and monthly contribution there.

## Configuration (`config.json`)

| Key | Meaning |
|---|---|
| `thresholds` | `crisis` 0.20, `escalation` 0.40 (drawdown fractions) |
| `allocations` | target % per regime |
| `normal_contribution_mode` | `by_target` (70/10/10/10) or `all_equity` (outside refill) |
| `payday_day` | day of month for the contribution reminder |
| `refill_cap_months` | safety net, default 12 |
| `data_alert_after_failures` / `data_alert_repeat_every` | data-problem alert cadence |
| `weekly_summary` | `true` for a Saturday summary |
| `dashboard_url` | `Click` link in every notification |

Private values are **not** in `config.json`. They come from the env vars `PORTFOLIO_VALUE_EUR` and `MONTHLY_CONTRIBUTION_EUR` (GitHub secrets), or locally from a gitignored `config.local.json`:

```json
{ "portfolio_value_eur": 40000, "monthly_contribution_eur": 800 }
```

## Local use

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m src.check --dry-run            # prints messages instead of sending
.venv/bin/python -m src.check --dry-run --history-csv my.csv --state /tmp/state.json --today 2026-12-01
cd docs && python3 -m http.server 8000            # dashboard at http://localhost:8000
```

`--dry-run` still writes `data/`, `docs/data.json` and `docs/chart.png`. Use `--state` and `--docs` pointing at a temp folder to keep the repo clean.

## Layout

```
src/check.py      entry point: fetch → classify → refill → notify → write state + dashboard data
src/data.py       Yahoo fetch with retries, merge into data/history.csv, drop today's incomplete bar
src/regime.py     regime state machine (3y high/low, Crisis anchor), trigger levels, transition deltas
src/refill.py     refill phase: payday reminders, completion, 12-month cap
src/notify.py     ntfy messages (Title/Priority/Tags/Click, PNG attachment)
src/chart.py      notification/dashboard PNG
src/backtest.py   regime events since 1972 (same engine) for the dashboard
docs/             PWA (Chart.js from cdnjs), data.json + chart.png written daily
```

## Notes

- GitHub pauses scheduled workflows in repos with no activity for 60 days. The daily data commits keep this repo active.
- Yahoo sometimes rate-limits GitHub runners. The fetch retries 3 times and raises the data-problem alert if it still fails.
- If sending a notification fails, the run exits non-zero and `state.json` is **not** advanced, so the alert is retried on the next run.
