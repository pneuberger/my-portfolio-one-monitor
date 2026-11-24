"use strict";

const HOLDINGS_KEY = "regime-monitor.holdings.v1";
const PREFS_KEY = "regime-monitor.prefs.v1";
const MAX_POINTS = 1200;
const HIST_LIMIT = 25;

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode etc. */ }
  },
};

const $ = (id) => document.getElementById(id);
const fmt = (x, d = 0) => Number(x).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const eur = (x) => `€${fmt(Math.round(x))}`;
const pct = (x, d = 1) => `${fmt(x * 100, d)}%`;
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

let DATA = null;
let charts = {};
let prefs = store.get(PREFS_KEY, { range: 5, hist: "live" });

async function load() {
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    DATA = await res.json();
  } catch (e) {
    $("updated").textContent = "Could not load data.json";
    return;
  }
  renderRegime();
  renderCharts();
  renderHoldings();
  renderRefill();
  renderHistory();
}

// ---------- regime card ----------
function renderRegime() {
  const d = DATA;
  $("updated").textContent = d.close_date ? `Close ${d.close_date}` : "";
  if (!d.regime) return;
  document.documentElement.style.setProperty("--regime", `var(--${d.regime})`);
  $("regime-name").textContent = d.regime_label;
  $("dd").textContent = `−${pct(d.drawdown)}`;
  $("dd-sub").textContent = `below all-time high · since ${d.regime_since || "–"}`;
  $("close").textContent = fmt(d.close, 2);
  $("ath").innerHTML = `${fmt(d.ath, 2)}<br><span class="muted small-text">${d.ath_date}</span>`;
  $("lvl-crisis").textContent = fmt(d.levels.crisis, 0);
  $("lvl-esc").textContent = fmt(d.levels.escalation, 0);
  const t = d.allocations[d.regime];
  $("target").textContent = "Target: " + d.assets.map((a) => `${d.asset_labels[a]} ${t[a]}%`).join(" · ");
  if (d.failed_fetches > 0) {
    const w = $("data-warning");
    w.hidden = false;
    w.textContent = `Data warning: ${d.failed_fetches} run(s) without a new close. ${d.last_error || ""}`;
  }
}

// ---------- charts ----------
function sliceRange(years) {
  const { dates, closes } = DATA.series;
  // Running ATH over the full history, so drawdown is correct for any window.
  const ath = new Array(closes.length);
  let m = -Infinity;
  for (let i = 0; i < closes.length; i++) { m = Math.max(m, closes[i]); ath[i] = m; }
  let start = 0;
  if (years > 0) {
    const cut = new Date(dates[dates.length - 1]);
    cut.setFullYear(cut.getFullYear() - years);
    const iso = cut.toISOString().slice(0, 10);
    start = dates.findIndex((x) => x >= iso);
  }
  const n = dates.length - start;
  const step = Math.max(1, Math.ceil(n / MAX_POINTS));
  const out = { labels: [], close: [], ath: [], dd: [] };
  for (let i = start; i < dates.length; i += step) {
    // Keep the lowest close inside each bucket so crashes are not smoothed away.
    let j = i;
    for (let k = i; k < Math.min(i + step, dates.length); k++) if (closes[k] / ath[k] < closes[j] / ath[j]) j = k;
    out.labels.push(dates[j]); out.close.push(closes[j]); out.ath.push(ath[j]); out.dd.push(closes[j] / ath[j] - 1);
  }
  const last = dates.length - 1;
  if (out.labels[out.labels.length - 1] !== dates[last]) {
    out.labels.push(dates[last]); out.close.push(closes[last]); out.ath.push(ath[last]); out.dd.push(closes[last] / ath[last] - 1);
  }
  return out;
}

const bandsPlugin = {
  id: "bands",
  beforeDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.bands) return;
    const { ctx, chartArea: a, scales: { y } } = chart;
    ctx.save();
    for (const b of opts.bands) {
      const top = y.getPixelForValue(b.from), bottom = y.getPixelForValue(b.to);
      ctx.fillStyle = b.color;
      ctx.fillRect(a.left, Math.max(a.top, top), a.right - a.left, Math.min(a.bottom, bottom) - Math.max(a.top, top));
    }
    ctx.restore();
  },
};

