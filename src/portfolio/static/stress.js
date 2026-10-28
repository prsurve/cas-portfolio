"use strict";

// What-if on the Performance page: a percentage per asset class applied to statement values (PortfolioTools.stressTest).
// The numbers typed live in memory only and are never stored. Loaded before app.js.

const stressShocks = {};
let stressClasses = [];

function renderStress(p) {
  stressClasses = (p.allocation.asset_class || []).map((r) => ({ label: r.label, value: r.value }));
  document.getElementById("stress-section").hidden = stressClasses.length === 0;
  drawStress();
}

function drawStress() {
  const result = PortfolioTools.stressTest(stressClasses, stressShocks);
  const body = document.querySelector("#stress tbody");
  const cells = (r, input) => [
    el("td", null, r.label),
    el("td", { class: "num", "data-label": "Now" }, money(r.value)),
    el("td", { class: "num", "data-label": "Change in price (%)" }, input),
    el("td", { class: "num", "data-label": "After" }, money(r.after)),
    el("td", { class: `num ${signClass(r.change)}`, "data-label": "Difference" }, money(r.change)),
  ];
  body.replaceChildren(...result.rows.map((r) => {
    const input = el("input", { type: "number", step: "1", min: "-100", max: "1000", inputmode: "decimal", class: "shock",
      "aria-label": `Change in price for ${r.label}, percent` });
    input.value = stressShocks[r.label] === undefined ? "" : String(stressShocks[r.label]);
    input.addEventListener("input", () => {
      const n = input.valueAsNumber;
      if (Number.isFinite(n)) stressShocks[r.label] = n; else delete stressShocks[r.label];
      updateStressOutputs();
    });
    return el("tr", { "data-class": r.label }, cells(r, input));
  }));
  updateStressOutputs();
}

// Refresh the figures without rebuilding the inputs, so typing is not interrupted.
function updateStressOutputs() {
  const result = PortfolioTools.stressTest(stressClasses, stressShocks);
  document.querySelectorAll("#stress tbody tr").forEach((tr, i) => {
    const r = result.rows[i];
    const tds = tr.querySelectorAll("td");
    tds[3].textContent = money(r.after);
    tds[4].textContent = money(r.change);
    tds[4].className = `num ${signClass(r.change)}`;
  });
  const any = result.rows.some((r) => r.shock !== 0);
  document.getElementById("stress-summary").textContent = !any
    ? `Total now ${money(result.total)}. Enter a change to see the effect.`
    : `Total ${money(result.total)} becomes ${money(result.after)}: ${money(result.change)} (${Fmt.percent(result.changePct)}). An assumption, not a prediction.`;
}

document.getElementById("stress-preset").addEventListener("click", () => {
  stressClasses.forEach((c) => { stressShocks[c.label] = -20; });
  drawStress();
});
document.getElementById("stress-reset").addEventListener("click", () => {
  Object.keys(stressShocks).forEach((k) => delete stressShocks[k]);
  drawStress();
});
