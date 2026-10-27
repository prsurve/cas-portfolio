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

function renderTotals(p) {
  const t = p.totals;
  const tile = (label, value, sub, cls) =>
    el("div", { class: "tile" },
      el("div", { class: "label" }, label),
      el("div", { class: `value ${cls || ""}` }, value),
      sub ? el("div", { class: `sub ${cls || ""}` }, sub) : null);
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
  document.getElementById("totals").replaceChildren(...tiles);
}

function renderAllocation(p) {
  const a = p.allocation;
  const groups = [
    ["By asset class", a.asset_class.length > 1 ? a.asset_class : []],  // one class at 100% says nothing
    ["Stocks by broker", a.broker],
    ["Mutual funds by AMC", a.amc],
    ["Mutual funds by type", a.fund_type],
  ].filter(([, rows]) => rows.length > 0);
  document.getElementById("alloc-section").hidden = groups.length === 0;
  document.getElementById("allocation").replaceChildren(...groups.map(([title, rows]) =>
    el("div", { class: "table-wrap" },
      el("table", null,
        el("caption", null, title),
        el("tbody", null, rows.map((r) =>
          el("tr", null,
            el("td", null, r.label),
            el("td", { class: "num" }, money(r.value)),
            el("td", { class: "num" }, `${r.weight.toFixed(1)}%`))))))));
}

function renderFunds(p) {
  document.getElementById("funds-section").hidden = p.funds.length === 0;
  if (p.statements.mf) {
    document.getElementById("funds-asof").textContent =
      `· ${ISSUERS[p.statements.mf.issuer]} statement to ${day(p.statements.mf.period_to)}`;
  }
  const rows = p.funds.flatMap((f) => expandable([
    el("td", null, el("div", { class: "symbol" }, f.scheme),
      el("div", { class: "subtle" },
        [f.amc, f.scheme_type, `${f.folios.length} folio${f.folios.length > 1 ? "s" : ""}`].filter(Boolean).join(" · "))),
    el("td", { class: "num" }, qtyFmt.format(f.units)),
    el("td", { class: "num" }, price(f.nav), el("div", { class: "subtle" }, day(f.nav_date))),
    el("td", { class: "num" }, money(f.cost)),
    el("td", { class: "num" }, money(f.value)),
    el("td", { class: `num ${signClass(f.gain)}` }, money(f.gain), el("div", { class: "subtle" }, pct(f.gain_pct))),
  ], f.folios.map((x) => el("tr", null,
    el("td", null, `Folio ${x.folio}`),
    el("td", { class: "num" }, qtyFmt.format(x.units)),
    el("td"),
    el("td", { class: "num" }, money(x.cost)),
    el("td", { class: "num" }, money(x.value)),
    el("td")))));
  document.querySelector("#funds tbody").replaceChildren(...rows);
}

function renderStocks(p) {
  document.getElementById("stocks-section").hidden = p.stocks.length === 0;
  if (p.statements.demat) {
    document.getElementById("stocks-asof").textContent =
      `· ${ISSUERS[p.statements.demat.issuer]} statement to ${day(p.statements.demat.period_to)}`;
  }
  const multiOnly = document.getElementById("multi-only").checked;
  const total = p.totals.demat_value;
  const list = p.stocks.filter((h) => !multiOnly || h.accounts.length > 1);
  const rows = list.flatMap((h) => expandable([
    el("td", null, el("div", { class: "symbol" }, h.name),
      el("div", { class: "subtle" },
        [ASSET_TYPES[h.asset_type] || h.asset_type, h.symbol, h.isin].filter(Boolean).join(" · "))),
    el("td", { class: "num" }, qtyFmt.format(h.quantity)),
    el("td", { class: "num" }, price(h.price)),
    el("td", { class: "num" }, money(h.value)),
    el("td", { class: "num" }, total ? `${((h.value / total) * 100).toFixed(1)}%` : "–"),
    el("td", null, h.brokers.map((b) => el("span", { class: "chip" }, b))),
  ], h.accounts.map((a) => el("tr", null,
    el("td", null, `${a.label} ${a.client} · ${a.depository}`),
    el("td", { class: "num" }, qtyFmt.format(a.quantity)),
    el("td"),
    el("td", { class: "num" }, money(a.value)),
    el("td"), el("td")))));
  if (rows.length === 0 && p.stocks.length) {
    rows.push(el("tr", null, el("td", { colspan: "6", class: "empty" }, "No stock is held in more than one account.")));
  }
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
    el("td", null, ISSUERS[s.issuer] || s.issuer),
    el("td", null, s.kind === "mf" ? "Mutual funds" : "Stocks"),
    el("td", null, `${day(s.period_from)} – ${day(s.period_to)}`),
    el("td", null, day(s.imported_at)),
    el("td", null, statementStatus(s)),
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
  if (p.totals.mf_without_cost) alerts.push(`${p.totals.mf_without_cost} fund(s) have no purchase cost on the statement.`);
  return alerts;
}

let lastPortfolio = null;

async function load() {
  try {
    const [portfolio, statements] = await Promise.all([api("/api/portfolio"), api("/api/statements")]);
    lastPortfolio = portfolio;
    renderTotals(portfolio);
    renderAllocation(portfolio);
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

document.getElementById("upload-form").addEventListener("submit", upload);
document.getElementById("multi-only").addEventListener("change", () => lastPortfolio && renderStocks(lastPortfolio));
load();
