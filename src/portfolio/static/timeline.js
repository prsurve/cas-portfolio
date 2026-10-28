"use strict";

// The Timeline section of the Performance page, from /api/timeline. Read-only. Loaded before app.js.

const TIMELINE_STEP = 50;
let timelineEvents = [];
let timelineShown = TIMELINE_STEP;

function timelineRow(e) {
  if (e.kind === "statement") {
    const what = e.statement_kind === "demat" ? "holdings" : "mutual funds";
    return el("tr", null, el("td", null, day(e.date)), el("td", { "data-label": "Event" }, e.title),
      el("td", { "data-label": "Detail" }, `${ISSUERS[e.issuer] || e.issuer} statement of ${what}, from ${day(e.period_from)}`),
      el("td", { class: "num", "data-label": "Amount" }, Fmt.MISSING));
  }
  const units = e.units === null || e.units === undefined ? "" : `, ${Fmt.quantity(Math.abs(e.units))} units`;
  return el("tr", null, el("td", null, day(e.date)), el("td", { "data-label": "Event" }, e.title),
    el("td", { "data-label": "Detail" }, `${e.scheme || Fmt.MISSING}${units}`),
    el("td", { class: "num", "data-label": "Amount" }, e.amount === null || e.amount === undefined ? Fmt.MISSING : Fmt.price(e.amount)));
}

function drawTimeline() {
  const filter = document.getElementById("timeline-filter").value;
  const list = timelineEvents.filter((e) => filter === "all" || e.kind === filter);
  const shown = list.slice(0, timelineShown);
  document.querySelector("#timeline tbody").replaceChildren(...(shown.length ? shown.map(timelineRow) :
    [el("tr", null, el("td", { class: "empty", colspan: "4" }, "Nothing to show."))]));
  document.getElementById("timeline-count").textContent = list.length ? `Showing ${shown.length} of ${list.length}` : "";
  document.getElementById("timeline-more").hidden = shown.length >= list.length;
}

async function loadTimeline() {
  try {
    const data = await api("/api/timeline?limit=2000");
    timelineEvents = data.events;
    document.getElementById("timeline-section").hidden = timelineEvents.length === 0;
    drawTimeline();
  } catch (err) {
    document.getElementById("timeline-count").textContent = `Could not load the timeline: ${err.message}`;
  }
}

document.getElementById("timeline-filter").addEventListener("change", () => { timelineShown = TIMELINE_STEP; drawTimeline(); });
document.getElementById("timeline-more").addEventListener("click", () => { timelineShown += TIMELINE_STEP; drawTimeline(); });
