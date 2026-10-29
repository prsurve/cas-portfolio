"use strict";

// The Imports page: a guided upload (choose, check, save), the list of imported statements with what was read from
// each, and recent activity. Loaded before app.js; it uses app.js's helpers (el, api, money, day, checkItem, load)
// only when its functions run.

// Between the check and the save the file and password wait here, in memory only. Nothing is stored in the page,
// the browser or the server (the server reads the PDF in memory and keeps only parsed rows).
let pendingUpload = null;

const STEPS = ["file", "check", "saved"];

function setStep(step) {
  for (const li of document.querySelectorAll("#steps li")) {
    if (li.dataset.step === step) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
    const done = STEPS.indexOf(li.dataset.step) < STEPS.indexOf(step);
    li.classList.toggle("done", done);
  }
}

const WOULD = {
  new: "This statement is new. Saving adds it.",
  duplicate: "This exact file is already imported, so saving would change nothing.",
  replaces_older: "This file was imported before by an older version of this app. Saving replaces what was stored with a fuller read.",
};

// What the statement contains, in words. Shared by the check and by the saved result.
function summaryLines(r) {
  if (r.kind === "mf") {
    return [el("p", null, `${r.funds} funds in ${r.folios} folios, value ${money(r.total_value)}` +
      (r.total_cost !== null ? `, invested ${money(r.total_cost)}` : "") + `. ${r.transactions} transactions.`)];
  }
  const lines = [el("p", null, `${plural(r.stocks, "stock")}, ${plural(r.funds, "ETF or fund")} and ${plural(r.bonds, "bond")} worth ${money(r.total_value)}, ` +
    `in ${plural(r.accounts.length, "demat account")}.`)];
  if (r.folio_funds) {
    lines.push(el("p", null, `Mutual fund folios section: ${r.folio_funds} holdings worth ${money(r.folio_value)}` +
      (r.folio_cost !== null ? `, invested ${money(r.folio_cost)}` : "") + "."));
  }
  return lines;
}

// Each printed total against the rows that were read. A gap means some rows on the statement were not recognised.
function reconciliationChecks(r) {
  if (r.kind === "mf") {
    return [[true, "This kind of statement prints no account totals, so the read cannot be checked against one.",
      "Check the fund values against the PDF."]];
  }
  const gapNote = "Some rows on the statement were not recognised, so values here may be too low. " +
    "The Overview will list this under Data quality.";
  const checks = r.accounts.map((a) => [a.ok,
    `${a.label} ${a.client} (${a.depository}): read ${money(a.imported_value)} of the printed total ${money(a.statement_total)}.`,
    a.ok ? null : `${money(a.gap)} is missing. ${gapNote}`]);
  if (r.folio_check) {
    const f = r.folio_check;
    checks.push([f.ok, `Mutual fund folios: read ${money(f.imported_value)} of the printed total ${money(f.statement_total)}.`,
      f.ok ? null : `${money(f.gap)} is missing. ${gapNote}`]);
  }
  return checks;
}

// One verdict for a read: complete, read with gaps, or nothing new. A mark and words, never colour alone.
// Class names are written out in full so the stylesheet build can find them.
function outcomeOf(r, problems) {
  if (r.would === "duplicate") return { cls: "result", mark: "i", text: "Nothing new: this exact file is already imported." };
  if (problems) {
    return { cls: "result partial", mark: "!", text: `Read with gaps: ${plural(problems, "thing")} to check. Some rows may be missing, so some values may be too low.` };
  }
  if (r.kind === "mf") {
    return { cls: "result ok", mark: "✓", text: "Read. This kind of statement prints no totals to check against, so compare the fund values with the PDF." };
  }
  return { cls: "result ok", mark: "✓", text: "Read completely. Every printed total matches the rows read." };
}

const problemCount = (r) => reconciliationChecks(r).filter((c) => !c[0]).length + (r.warnings || []).length;

function outcomeLine(o) {
  return el("p", { class: "outcome" }, el("span", { class: "mark", "aria-hidden": "true" }, o.mark), o.text);
}

function renderCheck(r) {
  const checks = reconciliationChecks(r);
  const problems = problemCount(r);
  const duplicate = r.would === "duplicate";
  const outcome = outcomeOf(r, problems);
  const box = document.getElementById("upload-result");
  box.replaceChildren(el("div", { class: outcome.cls },
    outcomeLine(outcome),
    el("div", { class: "result-title" }, `${r.issuer} statement, ${day(r.period_from)} to ${day(r.period_to)}`),
    el("p", { class: duplicate ? "subtle" : "" }, WOULD[r.would]),
    ...summaryLines(r),
    el("h3", null, "Does it add up?"),
    el("ul", { class: "checks" }, checks.map(checkItem)),
    r.warnings.length ? el("div", null, el("h3", null, "Warnings from the reader"),
      el("ul", { class: "checks" }, r.warnings.map((w) => checkItem([false, w])))) : null,
    r.notes.length ? el("div", null, r.notes.map((n) => el("p", { class: "subtle" }, n))) : null,
    el("p", { class: "subtle" }, problems
      ? "You can still save it. Nothing is hidden: the gaps stay listed on the Overview until you upload a statement that reads fully."
      : "Nothing has been saved yet."),
    el("div", { class: "controls" },
      duplicate ? null : el("button", { type: "button", class: "btn primary", id: "save-upload", onclick: saveUpload }, "Save statement"),
      el("button", { type: "button", class: "btn", onclick: discardUpload }, duplicate ? "Done" : "Discard"))));
}

