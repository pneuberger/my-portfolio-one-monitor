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
const LABEL = { normal: "Normal", crisis: "Crisis", escalation: "Escalation" };

function renderRegime() {
  const d = DATA;
  $("updated").textContent = d.close_date ? `Close ${d.close_date}` : "";
  if (!d.regime) return;
  document.documentElement.style.setProperty("--regime", `var(--${d.regime})`);
  $("regime-name").textContent = d.regime_label;
  $("dd").textContent = `${d.drawdown > 0 ? "−" : ""}${pct(d.drawdown)}`;
  $("dd-sub").textContent = `vs 3-year high · ${d.regime_label} since ${d.regime_since || "–"}`;
  $("close").textContent = fmt(d.close, 2);
  $("high3y").innerHTML = `${fmt(d.high3y, 2)}<br><span class="muted small-text">${d.high3y_date}</span>`;
  $("low3y").innerHTML = `${fmt(d.low3y, 2)} <span class="muted small-text">(+${pct(d.rise)})</span><br><span class="muted small-text">${d.low3y_date}</span>`;
  const anchorBox = $("anchor-box");
  anchorBox.hidden = !d.anchor;
  if (d.anchor) $("anchor").innerHTML = `${fmt(d.anchor, 2)} <span class="muted small-text">(${d.close >= d.anchor ? "+" : "−"}${pct(Math.abs(d.close / d.anchor - 1))})</span>`;
  $("triggers").innerHTML = `<div class="label">Next regime change</div>` + d.levels.map((lv) => {
    const dist = lv.level / d.close - 1;
    return `<div class="kv"><span><span class="pill ${lv.to}">${LABEL[lv.to]}</span> if close ${lv.op} <strong>${fmt(lv.level, 0)}</strong></span>
      <span class="muted">${dist >= 0 ? "+" : "−"}${pct(Math.abs(dist))} away</span></div>
      <div class="muted small-text trig-basis">${lv.pct > 0 ? "+" : "−"}${fmt(Math.abs(lv.pct) * 100)}% vs ${lv.basis} ${fmt(lv.basis_level, 0)}</div>`;
  }).join("");
  const t = d.allocations[d.regime];
  $("target").textContent = "Target: " + d.assets.map((a) => `${d.asset_labels[a]} ${t[a]}%`).join(" · ");
  if (d.failed_fetches > 0) {
    const w = $("data-warning");
    w.hidden = false;
    w.textContent = `Data warning: ${d.failed_fetches} run(s) without a new close. ${d.last_error || ""}`;
  }
}

// ---------- charts ----------
// Rolling window (t - days, t] with monotonic deques, same as pandas rolling("1096D").
function rollingExtremes(dates, closes, days) {
  const n = closes.length, hi = new Array(n), lo = new Array(n);
  const t = dates.map((x) => Date.parse(x) / 86400000);
  const qh = [], ql = [];
  let hh = 0, hl = 0;
  for (let i = 0; i < n; i++) {
    while (qh.length > hh && closes[qh[qh.length - 1]] <= closes[i]) qh.pop();
    qh.push(i);
    while (t[qh[hh]] <= t[i] - days) hh++;
    while (ql.length > hl && closes[ql[ql.length - 1]] >= closes[i]) ql.pop();
    ql.push(i);
    while (t[ql[hl]] <= t[i] - days) hl++;
    hi[i] = closes[qh[hh]];
    lo[i] = closes[ql[hl]];
  }
  return { hi, lo };
}

let ROLL = null;
function regimeArray() {
  const n = DATA.series.closes.length, out = new Array(n);
  const seg = DATA.regime_segments || [[0, DATA.regime, null]];
  for (let s = 0; s < seg.length; s++) {
    const end = s + 1 < seg.length ? seg[s + 1][0] : n;
    for (let i = seg[s][0]; i < end; i++) out[i] = seg[s][1];
  }
  return out;
}

function sliceRange(years) {
  const { dates, closes } = DATA.series;
  if (!ROLL) {
    ROLL = rollingExtremes(dates, closes, Math.round(365.25 * DATA.rules.window_years));
    ROLL.regime = regimeArray();
  }
  const { hi, lo, regime } = ROLL;
  let start = 0;
  if (years > 0) {
    const cut = new Date(dates[dates.length - 1]);
    cut.setFullYear(cut.getFullYear() - years);
    const iso = cut.toISOString().slice(0, 10);
    start = dates.findIndex((x) => x >= iso);
  }
  const n = dates.length - start;
  const step = Math.max(1, Math.ceil(n / MAX_POINTS));
  const out = { labels: [], close: [], hi: [], lo: [], dd: [], regime: [] };
  const push = (j) => {
    out.labels.push(dates[j]); out.close.push(closes[j]); out.hi.push(hi[j]); out.lo.push(lo[j]);
    out.dd.push(closes[j] / hi[j] - 1); out.regime.push(regime[j]);
  };
  for (let i = start; i < dates.length; i += step) {
    // Keep the deepest point inside each bucket so crashes are not smoothed away.
    let j = i;
    for (let k = i; k < Math.min(i + step, dates.length); k++) if (closes[k] / hi[k] < closes[j] / hi[j]) j = k;
    push(j);
  }
  if (out.labels[out.labels.length - 1] !== dates[dates.length - 1]) push(dates.length - 1);
  return out;
}

