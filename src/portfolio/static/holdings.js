"use strict";

// The Holdings page: one searchable, sortable list of every holding, and a detail view for one of them
// (#/holdings/<isin>) that shows where each number comes from. Loaded before app.js; it uses app.js's helpers
// (el, money, day, ...) only when its functions run, never while loading.

const holdingsState = { query: "", cls: "all", amc: "all", multi: false, key: "value", dir: "desc" };
let currentHoldingKey = null;

const HOLDING_SORT = {
  name: (h) => h.name, quantity: (h) => h.quantity, price: (h) => h.price, value: (h) => h.value,
  gain: (h) => h.gain, latest: (h) => (h.latest ? h.latest.value : null), weight: (h) => h.value,
};
const CLASS_LABEL = { equity: "Stock", fund: "ETF or demat fund", bond: "Bond or SGB", mf: "Mutual fund" };

// Stocks and funds share one shape so one table can show both. `parts` are the accounts or folios behind a row.
function holdingRows(p) {
  const total = p.totals.value;
  const demat = p.statements.demat, mf = p.statements.mf;
  const weight = (value) => (total ? (value / total) * 100 : null);
  const stocks = p.stocks.map((h) => ({
    cls: h.asset_type, key: h.isin, isin: h.isin, name: h.name, symbol: h.symbol, amc: null,
    sub: [CLASS_LABEL[h.asset_type], h.symbol, h.isin],
    quantity: h.quantity, price: h.price, priceDate: demat ? demat.period_to : null,
    cost: null, gain: null, gainPct: null, value: h.value, latest: h.latest, weight: weight(h.value),
    source: demat ? `${ISSUERS[demat.issuer]} statement to ${day(demat.period_to)}` : null,
    parts: h.accounts.map((a) => ({ label: `${a.label} ${a.client} · ${a.depository}`, quantity: a.quantity, value: a.value })),
    searchable: [h.name, h.symbol, h.isin, ...h.brokers, ...h.accounts.map((a) => a.client)],
  }));
  const funds = p.funds.map((f) => ({
    cls: "mf", key: f.isin || f.key, isin: f.isin, name: f.scheme, symbol: null, amc: f.amc,
    sub: [CLASS_LABEL.mf, f.amc, f.scheme_type, `${f.folios.length} folio${f.folios.length > 1 ? "s" : ""}`],
    quantity: f.units, price: f.nav, priceDate: f.nav_date,
    cost: f.cost, gain: f.gain, gainPct: f.gain_pct, value: f.value, latest: f.latest, weight: weight(f.value),
    source: mf ? `${ISSUERS[mf.issuer]} statement to ${day(mf.period_to)}` : null,
    parts: f.folios.map((x) => ({ label: `Folio ${x.folio}`, quantity: x.units, value: x.value })),
    searchable: [f.scheme, f.amc, f.isin, f.scheme_type, ...f.folios.map((x) => x.folio)],
  }));
  return [...stocks, ...funds];
}

function visibleHoldings(p) {
  const s = holdingsState;
  const list = holdingRows(p).filter((h) =>
    (s.cls === "all" || h.cls === s.cls)
    && (s.amc === "all" || (s.amc === "unknown" ? h.cls === "mf" && !h.amc : h.amc === s.amc))
    && (!s.multi || h.parts.length > 1)
    && Tools.matchesQuery(h.searchable, s.query));
  return Tools.sortRows(list, HOLDING_SORT[s.key], s.dir);
}

