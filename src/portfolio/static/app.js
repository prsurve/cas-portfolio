"use strict";

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const qtyFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });
const pctFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, signDisplay: "always" });
const dateFmt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

// Build DOM nodes without innerHTML so statement text can never inject markup.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const signClass = (n) => (n > 0 ? "gain" : n < 0 ? "loss" : "");
const money = (n) => (n === null || n === undefined ? "–" : inr.format(n));
const price = (n) => (n === null || n === undefined ? "–" : inr2.format(n));
const pct = (n) => (n === null || n === undefined ? "" : `${pctFmt.format(n)}%`);
const day = (iso) => (iso ? dateFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00`)) : "–");
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

// A summary row that expands to show its per-folio or per-account rows.
function expandable(mainCells, subRows) {
  const toggle = (row) => {
    const open = row.getAttribute("aria-expanded") !== "true";
    row.setAttribute("aria-expanded", String(open));
    subRows.forEach((r) => { r.hidden = !open; });
  };
  const main = el("tr", {
    class: "holding", tabindex: "0", "aria-expanded": "false",
    onclick: (e) => toggle(e.currentTarget),
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

// ---- upload -------------------------------------------------------------------

function renderUploadResult(r) {
  const lines = [
    el("div", { class: "result-title" },
      `${r.created ? "Imported" : "Already imported"}: ${r.issuer} statement, ${day(r.period_from)} to ${day(r.period_to)}`),
  ];
  if (r.kind === "mf") {
    lines.push(el("div", null, `${r.funds} funds in ${r.folios} folios. Value ${money(r.total_value)}` +
      (r.total_cost !== null ? `, invested ${money(r.total_cost)}` : "") + `. ${r.transactions} transactions.`));
  } else {
    lines.push(el("div", null,
      `${r.stocks} stocks, ${r.funds} ETFs/funds, ${r.bonds} bonds worth ${money(r.total_value)} ` +
      `across ${r.accounts.length} demat account(s):`));
    lines.push(el("ul", null, r.accounts.map((a) => {
      const ok = Math.abs(a.gap) <= 1;
      return el("li", { class: ok ? "" : "loss" },
        `${a.label} ${a.client} (${a.depository}): read ${money(a.imported_value)}` +
        ` of statement total ${money(a.statement_total)}` + (ok ? " ✓" : ` · ${money(a.gap)} missing`));
    })));
    if (r.folio_funds) {
      lines.push(el("div", null, `Mutual fund folios: ${r.folio_funds} holdings worth ${money(r.folio_value)}` +
        (r.folio_cost !== null ? `, invested ${money(r.folio_cost)}` : "") + "."));
    }
  }
  for (const w of r.warnings) lines.push(el("div", { class: "loss" }, `Warning: ${w}`));
  for (const n of r.notes) lines.push(el("div", { class: "subtle" }, n));
  lines.push(el("div", { class: "subtle" }, "Check these totals against the PDF. If something is wrong, delete the statement below."));
  document.getElementById("upload-result").replaceChildren(el("div", { class: "result" }, lines));
}

async function upload(event) {
  event.preventDefault();
  const fileInput = document.getElementById("pdf");
  const passwordInput = document.getElementById("password");
  const btn = document.getElementById("upload-btn");
  const file = fileInput.files[0];
  if (!file) return;
  btn.disabled = true;
  btn.textContent = "Reading…";
  try {
    const result = await api("/api/statements", {
      method: "POST",
      headers: { "Content-Type": "application/pdf", "X-PDF-Password": passwordInput.value },
      body: file,
    });
    renderUploadResult(result);
    fileInput.value = "";
    await load();
  } catch (err) {
    document.getElementById("upload-result").replaceChildren(el("div", { class: "result error" }, err.message));
  } finally {
    passwordInput.value = "";  // never kept in the page
    btn.disabled = false;
    btn.textContent = "Upload";
  }
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
  const tiles = [tile("Total value", money(t.value), range)];
  if (p.statements.demat) {
    tiles.push(tile("Demat holdings", money(t.demat_value), `as of ${day(p.statements.demat.period_to)}`));
  }
  if (p.statements.mf) {
    tiles.push(tile("Mutual fund folios", money(t.mf_value), `as of ${day(p.statements.mf.period_to)}`));
    if (t.mf_cost !== null) {
      tiles.push(tile("Mutual funds invested", money(t.mf_cost)));
      tiles.push(tile("Mutual fund gain", money(t.mf_gain), pct(t.mf_gain_pct), signClass(t.mf_gain)));
    }
  }
  for (const [label, m] of [["Demat", t.demat_latest], ["Funds", t.mf_latest]]) {
    if (!m) continue;
    tiles.push(tile(`${label} at latest price`, money(m.value), `${m.priced} of ${m.total}, as of ${day(m.as_of)}`));
    tiles.push(tile(`${label}: change since statement`, money(m.change), pct(m.change_pct), signClass(m.change)));
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

// ---- completeness ------------------------------------------------------------------

function completenessChecks(c) {
  const checks = [];  // [ok, text, detail?]
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (c.demat) {
    const d = c.demat;
    if (!d.available) {
      checks.push([false, "Account totals were not stored for this demat statement.",
        "Upload the same PDF again. It will be read with the current version and each account checked against its printed total."]);
    } else if (d.all_match) {
      checks.push([true, `All ${plural(d.accounts.length, "demat account")} add up to the totals printed on the statement.`]);
    } else {
      const bad = d.accounts.filter((a) => !a.ok);
      checks.push([false, `${bad.length} of ${plural(d.accounts.length, "demat account")} do not add up to the statement total. ` +
        `${money(d.imported_value)} of ${money(d.statement_total)} was read.`,
        bad.map((a) => `${a.label} ${a.client || ""}: statement ${money(a.statement_total)}, read ${money(a.imported_value)}`).join(" · ")]);
    }
    if (d.available && d.folios) {
      checks.push([d.folios.ok, d.folios.ok ? "Mutual fund folios add up to the statement total."
        : `Mutual fund folios: statement ${money(d.folios.statement_total)}, read ${money(d.folios.imported_value)}.`]);
    }
  }
  if (c.stocks && c.stocks.without_symbol) {
    checks.push([false, `${plural(c.stocks.without_symbol, "stock")} of ${c.stocks.count} ${c.stocks.without_symbol === 1 ? "has" : "have"} no NSE symbol.`,
      "They are shown by name. Prices from public data (a later feature) need a match by ISIN."]);
  }
  if (c.holdings) {
    const h = c.holdings;
    checks.push([h.without_price === 0, h.without_price === 0 ? `Every demat holding has a latest price.`
      : h.priced === 0 ? "No latest prices yet."
      : `${h.without_price} of ${h.count} demat holdings have no price newer than the statement.`,
      h.without_price === 0 ? null : "Use Refresh prices. Unlisted or delisted securities have none; they show a dash and are never estimated."]);
  }
  if (c.funds) {
    const f = c.funds;
    checks.push([f.without_cost === 0, f.without_cost === 0 ? `All ${plural(f.count, "fund")} have a purchase cost.`
      : `${f.without_cost} of ${plural(f.count, "fund")} ${f.without_cost === 1 ? "has" : "have"} no purchase cost, so no gain is shown for ${f.without_cost === 1 ? "it" : "them"}.`]);
    checks.push([f.without_price === 0, f.without_price === 0 ? `Every fund has a latest NAV.`
      : f.priced === 0 ? "No latest NAVs yet."
      : `${f.without_price} of ${plural(f.count, "fund")} ${f.without_price === 1 ? "has" : "have"} no NAV newer than the statement.`,
      f.without_price === 0 ? null : "Use Refresh prices. A fund AMFI does not list stays at its statement value and shows a dash."]);
    if (f.unknown_amc) checks.push([false, `${plural(f.unknown_amc, "fund")} ${f.unknown_amc === 1 ? "has" : "have"} no fund house name.`]);
    const h = f.history;
    if (h.unknown) {
      checks.push([false, `Transaction history is missing for ${h.unknown} of ${plural(f.count, "fund")}.`,
        "Upload a detailed CAS from CAMS or KFintech to get it. Returns (XIRR) need it."]);
    }
    if (h.incomplete) {
      checks.push([false, `${plural(h.incomplete, "fund")} ${h.incomplete === 1 ? "was" : "were"} held before the first statement began, so the history is partial.`,
        "Request a CAMS or KFintech statement covering the full period."]);
    }
    if (!h.unknown && !h.incomplete) checks.push([true, "Transaction history covers the whole holding for every fund."]);
  }
  return checks;
}

function renderCompleteness(p) {
  const checks = completenessChecks(p.completeness);
  document.getElementById("complete-section").hidden = checks.length === 0;
  document.getElementById("completeness").replaceChildren(...checks.map(([ok, text, detail]) =>
    el("li", { class: ok ? "ok" : "warn" },
      el("span", { class: "mark", "aria-hidden": "true" }, ok ? "✓" : "!"),
      el("div", null,
        el("span", { class: "sr-only" }, ok ? "OK: " : "Needs attention: "), text,
        detail ? el("div", { class: "detail" }, detail) : null))));
}

// ---- changes between statements ---------------------------------------------------

const CHANGE_KINDS = { new: "New", exited: "Sold out", more: "Bought more", less: "Sold some", price: "Price only" };

function setupChanges(points) {
  document.getElementById("changes-section").hidden = points.length < 2;
  if (points.length < 2) return;
  const from = document.getElementById("chg-from"), to = document.getElementById("chg-to");
  const keep = [from.value, to.value];
  const fill = (select, wanted) => {
    select.replaceChildren(...points.map((pt) => el("option", { value: pt.date }, day(pt.date))));
    select.value = points.some((pt) => pt.date === wanted) ? wanted : "";
  };
  fill(from, keep[0] || points[points.length - 2].date);
  fill(to, keep[1] || points[points.length - 1].date);
  if (!from.value) from.value = points[points.length - 2].date;
  if (!to.value) to.value = points[points.length - 1].date;
  loadChanges();
}

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
  document.getElementById("conc-note").textContent =
    "Worked out from statement values. A position is one ISIN, and a mutual fund or ETF counts as one position " +
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

// ---- search, sort and export for the holdings tables -------------------------------

const Tools = window.PortfolioTools;
const tableState = {
  stocks: { query: "", type: "all", key: "value", dir: "desc" },
  funds: { query: "", key: "value", dir: "desc" },
};
const STOCK_SORT = {
  name: (h) => h.name, quantity: (h) => h.quantity, price: (h) => h.price, value: (h) => h.value,
  latest: (h) => (h.latest ? h.latest.value : null), weight: (h) => h.value, brokers: (h) => h.brokers[0] || null,
};
const FUND_SORT = {
  scheme: (f) => f.scheme, units: (f) => f.units, nav: (f) => f.nav, cost: (f) => f.cost, value: (f) => f.value,
  gain: (f) => f.gain, latest: (f) => (f.latest ? f.latest.value : null),
};

function visibleStocks(p) {
  const s = tableState.stocks;
  const multiOnly = document.getElementById("multi-only").checked;
  const list = p.stocks.filter((h) =>
    (!multiOnly || h.accounts.length > 1) && (s.type === "all" || h.asset_type === s.type)
    && Tools.matchesQuery([h.name, h.symbol, h.isin, ...h.brokers, ...h.accounts.map((a) => a.client)], s.query));
  return Tools.sortRows(list, STOCK_SORT[s.key], s.dir);
}

function visibleFunds(p) {
  const s = tableState.funds;
  const list = p.funds.filter((f) =>
    Tools.matchesQuery([f.scheme, f.amc, f.isin, f.scheme_type, ...f.folios.map((x) => x.folio)], s.query));
  return Tools.sortRows(list, FUND_SORT[s.key], s.dir);
}

function showCount(id, shown, total, noun) {
  document.getElementById(id).textContent = shown === total ? `${total} ${noun}` : `Showing ${shown} of ${total} ${noun}`;
}

// Header cells become buttons: click to sort, click again to reverse.
function makeSortable(tableId, state, rerender) {
  for (const th of document.querySelectorAll(`#${tableId} th[data-sort]`)) {
    const key = th.dataset.sort;
    th.replaceChildren(el("button", {
      type: "button", class: "sort",
      onclick: () => {
        if (state.key === key) state.dir = state.dir === "asc" ? "desc" : "asc";
        else { state.key = key; state.dir = th.classList.contains("num") ? "desc" : "asc"; }
        rerender();
      },
    }, th.textContent.trim()));
  }
}

