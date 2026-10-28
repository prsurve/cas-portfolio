"use strict";

// Target mix and drift on the Performance page, from /api/targets. The targets are saved in the database (PUT),
// never in the browser. Loaded before app.js.

let driftClasses = [];

function driftInputs() {
  return [...document.querySelectorAll("#drift-inputs input")];
}

function driftSum() {
  return driftInputs().reduce((a, i) => a + (Number.isFinite(i.valueAsNumber) ? i.valueAsNumber : 0), 0);
}

function updateDriftSum() {
  const any = driftInputs().some((i) => i.value !== "");
  const sum = driftSum();
  const ok = !any || Math.abs(sum - 100) <= 0.01;
  document.getElementById("drift-sum").textContent = any ? `Total ${Fmt.percent(sum).replace("+", "")}${ok ? "" : ", must be 100%"}` : "";
  document.getElementById("drift-save").disabled = !ok;
}

function renderDrift(data) {
  driftClasses = data.classes;
  const stored = data.targets || {};
  document.getElementById("drift-inputs").replaceChildren(...driftClasses.map((label) => {
    const input = el("input", { type: "number", min: "0", max: "100", step: "any", inputmode: "decimal", "data-class": label });
    input.value = stored[label] === undefined ? "" : String(stored[label]);
    input.addEventListener("input", updateDriftSum);
    return el("label", null, label, " (%) ", input);
  }));
  updateDriftSum();
  const rows = data.drift ? data.drift.rows : [];
  const gap = (n) => (n === null || n === undefined ? Fmt.MISSING : Fmt.percent(n));
  document.querySelector("#drift tbody").replaceChildren(...(rows.length ? rows.map((r) => el("tr", null,
    el("td", null, r.label),
    el("td", { class: "num", "data-label": "Now" }, `${Fmt.percent(r.weight).replace("+", "")}`),
    el("td", { class: "num", "data-label": "Target" }, `${Fmt.percent(r.target).replace("+", "")}`),
    el("td", { class: `num ${signClass(r.gap_pct)}`, "data-label": "Gap (points)" }, gap(r.gap_pct)),
    el("td", { class: `num ${signClass(r.gap_value)}`, "data-label": "Gap in rupees" }, money(r.gap_value)))) :
    [el("tr", null, el("td", { class: "empty", colspan: "5" }, "No targets saved yet."))]));
}

async function loadDrift() {
  try {
    const data = await api("/api/targets");
    document.getElementById("drift-section").hidden = lastPortfolio === null || lastPortfolio.allocation.asset_class.length === 0;
    renderDrift(data);
  } catch (err) {
    document.getElementById("drift-message").textContent = `Could not load targets: ${err.message}`;
  }
}

async function saveTargets(values) {
  const message = document.getElementById("drift-message");
  try {
    renderDrift(await api("/api/targets", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ targets: values }) }));
    message.textContent = Object.keys(values).length ? "Targets saved." : "Targets cleared.";
  } catch (err) {
    message.textContent = `Not saved: ${err.message}`;
  }
}

document.getElementById("drift-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const values = {};
  for (const input of driftInputs()) if (input.value !== "") values[input.dataset.class] = input.valueAsNumber;
  saveTargets(values);
});
document.getElementById("drift-clear").addEventListener("click", () => saveTargets({}));
