"use strict";

// The Overview's "How current is this?" and "Data quality" panels, from /api/confidence. Each issue says which figures
// it affects and what to do; each figure says how far it can be trusted. Loaded before app.js.

const STATUS_TEXT = { ok: "Fully backed", partial: "Partly backed", unavailable: "Cannot be worked out" };
const STATUS_MARK = { ok: "✓", partial: "!", unavailable: "–" };

// An issue: the problem, what it touches, and what to do. "note" issues change no figure.
function issueItem(i) {
  const attention = i.severity === "attention";
  return el("li", { class: attention ? "warn" : "ok" },
    el("span", { class: "mark", "aria-hidden": "true" }, attention ? "!" : "i"),
    el("div", null,
      el("span", { class: "sr-only" }, attention ? "Needs attention: " : "Note: "), i.title,
      i.detail ? el("div", { class: "detail" }, i.detail) : null,
      i.affects.length ? el("div", { class: "detail" }, el("strong", null, "Affects: "), i.affects.join(", ")) : null,
      i.action ? el("div", { class: "detail" }, el("strong", null, "What to do: "), i.action) : null));
}

function figureRow(f) {
  return el("tr", null,
    el("td", null, el("span", { class: "symbol" }, f.figure),
      f.caveats.length ? el("ul", { class: "caveats" }, f.caveats.map((c) => el("li", null, c))) : null),
    el("td", { "data-label": "How far to trust it" },
      el("span", { class: `status ${f.status}` }, el("span", { "aria-hidden": "true" }, `${STATUS_MARK[f.status]} `), STATUS_TEXT[f.status])),
    el("td", { "data-label": "Based on" }, f.basis));
}

// Where the data came from and how new it is: one line per source.
function sourceChecks(src) {
  const rows = [];
  for (const [label, s] of [["Stocks and ETFs (demat statement)", src.statements.demat], ["Mutual funds", src.statements.mf]]) {
    if (!s) continue;
    rows.push([!s.stale, `${label}: ${ISSUERS[s.issuer]} statement dated ${day(s.period_to)}, ${plural(s.age_days, "day")} old.`,
      s.stale ? "Older than this app's limit. Upload a newer statement; the values are as of that date." : null]);
  }
  const names = { AMFI: "Fund NAVs (AMFI)", NSE: "NSE closing prices", BSE: "BSE closing prices" };
  for (const p of src.prices) {
    rows.push([true, `${names[p.source] || p.source}: ${plural(p.count, "price")}, newest ${day(p.newest)}` +
      (p.oldest !== p.newest ? `, oldest ${day(p.oldest)}` : "") + `. Fetched ${Fmt.dateTime(p.fetched_at)}.`]);
  }
  if (src.prices.length === 0) rows.push([false, "No latest prices yet.", "Use Refresh prices. Until then every value is a statement value."]);
  else rows.push([true, "Latest prices are shown beside statement values and never added into them.",
    src.last_refresh ? `Last refreshed ${Fmt.dateTime(src.last_refresh)}.` : null]);
  if (src.last_refresh_failed && (!src.last_refresh || src.last_refresh_failed > src.last_refresh)) {
    rows.push([false, `The last price refresh failed (${Fmt.dateTime(src.last_refresh_failed)}).`, "Try Refresh prices again. The public files may be unavailable today."]);
  }
  return rows;
}

function renderConfidence(r) {
  document.getElementById("fresh-section").hidden = r.empty;
  document.getElementById("complete-section").hidden = r.empty;
  if (r.empty) return;
  document.getElementById("freshness").replaceChildren(...sourceChecks(r.sources).map(checkItem));

  const attention = r.issues.filter((i) => i.severity === "attention");
  document.getElementById("quality-card").classList.toggle("has-attention", attention.length > 0);
  document.getElementById("completeness-summary").textContent = attention.length === 0
    ? "All checks passed. Every figure is backed by what the statements printed."
    : `${plural(attention.length, "thing")} to look at. Each says which figures it affects and what to do.`;
  document.getElementById("completeness").replaceChildren(...r.issues.map(issueItem));
  const folded = document.getElementById("completeness-ok");
  folded.hidden = r.passed.length === 0;
  document.getElementById("completeness-ok-summary").textContent = `${r.passed.length} ${r.passed.length === 1 ? "check" : "checks"} passed`;
  document.getElementById("completeness-passed").replaceChildren(...r.passed.map((p) => checkItem([true, p.title])));
  document.querySelector("#figures tbody").replaceChildren(...r.figures.map(figureRow));
}

async function loadConfidence() {
  try {
    renderConfidence(await api("/api/confidence"));
  } catch (err) {
    document.getElementById("completeness-summary").textContent = `Could not check the data: ${err.message}`;
  }
}