function markSorted(tableId, state) {
  for (const th of document.querySelectorAll(`#${tableId} th[data-sort]`)) {
    const on = th.dataset.sort === state.key;
    th.setAttribute("aria-sort", on ? (state.dir === "asc" ? "ascending" : "descending") : "none");
    th.querySelector("button").dataset.arrow = on ? (state.dir === "asc" ? "▲" : "▼") : "";
  }
  const select = document.getElementById(`${tableId}-sort`);
  if (select) select.value = `${state.key}:${state.dir}`;
}

// On a phone the header row is hidden, so sorting moves to a dropdown that offers the same columns.
function setupMobileSort(tableId, state, rerender) {
  const select = document.getElementById(`${tableId}-sort`);
  for (const th of document.querySelectorAll(`#${tableId} th[data-sort]`)) {
    const name = th.textContent.trim(), numeric = th.classList.contains("num");
    for (const dir of numeric ? ["desc", "asc"] : ["asc", "desc"]) {
      const how = numeric ? (dir === "desc" ? "high to low" : "low to high") : (dir === "asc" ? "A to Z" : "Z to A");
      select.append(el("option", { value: `${th.dataset.sort}:${dir}` }, `${name}: ${how}`));
    }
  }
  select.value = `${state.key}:${state.dir}`;
  select.addEventListener("change", () => {
    [state.key, state.dir] = select.value.split(":");
    rerender();
  });
}