function renderSaved(r) {
  const problems = problemCount(r);
  const outcome = problems
    ? { cls: "result partial", mark: "!", text: `Saved with gaps: ${plural(problems, "thing")} to check. They are listed under Data quality on the Overview.` }
    : { cls: "result ok", mark: "✓", text: r.created ? "Saved. Every printed total matched." : "Already saved. Nothing changed." };
  document.getElementById("upload-result").replaceChildren(el("div", { class: outcome.cls },
    outcomeLine(outcome),
    el("div", { class: "result-title" },
      `${r.created ? "Saved" : "Already saved"}: ${r.issuer} statement, ${day(r.period_from)} to ${day(r.period_to)}`),
    ...summaryLines(r),
    el("p", { class: "subtle" }, "It is listed below. If something looks wrong, open its details or delete it.")));
}

function clearUploadForm() {
  document.getElementById("pdf").value = "";
  document.getElementById("password").value = "";  // the password lives only in pendingUpload now
}

async function previewUpload(event) {
  event.preventDefault();
  const file = document.getElementById("pdf").files[0];
  const password = document.getElementById("password").value;
  if (!file) return;
  const btn = document.getElementById("upload-btn"), box = document.getElementById("upload-result");
  btn.disabled = true;
  btn.textContent = "Checking…";
  box.setAttribute("aria-busy", "true");
  box.replaceChildren(el("div", { class: "result" }, "Reading the statement. A large one can take up to a minute. Nothing is saved yet."));
  try {
    const r = await api("/api/statements/preview", { method: "POST", headers: { "Content-Type": "application/pdf", "X-PDF-Password": password }, body: file });
    pendingUpload = { file, password };
    clearUploadForm();
    setStep("check");
    renderCheck(r);
  } catch (err) {
    pendingUpload = null;
    setStep("file");
    box.replaceChildren(el("div", { class: "result error" },
      el("p", { class: "outcome" }, el("span", { class: "mark", "aria-hidden": "true" }, "×"), "Not read. Nothing was saved."),
      el("p", null, err.message),
      el("div", { class: "subtle" }, "Things to check:"),
      el("ul", null,
        el("li", null, "The password. NSDL and CDSL use your PAN in capitals; CAMS and KFintech use the one you chose."),
        el("li", null, "That it is a CAS PDF from NSDL, CDSL, CAMS or KFintech, not a scanned copy or another report."),
        el("li", null, "That the file is the whole statement, downloaded from the issuer."))));
    document.getElementById("password").value = "";
  } finally {
    box.removeAttribute("aria-busy");
    box.focus();  // so the result or the error is announced
    btn.disabled = false;
    btn.textContent = "Check statement";
  }
}

async function saveUpload() {
  if (!pendingUpload) return;
  const { file, password } = pendingUpload;
  const btn = document.getElementById("save-upload"), box = document.getElementById("upload-result");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const r = await api("/api/statements", { method: "POST", headers: { "Content-Type": "application/pdf", "X-PDF-Password": password }, body: file });
    pendingUpload = null;
    setStep("saved");
    renderSaved(r);
    box.focus();
    await load();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Save statement";
    box.prepend(el("div", { class: "result error" }, `Could not save: ${err.message}`));
  }
}

function discardUpload() {
  pendingUpload = null;
  clearUploadForm();
  setStep("file");
  document.getElementById("upload-result").replaceChildren();
  document.getElementById("pdf").focus();
}

// ---- imported statements ---------------------------------------------------------------------

function statementStatus(s) {
  const notes = [];
  if (s.outdated) notes.push(el("div", { class: "loss" }, "Read by an older version. Upload this PDF again to refresh it."));
  if (s.warnings.length) notes.push(el("div", { class: "loss" }, `${s.warnings.length} warning(s)`));
  return notes.length ? notes : el("span", { class: "status ok" }, el("span", { "aria-hidden": "true" }, "✓ "), "OK");
}

