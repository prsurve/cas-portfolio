"use strict";

// The Reports page: a printable summary built from the loaded dashboard, and saved snapshots (/api/snapshots).
// A snapshot is saved in the database, not in the browser. Loaded before app.js.

const asOfText = (a) => (!a ? Fmt.MISSING : a.from === a.to ? day(a.to) : `${day(a.from)} to ${day(a.to)}`);

function renderReport(p) {
  const body = document.getElementById("report-body");
  if (!p || (!p.statements.demat && !p.statements.mf)) {
    body.replaceChildren(el("p", { class: "subtle" }, "Add a statement to see a report."));
    return;
  }
  const t = p.totals;
  const facts = [["Total value", money(t.value), asOfText(p.as_of)]];
  if (p.statements.demat) facts.push(["Demat holdings", money(t.demat_value), `as of ${day(p.statements.demat.period_to)}`]);
  if (p.statements.mf) facts.push(["Mutual fund folios", money(t.mf_value), `as of ${day(p.statements.mf.period_to)}`]);
  const rows = holdingRows(p).sort((a, b) => b.value - a.value);
  body.replaceChildren(
    el("h3", null, "Portfolio report"),
    el("p", { class: "subtle" }, `Prepared ${day(new Date().toISOString().slice(0, 10))} from your uploaded statements. Statement values, as of the dates shown.`),
    el("dl", { class: "facts" }, facts.map(([k, v, d]) => [el("dt", null, k), el("dd", null, `${v} `, el("span", { class: "subtle" }, d))])),
    el("h3", null, "By asset class"),
    el("table", { class: "cards" },
      el("thead", null, el("tr", null, el("th", { scope: "col" }, "Asset class"), el("th", { scope: "col", class: "num" }, "Value"), el("th", { scope: "col", class: "num" }, "Share"))),
      el("tbody", null, p.allocation.asset_class.map((r) => el("tr", null, el("td", null, r.label),
        el("td", { class: "num", "data-label": "Value" }, money(r.value)), el("td", { class: "num", "data-label": "Share" }, Fmt.percent(r.weight).replace("+", "")))))),
    el("h3", null, `Holdings (${rows.length})`),
    el("table", { class: "cards" },
      el("thead", null, el("tr", null, el("th", { scope: "col" }, "Holding"), el("th", { scope: "col" }, "Type"), el("th", { scope: "col", class: "num" }, "Value"), el("th", { scope: "col", class: "num" }, "Share"))),
      el("tbody", null, rows.map((h) => el("tr", null, el("td", null, h.name),
        el("td", { "data-label": "Type" }, CLASS_LABEL[h.cls] || h.cls),
        el("td", { class: "num", "data-label": "Value" }, money(h.value)),
        el("td", { class: "num", "data-label": "Share" }, h.weight === null ? Fmt.MISSING : Fmt.percent(h.weight).replace("+", "")))))),
    el("p", { class: "subtle" }, "Latest market prices are not included. Anything the statements did not show is marked with a dash, never estimated. This is not tax or investment advice."));
}

// ---- snapshots ------------------------------------------------------------------

let pendingDeleteId = null;

async function loadSnapshots() {
  try {
    const data = await api("/api/snapshots");
    const body = document.querySelector("#snapshots tbody");
    body.replaceChildren(...(data.snapshots.length ? data.snapshots.map((s) => snapshotRow(s)) :
      [el("tr", null, el("td", { class: "empty", colspan: "5" }, "No snapshots yet."))]));
  } catch (err) {
    document.getElementById("snap-message").textContent = `Could not load snapshots: ${err.message}`;
  }
}

function snapshotRow(s) {
  const view = el("button", { type: "button" }, "Compare with now");
  view.setAttribute("aria-label", `Compare the snapshot saved ${day(s.taken_at.slice(0, 10))} with now`);
  view.addEventListener("click", () => compareSnapshot(s.id));
  const del = el("button", { type: "button" }, "Delete");
  del.addEventListener("click", async () => {
    if (pendingDeleteId !== s.id) {  // two clicks, so a stray one deletes nothing
      pendingDeleteId = s.id;
      del.textContent = "Click again to delete";
      return;
    }
    pendingDeleteId = null;
    try {
      await api(`/api/snapshots/${s.id}`, { method: "DELETE" });
      document.getElementById("snap-compare").hidden = true;
      document.getElementById("snap-message").textContent = "Snapshot deleted.";
      loadSnapshots();
    } catch (err) {
      document.getElementById("snap-message").textContent = `Not deleted: ${err.message}`;
    }
  });
  del.addEventListener("blur", () => { if (pendingDeleteId === s.id) { pendingDeleteId = null; del.textContent = "Delete"; } });
  return el("tr", null,
    el("td", null, day(s.taken_at.slice(0, 10))),
    el("td", { "data-label": "Note" }, s.note || Fmt.MISSING),
    el("td", { "data-label": "Statement dates" }, asOfText(s.as_of_to ? { from: s.as_of_from, to: s.as_of_to } : null)),
    el("td", { class: "num", "data-label": "Total" }, money(s.total)),
    el("td", { class: "actions" }, view, " ", del));
}

async function compareSnapshot(id) {
  const box = document.getElementById("snap-compare");
  try {
    const snap = await api(`/api/snapshots/${id}`);
    const then = snap.view, now = lastPortfolio;
    const labels = [...new Set([...then.allocation.asset_class, ...(now ? now.allocation.asset_class : [])].map((r) => r.label))];
    const at = (p, label) => (p ? p.allocation.asset_class.find((r) => r.label === label)?.value ?? 0 : 0);
    const nowTotal = now ? now.totals.value : 0;
    const line = (label, a, b) => el("tr", null, el("td", null, label),
      el("td", { class: "num", "data-label": "Then" }, money(a)), el("td", { class: "num", "data-label": "Now" }, money(b)),
      el("td", { class: `num ${signClass(b - a)}`, "data-label": "Difference" }, money(b - a)));
    box.replaceChildren(
      el("h3", null, `Snapshot saved ${day(snap.taken_at.slice(0, 10))} compared with now`),
      el("p", { class: "subtle" }, `Then: statements ${asOfText(then.as_of)}. Now: statements ${asOfText(now && now.as_of)}. ` +
        "Statement values. A difference includes money you added or took out as well as price moves, so it is not a return."),
      el("table", { class: "cards" },
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "Asset class"), el("th", { scope: "col", class: "num" }, "Then"), el("th", { scope: "col", class: "num" }, "Now"), el("th", { scope: "col", class: "num" }, "Difference"))),
        el("tbody", null, [...labels.map((l) => line(l, at(then, l), at(now, l))), line("Total", then.totals.value, nowTotal)])));
    box.hidden = false;
    box.focus();
  } catch (err) {
    document.getElementById("snap-message").textContent = `Could not open the snapshot: ${err.message}`;
  }
}

document.getElementById("snap-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.getElementById("snap-message");
  const note = document.getElementById("snap-note");
  try {
    await api("/api/snapshots", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: note.value }) });
    note.value = "";
    message.textContent = "Snapshot saved.";
    loadSnapshots();
  } catch (err) {
    message.textContent = `Not saved: ${err.message}`;
  }
});
document.getElementById("report-print").addEventListener("click", () => window.print());