const orNull = (l, field) => (l ? l[field] : null);
const STOCK_CSV = [
  { header: "Name", value: (h) => h.name },
  { header: "Type", value: (h) => ASSET_TYPES[h.asset_type] || h.asset_type },
  { header: "Symbol", value: (h) => h.symbol },
  { header: "ISIN", value: (h) => h.isin },
  { header: "Quantity", value: (h) => h.quantity },
  { header: "Statement price", value: (h) => h.price },
  { header: "Statement value", value: (h) => h.value },
  { header: "Latest price", value: (h) => orNull(h.latest, "price") },
  { header: "Latest value", value: (h) => orNull(h.latest, "value") },
  { header: "Latest price date", value: (h) => orNull(h.latest, "price_date") },
  { header: "Price source", value: (h) => orNull(h.latest, "source") },
  { header: "Accounts", value: (h) => h.accounts.map((a) => `${a.label} ${a.client}`).join("; ") },
];
const FUND_CSV = [
  { header: "Scheme", value: (f) => f.scheme },
  { header: "Fund house", value: (f) => f.amc },
  { header: "ISIN", value: (f) => f.isin },
  { header: "Type", value: (f) => f.scheme_type },
  { header: "Units", value: (f) => f.units },
  { header: "Statement NAV", value: (f) => f.nav },
  { header: "Statement NAV date", value: (f) => f.nav_date },
  { header: "Invested", value: (f) => f.cost },
  { header: "Statement value", value: (f) => f.value },
  { header: "Gain", value: (f) => f.gain },
  { header: "Latest NAV", value: (f) => orNull(f.latest, "price") },
  { header: "Latest value", value: (f) => orNull(f.latest, "value") },
  { header: "Latest NAV date", value: (f) => orNull(f.latest, "price_date") },
  { header: "Folios", value: (f) => f.folios.map((x) => x.folio).join("; ") },
];

