"use strict";

// Build DOM nodes without innerHTML so statement text can never inject markup.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const Fmt = window.PortfolioTools.fmt;
const signClass = (n) => (n > 0 ? "gain" : n < 0 ? "loss" : "");
const money = Fmt.money;
const price = Fmt.price;
const pct = Fmt.percent;
const day = Fmt.day;
const qtyFmt = { format: Fmt.quantity };
const ISSUERS = { CAMS: "CAMS", KFINTECH: "KFintech", NSDL: "NSDL", CDSL: "CDSL" };
const ASSET_TYPES = { equity: "Stock", fund: "ETF/Fund", bond: "Bond" };

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "X-Requested-With": "portfolio", ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function showAlerts(messages) {
  document.getElementById("alerts").replaceChildren(...messages.map((m) => el("div", { class: "alert" }, m)));
}

// A load failure says what to try, and offers to try again.
function showLoadError(err) {
  document.getElementById("alerts").replaceChildren(el("div", { class: "alert" },
    `Could not load your portfolio: ${err.message}. If the server was just updated or restarted, try again; ` +
    "if it keeps failing, check that the server is still running.",
    el("button", { type: "button", class: "btn", onclick: () => load() }, "Try again")));
}

// A summary row that expands to show its per-folio or per-account rows.
function expandable(mainCells, subRows) {
  const toggle = (row) => {
    const open = row.getAttribute("aria-expanded") !== "true";
    row.setAttribute("aria-expanded", String(open));
    subRows.forEach((r) => { r.hidden = !open; });
  };
  const main = el("tr", {
    class: "holding", tabindex: "0", "aria-expanded": "false",
    onclick: (e) => { if (!e.target.closest("a")) toggle(e.currentTarget); },
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(e.currentTarget); } },
  }, mainCells);
  subRows.forEach((r) => { r.hidden = true; r.classList.add("lot"); });
  return [main, ...subRows];
}

const SVG_NS = "http://www.w3.org/2000/svg";

// SVG counterpart of el(): attributes only, never markup strings.
function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// ---- dashboard ----------------------------------------------------------------

function tileEl(label, value, sub, cls) {
  return el("div", { class: "tile" },
    el("div", { class: "label" }, label),
    el("div", { class: `value ${cls || ""}` }, value),
    sub ? el("div", { class: `sub ${cls || ""}` }, sub) : null);
}

function renderTotals(p) {
  const t = p.totals;
  const tile = tileEl;
  const a = p.as_of;
  const range = !a ? null : a.from === a.to ? `as of ${day(a.to)}` : `${day(a.from)} to ${day(a.to)}`;
  const mfDate = p.statements.mf ? `as of ${day(p.statements.mf.period_to)}` : null;
  const tiles = [tile("Total value", money(t.value), range)];
  if (p.statements.demat) {
    tiles.push(tile("Demat holdings", money(t.demat_value), `as of ${day(p.statements.demat.period_to)}`));
  }
  if (p.statements.mf) {
    tiles.push(tile("Mutual fund folios", money(t.mf_value), `as of ${day(p.statements.mf.period_to)}`));
    if (t.mf_cost !== null) {
      tiles.push(tile("Mutual funds invested", money(t.mf_cost), mfDate));
      tiles.push(tile("Mutual fund gain", money(t.mf_gain), `${pct(t.mf_gain_pct)}, ${mfDate}`, signClass(t.mf_gain)));
    }
  }
  for (const [label, m] of [["Demat", t.demat_latest], ["Funds", t.mf_latest]]) {
    if (!m) continue;
    tiles.push(tile(`${label} at latest price`, money(m.value), `${m.priced} of ${m.total}, as of ${day(m.as_of)}`));
    tiles.push(tile(`${label}: change since statement`, money(m.change), `${pct(m.change_pct)}, latest price as of ${day(m.as_of)}`, signClass(m.change)));
  }
  document.getElementById("totals").replaceChildren(...tiles);
}

let lastHistory = null;

