"use strict";

// Returns (XIRR) on the Performance page. The numbers come from /api/returns, which is worked out on request, so
// this only asks when the page is showing and the statements have changed. Loaded before app.js.

let lastReturnsKey = null;

const yearly = (fraction) => (fraction === null || fraction === undefined ? Fmt.MISSING : `${Fmt.percent(fraction * 100)} a year`);

function portfolioCard(p, valuationDate) {
  if (p.funds_total === 0) return null;
  if (p.status !== "ok") {
    return el("div", null,
      el("div", { class: "result-title" }, "All funds together: no rate"),
      el("p", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("span", { class: "sr-only" }, "Limitation: "), p.reason));
  }
  const coverage = `${plural(p.funds_included, "fund")} of ${p.funds_total}, ${p.coverage_pct.toFixed(0)}% of fund value`;
  return el("div", null,
    el("div", { class: "tiles" },
      tileEl("Funds that could be worked out, together", yearly(p.xirr), `${coverage}`, signClass(p.xirr)),
      tileEl("Put in", money(p.invested), `since ${day(p.since)}`),
      tileEl("Taken out", money(p.withdrawn)),
      tileEl("Statement value", money(p.value_included), `as of ${day(valuationDate)}`),
      tileEl("Gain on those funds", money(p.gain), null, signClass(p.gain))),
    p.funds_included < p.funds_total
      ? el("p", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("span", { class: "sr-only" }, "Limitation: "),
        `${plural(p.funds_total - p.funds_included, "fund")} could not be worked out and ${p.funds_total - p.funds_included === 1 ? "is" : "are"} left out. ` +
        "See the reasons below. The rate covers only the funds shown with one.")
      : null,
    p.short_history ? el("p", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("span", { class: "sr-only" }, "Limitation: "),
      "Less than a year of history, so an annualised rate can mislead.") : null);
}

function returnRow(r) {
  const title = el("td", null, el("a", { class: "symbol", href: `#/holdings/${encodeURIComponent(r.key)}` }, r.scheme),
    el("div", { class: "subtle" }, [r.amc, r.isin].filter(Boolean).join(" · ")),
    r.status === "ok" ? null : el("div", { class: "limit" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"),
      el("span", { class: "sr-only" }, "Not worked out: "), r.reason));
  if (r.status !== "ok") {
    return el("tr", null, title, ...["Return a year", "Since", "Put in", "Taken out", "Value", "Gain"].map((label) =>
      el("td", { class: label === "Since" ? "" : "num", "data-label": label }, Fmt.MISSING)));
  }
  return el("tr", null, title,
    el("td", { class: `num ${signClass(r.xirr)}`, "data-label": "Return a year" }, yearly(r.xirr),
      r.short_history ? el("div", { class: "subtle" }, `only ${plural(r.days, "day")} of history`) : null),
    el("td", { "data-label": "Since" }, day(r.since)),
    el("td", { class: "num", "data-label": "Put in" }, money(r.invested)),
    el("td", { class: "num", "data-label": "Taken out" }, money(r.withdrawn)),
    el("td", { class: "num", "data-label": "Value" }, money(r.value)),
    el("td", { class: `num ${signClass(r.gain)}`, "data-label": "Gain" }, money(r.gain)));
}

function renderReturns(data) {
  const box = document.getElementById("returns-portfolio");
  const card = portfolioCard(data.portfolio, data.valuation_date);
  box.hidden = card === null;
  if (card) box.replaceChildren(card);
  // Worked-out funds first, best rate first, then the ones that could not be worked out.
  const sorted = Tools.sortRows(data.funds, (r) => (r.status === "ok" ? r.xirr : null), "desc");
  document.querySelector("#returns tbody").replaceChildren(...sorted.map(returnRow));
  document.getElementById("returns-status").textContent = data.funds.length === 0 ? "No mutual fund holdings." : "";
}

// Asks only when the Performance page is showing and the funds' statement has changed since the last answer.
async function loadReturns(force) {
  const section = document.getElementById("returns-section");
  const mf = lastPortfolio && lastPortfolio.statements.mf;
  section.hidden = !mf;
  if (!mf || document.getElementById("page-performance").hidden) return;
  const key = JSON.stringify([mf.id, mf.period_to, mf.imported_at]);
  if (!force && key === lastReturnsKey) return;
  const status = document.getElementById("returns-status");
  status.textContent = "Working it out…";
  try {
    renderReturns(await api("/api/returns"));
    lastReturnsKey = key;  // only a success is remembered
  } catch (err) {
    status.textContent = `Could not work out returns: ${err.message}`;
  }
}