// Saves exactly what the table shows (filters and sort applied) as a file in the browser.
// A byte-order mark makes Excel read it as UTF-8.
function downloadCsv(filename, text) {
  const url = URL.createObjectURL(new Blob(["\ufeff", text], { type: "text/csv;charset=utf-8" }));
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderFunds(p) {
  document.getElementById("funds-section").hidden = p.funds.length === 0;
  if (p.statements.mf) {
    document.getElementById("funds-asof").textContent =
      `· ${ISSUERS[p.statements.mf.issuer]} statement to ${day(p.statements.mf.period_to)}`;
  }
  const list = visibleFunds(p);
  const rows = list.flatMap((f) => expandable([
    el("td", null, el("div", { class: "symbol" }, f.scheme),
      el("div", { class: "subtle" },
        [f.amc, f.scheme_type, `${f.folios.length} folio${f.folios.length > 1 ? "s" : ""}`].filter(Boolean).join(" · "))),
    el("td", { class: "num", "data-label": "Units" }, qtyFmt.format(f.units)),
    el("td", { class: "num", "data-label": "NAV" }, price(f.nav), el("div", { class: "subtle" }, day(f.nav_date))),
    el("td", { class: "num", "data-label": "Invested" }, money(f.cost)),
    el("td", { class: "num", "data-label": "Value" }, money(f.value)),
    el("td", { class: `num ${signClass(f.gain)}`, "data-label": "Gain" }, money(f.gain), el("div", { class: "subtle" }, pct(f.gain_pct))),
    latestCell(f.latest, "At latest NAV"),
  ], f.folios.map((x) => el("tr", null,
    el("td", null, `Folio ${x.folio}`),
    el("td", { class: "num", "data-label": "Units" }, qtyFmt.format(x.units)),
    el("td"),
    el("td", { class: "num", "data-label": "Invested" }, money(x.cost)),
    el("td", { class: "num", "data-label": "Value" }, money(x.value)),
    el("td"), el("td")))));
  if (rows.length === 0 && p.funds.length) {
    rows.push(el("tr", null, el("td", { colspan: "7", class: "empty" }, "No fund matches the search.")));
  }
  showCount("funds-count", list.length, p.funds.length, "folio funds");
  markSorted("funds", tableState.funds);
  document.querySelector("#funds tbody").replaceChildren(...rows);
}

function renderStocks(p) {
  document.getElementById("stocks-section").hidden = p.stocks.length === 0;
  if (p.statements.demat) {
    document.getElementById("stocks-asof").textContent =
      `· ${ISSUERS[p.statements.demat.issuer]} statement to ${day(p.statements.demat.period_to)}`;
  }
  const total = p.totals.demat_value;
  const list = visibleStocks(p);
  const rows = list.flatMap((h) => expandable([
    el("td", null, el("div", { class: "symbol" }, h.name),
      el("div", { class: "subtle" },
        [ASSET_TYPES[h.asset_type] || h.asset_type, h.symbol, h.isin].filter(Boolean).join(" · "))),
    el("td", { class: "num", "data-label": "Quantity" }, qtyFmt.format(h.quantity)),
    el("td", { class: "num", "data-label": "Price" }, price(h.price)),
    el("td", { class: "num", "data-label": "Value" }, money(h.value)),
    latestCell(h.latest, "At latest price"),
    el("td", { class: "num", "data-label": "Weight" }, total ? `${((h.value / total) * 100).toFixed(1)}%` : "–"),
    el("td", { "data-label": "Brokers" }, el("div", { class: "chips" }, h.brokers.map((b) => el("span", { class: "chip" }, b)))),
  ], h.accounts.map((a) => el("tr", null,
    el("td", null, `${a.label} ${a.client} · ${a.depository}`),
    el("td", { class: "num", "data-label": "Quantity" }, qtyFmt.format(a.quantity)),
    el("td"),
    el("td", { class: "num", "data-label": "Value" }, money(a.value)),
    el("td"), el("td"), el("td")))));
  if (rows.length === 0 && p.stocks.length) {
    rows.push(el("tr", null, el("td", { colspan: "7", class: "empty" }, "No holding matches the search and filters.")));
  }
  showCount("stocks-count", list.length, p.stocks.length, "holdings");
  markSorted("stocks", tableState.stocks);
  document.querySelector("#stocks tbody").replaceChildren(...rows);
}

function statementStatus(s) {
  const notes = [];
  if (s.outdated) notes.push(el("div", { class: "loss" }, "Read by an older version. Upload this PDF again to refresh it."));
  if (s.warnings.length) notes.push(el("div", { class: "loss" }, `${s.warnings.length} warning(s)`));
  return notes.length ? notes : "OK";
}

function renderStatements(list) {
  const rows = list.map((s) => el("tr", null,
    el("td", { "data-label": "Issuer" }, ISSUERS[s.issuer] || s.issuer),
    el("td", { "data-label": "Covers" }, s.kind === "mf" ? "Mutual funds" : "Stocks"),
    el("td", { "data-label": "Period" }, `${day(s.period_from)} – ${day(s.period_to)}`),
    el("td", { "data-label": "Imported" }, day(s.imported_at)),
    el("td", { "data-label": "Status" }, statementStatus(s)),
    el("td", { class: "num" }, el("button", { type: "button", onclick: () => removeStatement(s) }, "Delete"))));
  if (rows.length === 0) {
    rows.push(el("tr", null, el("td", { colspan: "6", class: "empty" }, "Nothing imported yet. Upload a statement above.")));
  }
  document.querySelector("#statements tbody").replaceChildren(...rows);
}

function collectAlerts(p) {
  const alerts = [];
  const { demat, mf } = p.statements;
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
    renderTotals(portfolio);
    renderCompleteness(portfolio);
    renderHistory(history.points);
    setupChanges(history.points);
    renderAllocation(portfolio);
    renderConcentration(portfolio);
    renderFunds(portfolio);
    renderStocks(portfolio);
    renderStatements(statements.statements);
    showAlerts(collectAlerts(portfolio));
  } catch (err) {
    showAlerts([`Could not load data: ${err.message}`]);
  }
}