function fillFundHouses(p) {
  const select = document.getElementById("holdings-amc");
  const houses = [...new Set(p.funds.map((f) => f.amc).filter(Boolean))].sort();
  const options = [["all", "All"], ...houses.map((h) => [h, h])];
  if (p.funds.some((f) => !f.amc)) options.push(["unknown", "Unknown fund house"]);
  select.replaceChildren(...options.map(([v, label]) => el("option", { value: v }, label)));
  if (!options.some(([v]) => v === holdingsState.amc)) holdingsState.amc = "all";
  select.value = holdingsState.amc;
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
const HOLDING_CSV = [
  { header: "Name", value: (h) => h.name },
  { header: "Type", value: (h) => CLASS_LABEL[h.cls] },
  { header: "Symbol", value: (h) => h.symbol },
  { header: "Fund house", value: (h) => h.amc },
  { header: "ISIN", value: (h) => h.isin },
  { header: "Quantity", value: (h) => h.quantity },
  { header: "Statement price", value: (h) => h.price },
  { header: "Statement price date", value: (h) => h.priceDate },
  { header: "Statement value", value: (h) => h.value },
  { header: "Amount invested", value: (h) => h.cost },
  { header: "Gain", value: (h) => h.gain },
  { header: "Latest price", value: (h) => orNull(h.latest, "price") },
  { header: "Latest value", value: (h) => orNull(h.latest, "value") },
  { header: "Latest price date", value: (h) => orNull(h.latest, "price_date") },
  { header: "Price source", value: (h) => orNull(h.latest, "source") },
  { header: "Statement", value: (h) => h.source },
  { header: "Accounts or folios", value: (h) => h.parts.map((x) => x.label).join("; ") },
];

// Saves exactly what the table shows (filters and sort applied) as a file in the browser.
// A byte-order mark makes Excel read it as UTF-8.
function downloadCsv(filename, text) {
  const url = URL.createObjectURL(new Blob(["﻿", text], { type: "text/csv;charset=utf-8" }));
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A missing gain says why, instead of a bare dash: stocks have no purchase cost on NSDL or CDSL statements.
function gainCell(h) {
  if (h.gain === null) {
    return el("td", { class: "num", "data-label": "Gain" }, "–", el("div", { class: "subtle" }, "no cost on statement"));
  }
  return el("td", { class: `num ${signClass(h.gain)}`, "data-label": "Gain" }, money(h.gain),
    el("div", { class: "subtle" }, `${pct(h.gainPct)} on ${money(h.cost)}`));
}

function renderHoldings(p) {
  const all = holdingRows(p);
  document.getElementById("holdings-empty").hidden = all.length > 0;
  document.getElementById("holdings-section").hidden = all.length === 0;
  const dates = [p.statements.demat && `demat to ${day(p.statements.demat.period_to)}`,
    p.statements.mf && `funds to ${day(p.statements.mf.period_to)}`].filter(Boolean);
  document.getElementById("holdings-asof").textContent = dates.length ? `· statement values, ${dates.join(", ")}` : "";
  fillFundHouses(p);

  const list = visibleHoldings(p);
  const rows = list.flatMap((h) => {
    const sub = h.parts.map((x) => el("tr", null,
      el("td", null, x.label),
      el("td", { class: "num", "data-label": "Quantity" }, qtyFmt.format(x.quantity)),
      el("td"), el("td", { class: "num", "data-label": "Value" }, money(x.value)), el("td"), el("td"), el("td")));
    return expandable([
      el("td", null,
        el("a", { class: "symbol", href: `#/holdings/${encodeURIComponent(h.key)}` }, h.name),
        el("div", { class: "subtle" }, h.sub.filter(Boolean).join(" · "))),
      el("td", { class: "num", "data-label": "Quantity" }, qtyFmt.format(h.quantity)),
      el("td", { class: "num", "data-label": "Price" }, price(h.price), el("div", { class: "subtle" }, day(h.priceDate))),
      el("td", { class: "num", "data-label": "Value" }, money(h.value)),
      gainCell(h),
      latestCell(h.latest, "At latest price"),
      el("td", { class: "num", "data-label": "Weight" }, h.weight === null ? "–" : `${h.weight.toFixed(1)}%`),
    ], sub);
  });
  if (rows.length === 0 && all.length) {
    rows.push(el("tr", null, el("td", { colspan: "7", class: "empty" }, "No holding matches the search and filters.")));
  }
  showCount("holdings-count", list.length, all.length, "holdings");
  markSorted("holdings", holdingsState);
  document.querySelector("#holdings tbody").replaceChildren(...rows);
}

// ---- one holding ------------------------------------------------------------------------------

const UNIT_FORMAT = {
  inr: (n) => (n === null || n === undefined ? Fmt.MISSING : Fmt.price(n)),
  quantity: (n) => Fmt.quantity(n),
  percent: (n) => (n === null || n === undefined ? Fmt.MISSING : `${n.toFixed(2)}%`),
};

function calcCard(c) {
  const format = UNIT_FORMAT[c.unit];
  return el("article", { class: "calc" },
    el("h4", null, c.label),
    el("div", { class: "calc-value" }, c.value === null ? Fmt.MISSING : format(c.value)),
    el("p", { class: "subtle" }, c.method),
    c.inputs.length ? el("dl", { class: "calc-inputs" },
      c.inputs.flatMap((i) => [el("dt", null, i.label), el("dd", null, UNIT_FORMAT[i.unit](i.value))])) : null,
    c.source || c.as_of ? el("p", { class: "subtle" },
      ["Source: " + (c.source || "statement"), c.as_of ? `as of ${day(c.as_of)}` : null].filter(Boolean).join(", ")) : null,
    c.limitation ? el("p", { class: "limit" },
      el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("span", { class: "sr-only" }, "Limitation: "), c.limitation) : null);
}

function componentBlock(c) {
  const folio = c.source === "folio";
  const s = c.statement;
  return el("div", { class: "card" },
    el("h4", null, folio ? "Mutual fund folios" : "Demat accounts"),
    el("p", { class: "subtle" },
      `${ISSUERS[s.issuer]} statement dated ${day(s.period_to)} (${plural(s.age_days, "day")} old${s.stale ? ", older than the limit" : ""}).`),
    el("ul", { class: "parts" }, (folio ? c.folios : c.accounts).map((x) => el("li", null,
      el("span", null, folio ? `Folio ${x.folio}` : `${x.label} ${x.client || ""} · ${x.depository}`),
      el("span", { class: "num" }, `${qtyFmt.format(x.units === undefined ? x.quantity : x.units)} · ${money(x.value)}`)))));
}

function historyTable(d) {
  if (d.history.length === 0) return el("p", { class: "subtle" }, "No statement history for this holding.");
  const rows = d.history.map((pt) => el("tr", null,
    el("td", null, day(pt.date)),
    el("td", { class: "num", "data-label": "Quantity" }, qtyFmt.format(pt.quantity)),
    el("td", { class: "num", "data-label": "Value" }, money(pt.value)),
    el("td", { "data-label": "From" }, pt.parts.map((x) => `${ISSUERS[x.issuer]} ${day(x.statement_date)}`).join(" and "))));
  return el("div", { class: "table-wrap" }, el("table", { class: "cards", id: "holding-history" },
    el("thead", null, el("tr", null,
      el("th", { scope: "col" }, "Statement date"), el("th", { scope: "col", class: "num" }, "Quantity"),
      el("th", { scope: "col", class: "num" }, "Value"), el("th", { scope: "col" }, "From"))),
    el("tbody", null, rows)));
}

function renderHoldingDetail(d) {
  const latest = d.calculations.find((c) => c.id === "latest_value");
  const heading = el("h2", { id: "holding-title", tabindex: "-1" }, d.name);
  document.getElementById("holding-detail").replaceChildren(el("div", null,  // el() skips null children; replaceChildren would print "null"
    el("p", null, el("a", { href: "#/holdings" }, "← All holdings")),
    heading,
    el("p", { class: "subtle" }, [d.kind, d.symbol, d.amc, d.isin].filter(Boolean).join(" · ")),
    el("div", { class: "tiles" },
      tileEl("Statement value", money(d.value), d.as_of ? `as of ${day(d.as_of.to)}` : null),
      tileEl("Share of portfolio", d.weight === null ? Fmt.MISSING : `${d.weight.toFixed(1)}%`),
      tileEl("At latest price", latest && latest.value !== null ? money(latest.value) : Fmt.MISSING,
        latest && latest.as_of ? `${latest.source}, ${day(latest.as_of)}` : "not available")),
    el("h3", null, "Where it is held"),
    d.components.map(componentBlock),
    el("h3", null, "How each figure is calculated"),
    el("p", { class: "subtle" }, "Each figure lists the values it was added up from, the statement and date they came from, and what the data cannot tell you."),
    el("div", { class: "calcs" }, d.calculations.map(calcCard)),
    d.notes.length ? el("div", null, el("h3", null, "Good to know"), el("ul", null, d.notes.map((n) => el("li", null, n)))) : null,
    el("h3", null, "Value at each statement"),
    el("p", { class: "subtle" }, d.history_note),
    historyTable(d)));
  return heading;
}

// Shows the list, or the detail for `key`. Called by the router and after the data reloads.
async function showHoldings(key, moveFocus) {
  currentHoldingKey = key;
  document.getElementById("holdings-list").hidden = key !== null;
  const box = document.getElementById("holding-detail");
  box.hidden = key === null;
  if (key === null) return;
  box.replaceChildren(el("p", { class: "subtle", role: "status" }, "Loading…"));
  try {
    const d = await api(`/api/holdings/${encodeURIComponent(key)}`);
    if (currentHoldingKey !== key) return;  // the user went elsewhere while this loaded
    const heading = renderHoldingDetail(d);
    document.title = `${d.name} · Portfolio`;
    if (moveFocus) heading.focus();
  } catch (err) {
    if (currentHoldingKey !== key) return;
    box.replaceChildren(
      el("p", null, el("a", { href: "#/holdings" }, "← All holdings")),
      el("h2", { id: "holding-title", tabindex: "-1" }, "Holding not found"),
      el("p", { class: "subtle" }, `${err.message} It may have been removed with a statement, or the link is old.`));
    if (moveFocus) document.getElementById("holding-title").focus();
  }
}

function initHoldings() {
  const rerender = () => lastPortfolio && renderHoldings(lastPortfolio);
  makeSortable("holdings", holdingsState, rerender);
  setupMobileSort("holdings", holdingsState, rerender);
  document.getElementById("holdings-q").addEventListener("input",
    Tools.debounce((e) => { holdingsState.query = e.target.value; rerender(); }, 150));
  document.getElementById("holdings-class").addEventListener("change", (e) => { holdingsState.cls = e.target.value; rerender(); });
  document.getElementById("holdings-amc").addEventListener("change", (e) => { holdingsState.amc = e.target.value; rerender(); });
  document.getElementById("holdings-multi").addEventListener("change", (e) => { holdingsState.multi = e.target.checked; rerender(); });
  document.getElementById("holdings-csv").addEventListener("click", () => {
    if (!lastPortfolio) return;
    const dates = [lastPortfolio.statements.demat, lastPortfolio.statements.mf].filter(Boolean).map((s) => s.period_to).sort();
    downloadCsv(`holdings-${dates[dates.length - 1] || "all"}.csv`, Tools.toCsv(HOLDING_CSV, visibleHoldings(lastPortfolio)));
  });
}