// What was read from one statement and how each printed total compares. Fetched when its row is opened.
async function showStatementDetail(s, cell) {
  cell.replaceChildren(el("p", { class: "subtle", role: "status" }, "Loading…"));
  try {
    const d = await api(`/api/statements/${s.id}`);
    const counts = [plural(d.counts.stocks, "demat row"), plural(d.counts.funds, "fund row"),
      d.counts.transactions ? plural(d.counts.transactions, "transaction") : null].filter(Boolean).join(", ");
    cell.replaceChildren(el("div", null,  // inside el(), which skips null; replaceChildren would print "null"
      el("p", null, `Read by parser version ${d.parser_version}: ${counts}.`),
      d.outdated ? el("p", { class: "loss" }, "A newer version of this app reads more from the same PDF. Upload it again to refresh.") : null,
      d.has_reconciliation
        ? el("ul", { class: "checks" }, d.reconciliation.map((c) => checkItem([c.ok,
          `${c.section === "folios" ? "Mutual fund folios" : `${c.name} ${c.client || ""} (${c.depository})`}: read ${money(c.imported_value)} of the printed total ${money(c.statement_total)}.`,
          c.ok ? null : `${money(c.gap)} is missing.`])))
        : el("p", { class: "subtle" }, "No totals were stored for this statement (it was imported by an older version). Upload the PDF again to check it."),
      d.warnings.length ? el("ul", { class: "checks" }, d.warnings.map((w) => checkItem([false, w]))) : null));
  } catch (err) {
    cell.replaceChildren(el("p", { class: "loss" }, `Could not load the details: ${err.message}`));
  }
}

function renderStatements(list) {
  const rows = list.flatMap((s) => {
    const cell = el("td", { colspan: "6" });
    const detail = el("tr", { class: "lot" }, cell);
    detail.hidden = true;
    let loaded = false;
    const toggle = el("button", { type: "button", "aria-expanded": "false", onclick: (e) => {
      const open = detail.hidden;
      detail.hidden = !open;
      e.currentTarget.setAttribute("aria-expanded", String(open));
      e.currentTarget.textContent = open ? "Hide details" : "Details";
      if (open && !loaded) { loaded = true; showStatementDetail(s, cell); }
    } }, "Details");
    return [el("tr", null,
      el("td", { "data-label": "Issuer" }, ISSUERS[s.issuer] || s.issuer),
      el("td", { "data-label": "Covers" }, s.kind === "mf" ? "Mutual funds" : "Stocks"),
      el("td", { "data-label": "Period" }, `${day(s.period_from)} – ${day(s.period_to)}`),
      el("td", { "data-label": "Imported" }, day(s.imported_at)),
      el("td", { "data-label": "Status" }, statementStatus(s)),
      el("td", { class: "num" }, toggle, " ", deleteButton(s))), detail];
  });
  if (rows.length === 0) {
    rows.push(el("tr", null, el("td", { colspan: "6", class: "empty" }, "Nothing imported yet. Choose a statement above.")));
  }
  document.querySelector("#statements tbody").replaceChildren(...rows);
}

// Two clicks, so a stray one deletes nothing (no browser confirm box); leaving the button cancels.
function deleteButton(s) {
  const btn = el("button", { type: "button", "aria-label": `Delete the ${ISSUERS[s.issuer]} statement to ${day(s.period_to)}` }, "Delete");
  let armed = false;
  const disarm = () => { armed = false; btn.textContent = "Delete"; };
  btn.addEventListener("click", () => {
    if (!armed) { armed = true; btn.textContent = "Click again to delete"; return; }
    disarm();
    removeStatement(s);
  });
  btn.addEventListener("blur", disarm);
  return btn;
}

async function removeStatement(s) {
  try {
    await api(`/api/statements/${s.id}`, { method: "DELETE" });
  } catch (err) {
    showAlerts([`Delete failed: ${err.message}`]);
    return;
  }
  await load();
}

// ---- recent activity -------------------------------------------------------------------------

const EVENT_LABEL = {
  statement_imported: "Statement imported", statement_duplicate: "Same file uploaded again (no change)",
  statement_rejected: "Statement could not be read", statement_deleted: "Statement deleted",
  prices_refreshed: "Prices refreshed", backup_created: "Backup made", prices_failed: "Price refresh failed", data_deleted: "All data deleted",
};
const REJECT_REASON = {
  wrong_password: "The password was wrong.", unreadable: "The file could not be read.",
  unsupported_issuer: "Not a statement type this app reads.", no_period: "No statement period was found in it.",
};

async function loadAudit() {
  const tbody = document.querySelector("#audit tbody");
  try {
    const { events } = await api("/api/audit?limit=30");
    const rows = events.map((e) => el("tr", null,
      el("td", null, Fmt.dateTime(e.ts)),
      el("td", { "data-label": "Event" }, EVENT_LABEL[e.event] || e.event),
      el("td", { "data-label": "Detail" }, (e.event === "statement_rejected" ? REJECT_REASON[e.detail] : null) || e.detail || "")));
    if (rows.length === 0) rows.push(el("tr", null, el("td", { colspan: "3", class: "empty" }, "No activity yet.")));
    tbody.replaceChildren(...rows);
  } catch (err) {
    tbody.replaceChildren(el("tr", null, el("td", { colspan: "3", class: "empty" }, `Could not load activity: ${err.message}`)));
  }
}

function initImports() {
  document.getElementById("upload-form").addEventListener("submit", previewUpload);
  setStep("file");
}