async function removeStatement(s) {
  if (!confirm(`Delete the ${ISSUERS[s.issuer]} statement to ${day(s.period_to)}? Its holdings are removed.`)) return;
  try {
    await api(`/api/statements/${s.id}`, { method: "DELETE" });
  } catch (err) {
    showAlerts([`Delete failed: ${err.message}`]);
  }
  await load();
}

document.getElementById("refresh-prices").addEventListener("click", refreshPrices);
let resizeTimer = null;
window.addEventListener("resize", () => {  // segment widths change, so which labels fit does too
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (lastPortfolio) renderAllocation(lastPortfolio);
    if (lastHistory) renderHistory(lastHistory);
  }, 150);
});
document.getElementById("chg-from").addEventListener("change", loadChanges);
document.getElementById("chg-to").addEventListener("change", loadChanges);
document.getElementById("upload-form").addEventListener("submit", upload);
const rerenderStocks = () => lastPortfolio && renderStocks(lastPortfolio);
const rerenderFunds = () => lastPortfolio && renderFunds(lastPortfolio);
makeSortable("stocks", tableState.stocks, rerenderStocks);
makeSortable("funds", tableState.funds, rerenderFunds);
setupMobileSort("stocks", tableState.stocks, rerenderStocks);
setupMobileSort("funds", tableState.funds, rerenderFunds);
document.getElementById("multi-only").addEventListener("change", rerenderStocks);
document.getElementById("stocks-q").addEventListener("input", (e) => { tableState.stocks.query = e.target.value; rerenderStocks(); });
document.getElementById("stocks-type").addEventListener("change", (e) => { tableState.stocks.type = e.target.value; rerenderStocks(); });
document.getElementById("funds-q").addEventListener("input", (e) => { tableState.funds.query = e.target.value; rerenderFunds(); });
document.getElementById("stocks-csv").addEventListener("click", () => {
  if (!lastPortfolio || !lastPortfolio.statements.demat) return;
  downloadCsv(`demat-holdings-${lastPortfolio.statements.demat.period_to}.csv`, Tools.toCsv(STOCK_CSV, visibleStocks(lastPortfolio)));
});
document.getElementById("funds-csv").addEventListener("click", () => {
  if (!lastPortfolio || !lastPortfolio.statements.mf) return;
  downloadCsv(`mutual-funds-${lastPortfolio.statements.mf.period_to}.csv`, Tools.toCsv(FUND_CSV, visibleFunds(lastPortfolio)));
});
load();