// Shades Crisis/Escalation periods behind the lines.
const regimeBgPlugin = {
  id: "regimeBg",
  beforeDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.regime) return;
    const { ctx, chartArea: a, scales: { x } } = chart;
    const reg = opts.regime, n = reg.length;
    const half = n > 1 ? (x.getPixelForValue(1) - x.getPixelForValue(0)) / 2 : 0;
    ctx.save();
    let i = 0;
    while (i < n) {
      let j = i;
      while (j + 1 < n && reg[j + 1] === reg[i]) j++;
      if (reg[i] !== "normal" && opts.colors[reg[i]]) {
        const x0 = Math.max(a.left, x.getPixelForValue(i) - half), x1 = Math.min(a.right, x.getPixelForValue(j) + half);
        ctx.fillStyle = opts.colors[reg[i]];
        ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
      }
      i = j + 1;
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
  const accent = cssVar("--accent"), muted = cssVar("--muted"), line = cssVar("--line");
  const colors = { crisis: cssVar("--crisis"), escalation: cssVar("--escalation"), normal: cssVar("--normal") };
  const bg = { regime: r.regime, colors: { crisis: alpha(colors.crisis, 0.16), escalation: alpha(colors.escalation, 0.18) } };
  Chart.defaults.color = muted;
  Chart.defaults.borderColor = line;
  Chart.defaults.font.family = "system-ui, sans-serif";

  const flat = (v) => r.labels.map(() => v);
  const common = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    elements: { point: { radius: 0, hitRadius: 6 }, line: { borderWidth: 1.5 } },
    scales: { x: { ticks: { maxTicksLimit: 5, maxRotation: 0, callback(v) { return this.getLabelForValue(v).slice(0, prefs.range === 1 ? 7 : 4); } }, grid: { display: false } } },
  };

  Object.values(charts).forEach((c) => c.destroy());
  const triggerSets = (DATA.levels || []).map((lv) => ({
    label: `${LABEL[lv.to]} ${lv.op === "below" ? "<" : ">"} ${fmt(lv.level, 0)}`, data: flat(lv.level),
    borderColor: colors[lv.to], borderDash: [6, 3], borderWidth: 1.2,
  }));
  charts.index = new Chart($("chart-index"), {
    type: "line",
    data: {
      labels: r.labels,
      datasets: [
        { label: "Close", data: r.close, borderColor: accent, backgroundColor: accent },
        { label: "3y high", data: r.hi, borderColor: muted, borderDash: [4, 3], borderWidth: 1 },
        { label: "3y low", data: r.lo, borderColor: muted, borderDash: [1, 3], borderWidth: 1 },
        ...triggerSets,
      ],
    },
    options: { ...common, plugins: { legend: { labels: { boxWidth: 12, boxHeight: 2 } }, regimeBg: bg,
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, 0)}` } } } },
    plugins: [regimeBgPlugin],
  });

  const th = DATA.rules.normal_to_crisis_drop_from_3y_high;
  const minDd = Math.min(-0.5, Math.min(...r.dd) - 0.05);
  charts.dd = new Chart($("chart-dd"), {
    type: "line",
    data: { labels: r.labels, datasets: [
      { label: "vs 3y high", data: r.dd, borderColor: accent, backgroundColor: alpha(accent, 0.25), fill: "origin" },
      { label: `−${th * 100}%`, data: flat(-th), borderColor: colors.crisis, borderDash: [2, 3], borderWidth: 1 },
    ] },
    options: {
      ...common,
      scales: { ...common.scales, y: { min: minDd, max: 0.02, ticks: { callback: (v) => `${Math.round(v * 100)}%` } } },
      plugins: { legend: { display: false }, regimeBg: bg,
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${pct(c.parsed.y)}` } } },
    },
    plugins: [regimeBgPlugin],
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
  const shown = histAll ? ev : ev.slice(0, HIST_LIMIT);
  list.innerHTML = shown.length ? shown.map((e) => `
    <li><div class="hist-top"><span>${e.date}</span>
      <span><span class="pill ${e.from}">${LABEL[e.from]}</span> → <span class="pill ${e.to}">${LABEL[e.to]}</span></span></div>
      ${e.trigger ? `<div class="muted small-text">${e.trigger}</div>` : ""}</li>`).join("")
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