function renderHistory(points) {
  document.getElementById("history-section").hidden = points.length < 2;  // one point is not a trend
  if (points.length < 2) return;
  lastHistory = points;
  // Drawn at the width it is shown at, so the axis text keeps its size on a phone instead of shrinking with the picture.
  const W = Math.max(300, Math.min(720, document.getElementById("history-chart").clientWidth || 720));
  const H = 240, L = 78, R = 16, T = 16, B = 30;
  const times = points.map((p) => new Date(`${p.date}T00:00:00`).getTime());
  const lo = Math.min(...points.map((p) => p.value)), hi = Math.max(...points.map((p) => p.value));
  const pad = (hi - lo) * 0.1 || hi * 0.1 || 1;
  const yMin = Math.max(0, lo - pad), yMax = hi + pad;
  const x = (t) => L + ((t - times[0]) / (times[times.length - 1] - times[0] || 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);
  const label = (text, attrs) => svgEl("text", attrs, text);
  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Portfolio value at each statement, from ${day(points[0].date)} to ${day(points[points.length - 1].date)}`,
  },
  [yMin, yMax].map((v) => svgEl("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) })),
  label(money(yMax), { x: L - 8, y: y(yMax) + 4, "text-anchor": "end" }),
  label(money(yMin), { x: L - 8, y: y(yMin) + 4, "text-anchor": "end" }),
  label(day(points[0].date), { x: L, y: H - 8, "text-anchor": "start" }),
  label(day(points[points.length - 1].date), { x: W - R, y: H - 8, "text-anchor": "end" }),
  svgEl("polyline", { class: "line", points: points.map((p, i) => `${x(times[i])},${y(p.value)}`).join(" ") }),
  points.map((p, i) => svgEl("circle", { class: p.mixed ? "dot mixed" : "dot", cx: x(times[i]), cy: y(p.value), r: 4 },
    svgEl("title", {}, `${day(p.date)}: ${money(p.value)}`))));
  document.getElementById("history-chart").replaceChildren(svg);

  document.querySelector("#history tbody").replaceChildren(...[...points].reverse().map((p) => el("tr", null,
    el("td", null, day(p.date)),
    el("td", { class: "num", "data-label": "Total" }, money(p.value)),
    el("td", { class: "num", "data-label": "Demat" }, money(p.demat_value)),
    el("td", { class: "num", "data-label": "Mutual funds" }, money(p.mf_value)),
    el("td", { class: p.mixed ? "loss" : "subtle", "data-label": "Note" },
      p.mixed ? `Stocks and funds are ${p.spread_days} days apart` : p.mf_source ? `Funds from ${ISSUERS[p.mf_source]}` : null))));
}

// ---- data quality: see confidence.js ----------------------------------------------

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function checkItem([ok, text, detail]) {
  return el("li", { class: ok ? "ok" : "warn" },
    el("span", { class: "mark", "aria-hidden": "true" }, ok ? "✓" : "!"),
    el("div", null,
      el("span", { class: "sr-only" }, ok ? "OK: " : "Needs attention: "), text,
      detail ? el("div", { class: "detail" }, detail) : null));
}

// The Overview's short version of the comparison: the two latest statements, the biggest movers, a link for the rest.
let lastOverviewKey = null;
async function renderOverviewChange(points) {
  const section = document.getElementById("prev-section");
  section.hidden = points.length < 2;
  if (points.length < 2) return;
  const key = JSON.stringify(points.map((pt) => [pt.date, pt.value]));
  if (key === lastOverviewKey) return;
  try {
    const r = await api("/api/changes");
    const total = r.demat.totals.change + r.funds.totals.change;
    document.getElementById("prev-summary").replaceChildren(
      `${day(r.from)} to ${day(r.to)}: your holdings moved by `, el("strong", { class: signClass(total) }, money(total)),
      `. Buying and selling account for ${money(r.demat.totals.quantity_effect + r.funds.totals.quantity_effect)}, ` +
      `price moves for ${money(r.demat.totals.price_effect + r.funds.totals.price_effect)}.`);
    const rows = [...r.demat.changes, ...r.funds.changes]
      .map((c) => ({ ...c, change: c.value_after - c.value_before }))
      .sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 5);
    document.getElementById("prev-list").replaceChildren(...rows.map((c) => el("li", null,
      el("span", null, c.name, el("span", { class: "subtle" }, ` · ${CHANGE_KINDS[c.kind] || c.kind}`)),
      el("span", { class: `num ${signClass(c.change)}` }, money(c.change)))));
    lastOverviewKey = key;  // only a success is remembered
  } catch (err) {
    document.getElementById("prev-summary").textContent = `Could not compare: ${err.message}`;
    document.getElementById("prev-list").replaceChildren();
  }
}

// ---- changes between statements ---------------------------------------------------

const CHANGE_KINDS = { new: "New", exited: "Sold out", more: "Bought more", less: "Sold some", price: "Price only" };

function setupChanges(points) {
  document.getElementById("changes-section").hidden = points.length < 2;
  if (points.length < 2) return;
  const from = document.getElementById("chg-from"), to = document.getElementById("chg-to");
  // The dates the user chose last time, if both are still statement dates; otherwise the two latest.
  const stored = prefs.get("compare", null);
  const known = (d) => typeof d === "string" && points.some((pt) => pt.date === d);
  const keep = from.value || to.value ? [from.value, to.value] : stored && known(stored.from) && known(stored.to) ? [stored.from, stored.to] : ["", ""];
  const fill = (select, wanted) => {
    select.replaceChildren(...points.map((pt) => el("option", { value: pt.date }, day(pt.date))));
    select.value = points.some((pt) => pt.date === wanted) ? wanted : "";
  };
  fill(from, keep[0] || points[points.length - 2].date);
  fill(to, keep[1] || points[points.length - 1].date);
  if (!from.value) from.value = points[points.length - 2].date;
  if (!to.value) to.value = points[points.length - 1].date;
  // The comparison depends only on the statements, so skip the request when nothing relevant changed
  // (a price refresh or an unrelated reload re-renders the page but leaves the statements alone).
  const key = JSON.stringify([points.map((pt) => [pt.date, pt.value]), from.value, to.value]);
  if (key !== lastChangesKey) loadChanges();
}

let lastChangesKey = null;

async function loadChanges() {
  const from = document.getElementById("chg-from").value, to = document.getElementById("chg-to").value;
  const tbody = document.querySelector("#changes tbody");
  if (from >= to) {
    document.getElementById("changes-summary").textContent = "Choose an earlier date on the left and a later one on the right.";
    document.getElementById("changes-warnings").replaceChildren();
    tbody.replaceChildren();
    return;
  }
  try {
    renderChanges(await api(`/api/changes?from_date=${from}&to_date=${to}`));
    lastChangesKey = JSON.stringify([lastHistory.map((pt) => [pt.date, pt.value]), from, to]);  // only a success is remembered
  } catch (err) {
    document.getElementById("changes-summary").textContent = `Could not compare: ${err.message}`;
  }
}

function renderChanges(r) {
  const t = r.demat.totals, f = r.funds.totals;
  const change = t.change + f.change;
  const qty = t.quantity_effect + f.quantity_effect, prc = t.price_effect + f.price_effect;
  document.getElementById("changes-summary").replaceChildren(
    `${day(r.from)} to ${day(r.to)}: your holdings moved by `,
    el("strong", { class: signClass(change) }, money(change)),
    `. Buying and selling account for ${money(qty)}, price moves for ${money(prc)}.`);
  document.getElementById("changes-warnings").replaceChildren(...r.warnings.map((w) => el("div", { class: "alert" }, w)));
  const rows = [
    ...r.demat.changes.map((c) => ({ ...c, group: "Demat" })),
    ...r.funds.changes.map((c) => ({ ...c, group: "Mutual fund folio" })),
  ];
  const body = rows.map((c) => el("tr", null,
    el("td", null, el("div", { class: "symbol" }, c.name),
      el("div", { class: "subtle" }, [c.group, c.isin].filter(Boolean).join(" · "))),
    el("td", { "data-label": "Change" }, CHANGE_KINDS[c.kind] || c.kind),
    el("td", { class: "num", "data-label": "Quantity" }, `${qtyFmt.format(c.quantity_before)} → ${qtyFmt.format(c.quantity_after)}`),
    el("td", { class: "num", "data-label": "Value before" }, money(c.value_before)),
    el("td", { class: "num", "data-label": "Value after" }, money(c.value_after)),
    el("td", { class: `num ${signClass(c.quantity_effect)}`, "data-label": "Buying or selling" }, money(c.quantity_effect)),
    el("td", { class: `num ${signClass(c.price_effect)}`, "data-label": "Price moves" }, money(c.price_effect))));
  if (body.length === 0) body.push(el("tr", null, el("td", { colspan: "7", class: "empty" }, "No holding changed between these dates.")));
  document.querySelector("#changes tbody").replaceChildren(...body);
}

// Allocation. Colour follows the entity, never its rank, so a filter or a new statement never repaints it.
// Only the four asset classes get categorical colours (fixed order); every other group is one colour.
const ASSET_SLOT = { "Stocks": 1, "Mutual funds (folios)": 2, "ETFs & demat funds": 3, "Bonds & SGBs": 4 };
const slotOf = (label) => ASSET_SLOT[label] || 0;

function allocationCard(title, rows, stacked) {
  const tip = (r) => `${r.label}: ${money(r.value)} (${r.weight.toFixed(1)}%)`;
  // The stack and its legend share one order (slot order), so they read the same way.
  const ordered = stacked ? [...rows].sort((a, b) => slotOf(a.label) - slotOf(b.label)) : rows;
  const parts = [el("div", { class: "alloc-head" }, title)];
  if (stacked) {
    parts.push(el("div", { class: "stack", "aria-hidden": "true" }, ordered.map((r) => {
      const seg = el("div", { class: `seg s${slotOf(r.label)}`, title: tip(r) }, el("span", null, `${r.weight.toFixed(1)}%`));
      seg.style.flexGrow = String(r.weight);  // CSSOM, not a style attribute: the CSP forbids those
      return seg;
    })));
  }
  // Bars compare rows with each other, so they are scaled to the biggest one. A single row is not a
  // comparison: it stays a plain line (the number is the chart).
  const biggest = Math.max(...rows.map((r) => r.value));
  const withBars = !stacked && rows.length > 1;
  parts.push(el("div", { class: "alloc-rows" },
    el("table", null,
      el("caption", { class: "sr-only" }, title),
      el("tbody", null, ordered.map((r) => {
        let bar = null;
        if (withBars) {
          bar = el("div", { class: "bar", title: tip(r) });
          bar.style.width = `${Math.max((r.value / biggest) * 100, 1.5)}%`;
        }
        return el("tr", null,
          el("td", { class: "label" },
            stacked ? el("span", { class: `swatch s${slotOf(r.label)}`, "aria-hidden": "true" }) : null, r.label, bar),
          el("td", { class: "num" }, money(r.value)),
          el("td", { class: "num" }, `${r.weight.toFixed(1)}%`));
      })))));
  return el("div", { class: "table-wrap alloc-card" }, parts);
}

function renderAllocation(p) {
  const a = p.allocation;
  const groups = [
    ["By asset class", a.asset_class.length > 1 ? a.asset_class : [], true],  // one class at 100% says nothing
    ["Stocks by broker", a.broker, false],
    ["Mutual funds by AMC", a.amc, false],
    ["Mutual funds by type", a.fund_type, false],
  ].filter(([, rows]) => rows.length > 0);
  document.getElementById("alloc-section").hidden = groups.length === 0;
  document.getElementById("allocation").replaceChildren(...groups.map(([title, rows, stacked]) => allocationCard(title, rows, stacked)));
  // A label goes inside a segment only if it fits with padding; otherwise the legend and tooltip carry the value.
  for (const seg of document.querySelectorAll("#allocation .seg")) {
    const label = seg.firstElementChild;
    if (label && label.scrollWidth + 10 > seg.clientWidth) label.remove();
  }
}

// ---- concentration -------------------------------------------------------------------

function renderConcentration(p) {
  const c = p.concentration;
  const section = document.getElementById("conc-section");
  section.hidden = !c || c.positions < 3;  // with one or two positions there is nothing to be concentrated
  if (section.hidden) return;
  const pc = (n) => `${n.toFixed(1)}%`;
  const a = p.as_of;
  const concAsOf = !a ? "" : a.from === a.to ? ` as of ${day(a.to)}` : ` as of ${day(a.from)} to ${day(a.to)}`;
  document.getElementById("conc-note").textContent =
    `Worked out from statement values${concAsOf}. ` +
    "A position is one ISIN, and a mutual fund or ETF counts as one position " +
    "whatever it holds inside. Effective holdings means your money behaves as if it were spread evenly over that " +
    "many positions: the lower it is compared with the real count, the more you rely on a few of them. " +
    "Only single positions are flagged. A fund house is not a single point of failure the way a company is, " +
    "because a fund's assets are held separately from its manager's own business.";
  document.getElementById("conc-tiles").replaceChildren(
    tileEl("Largest position", pc(c.largest.weight), c.largest.label),
    tileEl("Top 5 positions", pc(c.top5)),
    tileEl("Top 10 positions", pc(c.top10)),
    tileEl("Effective holdings", c.effective_holdings.toFixed(1), `of ${c.positions} positions`));
  const over = c.above_threshold;
  const checks = over.length === 0
    ? [[true, `No single position is above ${c.alert_pct}% of your portfolio.`]]
    : [[false, `${over.length} position${over.length === 1 ? " is" : "s are"} above ${c.alert_pct}% of your portfolio.`,
        over.map((r) => `${r.label} ${pc(r.weight)}`).join(" · ")]];
  document.getElementById("conc-checks").replaceChildren(...checks.map(([ok, text, detail]) =>
    el("li", { class: ok ? "ok" : "warn" },
      el("span", { class: "mark", "aria-hidden": "true" }, ok ? "✓" : "!"),
      el("div", null,
        el("span", { class: "sr-only" }, ok ? "OK: " : "Needs attention: "), text,
        detail ? el("div", { class: "detail" }, detail) : null))));
  document.getElementById("conc-cards").replaceChildren(
    allocationCard(`Top ${c.top_positions.length} positions`, c.top_positions, false),
    allocationCard("Largest issuers and fund houses", c.top_issuers, false));
}

// A holding's value at its latest public price. No price found: a dash, never an estimate.
function latestCell(l, column) {
  if (!l) return el("td", { class: "num", "data-label": column }, "–");
  const label = l.source === "AMFI" ? `NAV ${price(l.price)}` : `${price(l.price)} · ${l.source}`;
  return el("td", { class: "num", "data-label": column },
    money(l.value),
    el("div", { class: signClass(l.change) }, `${money(l.change)} ${pct(l.change_pct)}`),
    el("div", { class: l.stale ? "subtle loss" : "subtle" }, `${label} · ${day(l.price_date)}${l.stale ? " (old)" : ""}`));
}

async function refreshPrices() {
  const btn = document.getElementById("refresh-prices"), status = document.getElementById("refresh-status");
  btn.disabled = true;
  status.className = "subtle";
  status.textContent = "Downloading…";
  try {
    const r = await api("/api/prices/refresh", { method: "POST" });
    const parts = [];
    if (r.nav && r.nav.held) parts.push(`funds ${r.nav.priced} of ${r.nav.held}`);
    if (r.exchange && r.exchange.held) parts.push(`stocks, ETFs and bonds ${r.exchange.priced} of ${r.exchange.held}`);
    const problems = [...Object.values(r.errors), ...((r.exchange && r.exchange.warnings) || [])];
    status.className = problems.length ? "subtle loss" : "subtle";
    status.textContent = (parts.length ? `Priced: ${parts.join("; ")}. ` : "Nothing to price. ") + problems.join(" ");
    await load();
  } catch (err) {
    status.className = "subtle loss";
    status.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ---- holdings: see holdings.js ----------------------------------------------------

const Tools = window.PortfolioTools;

function collectAlerts(p) {
  const alerts = [];
  const { demat, mf } = p.statements;
  if (!mf && !demat) return alerts;  // the first-run steps on the Overview already say so
  if (!mf) alerts.push("No mutual fund data yet. Upload your NSDL/CDSL or CAMS/KFintech CAS.");
  if (!demat) alerts.push("No demat statement yet. Upload your NSDL or CDSL CAS for stocks and ETFs.");
  for (const [label, s] of [["Stock", demat], ["Mutual fund", mf]]) {
    if (s && s.stale) alerts.push(`${label} statement is ${s.age_days} days old. Upload a newer one.`);
    if (s) for (const w of s.warnings.slice(0, 5)) alerts.push(`${label} statement: ${w}`);
    if (s && s.warnings.length > 5) alerts.push(`${label} statement: ${s.warnings.length - 5} more warning(s).`);
  }
  if (p.as_of && p.as_of.mixed) {
    alerts.push(`The total adds statements ${p.as_of.spread_days} days apart (${day(p.as_of.from)} and ` +
      `${day(p.as_of.to)}), so it is not a value on one day. Upload a newer statement for the older part.`);
  }
  return alerts;
}

let lastPortfolio = null;

async function load() {
  try {
    const [portfolio, statements, history] = await Promise.all(
      [api("/api/portfolio"), api("/api/statements"), api("/api/history")]);
    lastPortfolio = portfolio;
    const empty = statements.statements.length === 0;
    document.getElementById("empty-state").hidden = !empty;
    document.getElementById("totals").hidden = empty;  // a row of zero totals would read as real figures
    document.getElementById("prices-bar").hidden = empty;
    renderTotals(portfolio);
    loadConfidence();  // not awaited: the panels filling in must not delay or break the page
    renderOverviewChange(history.points);
    renderHistory(history.points);
    setupChanges(history.points);
    renderAllocation(portfolio);
    renderConcentration(portfolio);
    renderStress(portfolio);
    renderHoldings(portfolio);
    loadReturns();  // only does anything while the Performance page is showing
    if (currentHoldingKey !== null) showHoldings(currentHoldingKey, false);  // a reload may have changed it
    renderStatements(statements.statements);
    loadAudit();  // not awaited: the activity list failing must not break the page
    showAlerts(collectAlerts(portfolio));
  } catch (err) {
    showLoadError(err);
  } finally {
    document.getElementById("loading").hidden = true;
  }
}

document.getElementById("refresh-prices").addEventListener("click", refreshPrices);
// Charts depend on their width, so they are drawn again when it changes and when their page is shown.
const redrawSized = () => {
  if (lastPortfolio) renderAllocation(lastPortfolio);
  if (lastHistory) renderHistory(lastHistory);
};
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(redrawSized, 150);
});
// A date chosen here is remembered; the default (the two latest) is not, so new statements still move it forward.
const compareChosen = () => {
  prefs.set("compare", { from: document.getElementById("chg-from").value, to: document.getElementById("chg-to").value });
  loadChanges();
};
document.getElementById("chg-from").addEventListener("change", compareChosen);
document.getElementById("chg-to").addEventListener("change", compareChosen);
initHoldings();
initImports();
initSettings();
window.Nav.init((page, arg, moveFocus) => {
  redrawSized();
  if (page === "settings") loadSettings();
  if (page === "performance") loadReturns();
  if (page === "holdings") showHoldings(arg, moveFocus);
  else currentHoldingKey = null;  // so a reload while on another page does not fetch a detail nobody is looking at
});
load();
// A visible label whenever the page shows the made-up demo portfolio. If the call fails the banner stays hidden.
fetch("/api/mode").then((r) => r.json()).then((m) => { document.getElementById("demo-banner").hidden = !m.demo; }).catch(() => {});