function alpha(color, a) {
  const c = color.replace("#", "");
  const n = parseInt(c.length === 3 ? c.split("").map((x) => x + x).join("") : c, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function renderCharts() {
  if (!DATA.series || typeof Chart === "undefined") return;
  const r = sliceRange(prefs.range);
  const th = DATA.thresholds;
  const accent = cssVar("--accent"), muted = cssVar("--muted"), line = cssVar("--line");
  const cCrisis = cssVar("--crisis"), cEsc = cssVar("--escalation");
  Chart.defaults.color = muted;
  Chart.defaults.borderColor = line;
  Chart.defaults.font.family = "system-ui, sans-serif";

  const athNow = DATA.ath;
  const flat = (v) => r.labels.map(() => v);
  const common = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    elements: { point: { radius: 0, hitRadius: 6 }, line: { borderWidth: 1.5 } },
    scales: { x: { ticks: { maxTicksLimit: 5, maxRotation: 0, callback(v) { return this.getLabelForValue(v).slice(0, prefs.range === 1 ? 7 : 4); } }, grid: { display: false } } },
  };

  Object.values(charts).forEach((c) => c.destroy());
  charts.index = new Chart($("chart-index"), {
    type: "line",
    data: {
      labels: r.labels,
      datasets: [
        { label: "Close", data: r.close, borderColor: accent, backgroundColor: accent },
        { label: "ATH", data: r.ath, borderColor: muted, borderDash: [4, 3], borderWidth: 1 },
        { label: `−${th.crisis * 100}%`, data: flat(athNow * (1 - th.crisis)), borderColor: cCrisis, borderDash: [2, 3], borderWidth: 1 },
        { label: `−${th.escalation * 100}%`, data: flat(athNow * (1 - th.escalation)), borderColor: cEsc, borderDash: [6, 3], borderWidth: 1 },
      ],
    },
    options: { ...common, plugins: { legend: { labels: { boxWidth: 12, boxHeight: 2 } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, 0)}` } } } },
  });

  const minDd = Math.min(-0.5, Math.min(...r.dd) - 0.05);
  charts.dd = new Chart($("chart-dd"), {
    type: "line",
    data: { labels: r.labels, datasets: [{ label: "Drawdown", data: r.dd, borderColor: accent, backgroundColor: alpha(accent, 0.25), fill: "origin" }] },
    options: {
      ...common,
      scales: { ...common.scales, y: { min: minDd, max: 0.02, ticks: { callback: (v) => `${Math.round(v * 100)}%` } } },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => `Drawdown: ${pct(c.parsed.y)}` } },
        bands: { bands: [
          { from: -th.crisis, to: -th.escalation, color: alpha(cCrisis, 0.14) },
          { from: -th.escalation, to: minDd, color: alpha(cEsc, 0.14) },
        ] },
      },
    },
    plugins: [bandsPlugin],
  });

  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", +b.dataset.range === prefs.range));
}

// ---------- holdings ----------
function renderHoldings() {
  const form = $("holdings");
  const h = store.get(HOLDINGS_KEY, {});
  form.innerHTML = DATA.assets.map((a) => `
    <label>${DATA.asset_labels[a]}
      <input type="number" inputmode="decimal" min="0" step="any" name="${a}" value="${h[a] ?? ""}" placeholder="€">
    </label>`).join("") + `
    <label>Monthly contribution
      <input type="number" inputmode="decimal" min="0" step="any" name="_contribution" value="${h._contribution ?? ""}" placeholder="€">
    </label>`;
  form.oninput = () => {
    const v = {};
    for (const a of [...DATA.assets, "_contribution"]) { const x = parseFloat(form.elements[a].value); if (!isNaN(x)) v[a] = x; }
    store.set(HOLDINGS_KEY, v);
    renderAlloc(); renderRefill();
  };
  renderAlloc();
}

function holdings() {
  const h = store.get(HOLDINGS_KEY, {});
  const has = DATA.assets.some((a) => h[a] > 0);
  const total = DATA.assets.reduce((s, a) => s + (h[a] || 0), 0);
  return { h, has, total };
}

function renderAlloc() {
  const box = $("alloc");
  const { h, has, total } = holdings();
  if (!has || !DATA.regime) { box.innerHTML = `<p class="muted small-text">Enter your holdings to see current vs. target.</p>`; return; }
  const t = DATA.allocations[DATA.regime];
  const refillActive = DATA.refill && DATA.refill.active;
  let html = `<div class="kv"><span>Total</span><strong>${eur(total)}</strong></div>`;
  for (const a of DATA.assets) {
    const cur = (h[a] || 0) / total;
    const tgt = t[a] / 100;
    const trade = tgt * total - (h[a] || 0);
    let cls = Math.abs(trade) < 1 ? "" : trade > 0 ? "buy" : "sell";
    let txt = Math.abs(trade) < 1 ? "on target" : `${trade > 0 ? "buy" : "sell"} ${eur(Math.abs(trade))}`;
    if (refillActive && trade <= -1) { cls = ""; txt = `${eur(-trade)} over target (no sale during refill)`; }
    if (refillActive && trade >= 1 && !["bonds", "cash"].includes(a)) { cls = ""; txt = `${eur(trade)} under target`; }
    html += `<div class="alloc-row">
      <div class="alloc-top"><span class="name">${DATA.asset_labels[a]}</span><span>${pct(cur)} / ${t[a]}%</span></div>
      <div class="bar"><div class="fill" style="width:${Math.min(100, cur * 100)}%"></div><div class="mark" style="left:calc(${tgt * 100}% - 1px)"></div></div>
      <div class="trade ${cls}">${txt}</div></div>`;
  }
  if (refillActive) html += `<p class="muted small-text">Refill phase: rule is no sales — close the gap with contributions.</p>`;
  box.innerHTML = html;
}

// ---------- refill ----------
function monthsBetween(a, b) {
  return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth()) + (b.getDate() >= a.getDate() ? 0 : -1);
}

function renderRefill() {
  const box = $("refill");
  const r = DATA.refill;
  if (!r) { box.innerHTML = ""; return; }
  const c = store.get(HOLDINGS_KEY, {})._contribution || 0;
  const split = DATA.contribution_split_pct || {};
  const splitTxt = DATA.assets.filter((a) => split[a] > 0)
    .map((a) => `${DATA.asset_labels[a]} ${fmt(split[a], split[a] % 1 ? 1 : 0)}%${c ? ` (${eur(c * split[a] / 100)})` : ""}`).join(" · ");
  if (!r.active) {
    box.innerHTML = `<p>No refill phase active.</p>
      <p class="muted small-text">Monthly contribution (${DATA.normal_contribution_mode}): ${splitTxt}</p>`;
    return;
  }
  const { h, has, total } = holdings();
  let progress = r.progress, gap = null, monthsNeeded = r.months_left_estimate, source = "estimate from contributions";
  if (has) {
    const t = DATA.allocations[DATA.regime];
    gap = Math.max(0, ((t.bonds + t.cash) / 100) * total - ((h.bonds || 0) + (h.cash || 0)));
    const initialGap = (r.gap_pp / 100) * total;
    progress = initialGap > 0 ? Math.max(0, Math.min(1, 1 - gap / initialGap)) : 1;
    monthsNeeded = c > 0 ? Math.ceil(gap / c) : "– (enter contribution)";
    source = "from your holdings";
  }
  const cap = new Date(r.cap_date);
  const today = new Date(DATA.today);
  const monthsToCap = Math.max(0, monthsBetween(today, cap));
  const daysToCap = Math.max(0, Math.round((cap - today) / 86400000));
  box.innerHTML = `
    <div class="progress"><div style="width:${progress * 100}%"></div></div>
    <div class="kv"><span>Progress (${source})</span><strong>${pct(progress, 0)}</strong></div>
    <div class="kv"><span>Remaining gap</span><strong>${gap === null ? `${fmt(r.gap_pp)} pp at start – enter holdings for €` : eur(gap)}</strong></div>
    <div class="kv"><span>Months of contributions left</span><strong>${monthsNeeded}</strong></div>
    <div class="kv"><span>Started</span><strong>${r.started}</strong></div>
    <div class="kv"><span>Safety net (sell remainder)</span><strong>${r.cap_date} · ${monthsToCap} mo / ${daysToCap} d</strong></div>
    <p class="muted small-text">Each payday: ${splitTxt}. ${has && gap <= 0 ? "Your holdings show bonds and cash back at target." : ""}</p>`;
}

// ---------- history ----------
let histAll = false;
function renderHistory() {
  const ev = ((DATA.events || {})[prefs.hist] || []).slice().reverse();
  const list = $("history");
  const label = { normal: "Normal", crisis: "Crisis", escalation: "Escalation" };
  const shown = histAll ? ev : ev.slice(0, HIST_LIMIT);
  list.innerHTML = shown.length ? shown.map((e) => `
    <li><span>${e.date}</span>
      <span><span class="pill ${e.from}">${label[e.from]}</span> → <span class="pill ${e.to}">${label[e.to]}</span></span>
      <span class="muted">−${pct(e.drawdown)}</span></li>`).join("")
    : `<li class="muted">${prefs.hist === "live" ? "No live signals yet." : "No events."}</li>`;
  $("hist-more").hidden = histAll || ev.length <= HIST_LIMIT;
  $("hist-more").textContent = `Show all ${ev.length}`;
  document.querySelectorAll("#hist-src button").forEach((b) => b.classList.toggle("on", b.dataset.src === prefs.hist));
}

// ---------- wiring ----------
document.addEventListener("DOMContentLoaded", () => {
  $("range").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.range = +b.dataset.range; store.set(PREFS_KEY, prefs); renderCharts();
  });
  $("hist-src").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.hist = b.dataset.src; histAll = false; store.set(PREFS_KEY, prefs); renderHistory();
  });
  $("hist-more").addEventListener("click", () => { histAll = true; renderHistory(); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => DATA && renderCharts());
  load();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
});
