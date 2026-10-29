"use strict";

// Tax lots on the Performance page. The lots come from /api/lots, worked out on request, so this only asks when the
// page is showing and the funds' statement has changed. Loaded after returns.js, before app.js.

let lastLotsKey = null;

const heldFor = (days) => `${plural(days, "day")}${days > 365 ? ", over a year" : ""}`;

function lotLine(lot) {
  return el("div", { class: "lot-line" },
    el("span", null, `Bought ${day(lot.bought)}`),
    el("span", null, `${Fmt.quantity(lot.units)} units, cost ${money(lot.cost)}`),
    el("span", { class: signClass(lot.gain) }, `now ${money(lot.value)} (${money(lot.gain)})`),
    el("span", { class: "subtle" }, `held ${heldFor(lot.days_held)}`));
}

function saleLines(sale) {
  return [
    el("div", { class: "lot-line" }, el("strong", null, `Sold ${day(sale.date)}`),
      el("span", null, `${Fmt.quantity(sale.units)} units for ${money(sale.proceeds)}`),
      el("span", { class: signClass(sale.gain) }, `gain ${money(sale.gain)}`)),
    ...sale.pieces.map((p) => el("div", { class: "lot-line subtle" },
      el("span", null, `from the lot bought ${day(p.bought)}`),
      el("span", null, `${Fmt.quantity(p.units)} units, cost ${money(p.cost)}`),
      el("span", null, `held ${heldFor(p.days_held)}`),
      el("span", { class: signClass(p.gain) }, `gain ${money(p.gain)}`))),
  ];
}

function lotRow(f) {
  const title = el("td", null, el("a", { class: "symbol", href: `#/holdings/${encodeURIComponent(f.key)}` }, f.scheme),
    el("div", { class: "subtle" }, [f.amc, f.isin].filter(Boolean).join(" · ")),
    f.status === "ok" ? null : el("div", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"),
      el("span", { class: "sr-only" }, "Not worked out: "), f.reason),
    f.status === "ok" && (f.open_lots.length || f.sales.length)
      ? el("details", null, el("summary", null, `${plural(f.open_lots.length, "open lot")}, ${plural(f.sales.length, "sale")}`),
        f.open_lots.length ? el("div", null, el("strong", null, "Open lots"), f.open_lots.map(lotLine)) : null,
        f.sales.length ? el("div", null, el("strong", null, "Sales"), f.sales.map(saleLines)) : null)
      : null);
  const cells = ["Open cost", "Unrealised gain", "Sold for", "Cost of sold units", "Realised gain"];
  if (f.status !== "ok") {
    return el("tr", null, title, ...cells.map((label) => el("td", { class: "num", "data-label": label }, Fmt.MISSING)));
  }
  return el("tr", null, title,
    el("td", { class: "num", "data-label": "Open cost" }, money(f.open_cost)),
    el("td", { class: `num ${signClass(f.unrealised_gain)}`, "data-label": "Unrealised gain" }, money(f.unrealised_gain)),
    el("td", { class: "num", "data-label": "Sold for" }, money(f.realised_proceeds)),
    el("td", { class: "num", "data-label": "Cost of sold units" }, money(f.realised_cost)),
    el("td", { class: `num ${signClass(f.realised_gain)}`, "data-label": "Realised gain" }, money(f.realised_gain)));
}

function renderLots(data) {
  const box = document.getElementById("lots-totals");
  const t = data.totals;
  box.hidden = t.funds_included === 0;
  box.replaceChildren(el("div", null,
    el("div", { class: "tiles" },
      tileEl("Open cost", money(t.open_cost), `${t.funds_included} of ${plural(t.funds_total, "fund")} worked out`),
      tileEl("Unrealised gain", money(t.unrealised_gain), `at the statement value on ${day(data.valuation_date)}`, signClass(t.unrealised_gain)),
      tileEl("Realised gain", money(t.realised_gain), `sold for ${money(t.realised_proceeds)}`, signClass(t.realised_gain))),
    t.funds_included < t.funds_total
      ? el("p", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("span", { class: "sr-only" }, "Limitation: "),
        `${plural(t.funds_total - t.funds_included, "fund")} could not be worked out and ${t.funds_total - t.funds_included === 1 ? "is" : "are"} left out of these totals. See the reasons below.`)
      : null));
  // Funds with figures first, then the ones that could not be worked out.
  const sorted = [...data.funds].sort((a, b) => (a.status === "ok" ? 0 : 1) - (b.status === "ok" ? 0 : 1));
  document.querySelector("#lots tbody").replaceChildren(...sorted.map(lotRow));
  document.getElementById("lots-status").textContent = data.funds.length === 0 ? "No mutual fund holdings." : "";
}

// Asks only when the Performance page is showing and the funds' statement has changed since the last answer.
async function loadLots(force) {
  const section = document.getElementById("lots-section");
  const mf = lastPortfolio && lastPortfolio.statements.mf;
  section.hidden = !mf;
  if (!mf || document.getElementById("page-performance").hidden) return;
  const key = JSON.stringify([mf.id, mf.period_to, mf.imported_at]);
  if (!force && key === lastLotsKey) return;
  const status = document.getElementById("lots-status");
  status.textContent = "Working it out…";
  try {
    renderLots(await api("/api/lots"));
    lastLotsKey = key;  // only a success is remembered
  } catch (err) {
    status.textContent = `Could not work out lots: ${err.message}`;
  }
}
