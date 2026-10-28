"use strict";

// The Settings page: health, backups, what is stored, delete-all, and display. Loaded before app.js; it uses
// app.js's helpers (el, api, plural, checkItem, load) only when its functions run.

const DELETE_PHRASE = "delete all data";

function factRow(label, value) {
  return [el("dt", null, label), el("dd", null, value)];
}

function renderHealth(h) {
  document.getElementById("health-summary").textContent = h.attention.length === 0
    ? "Everything looks fine." : `${plural(h.attention.length, "thing")} to look at.`;
  document.getElementById("health-attention").replaceChildren(...h.attention.map((a) => checkItem([false, a])));
  const d = h.database;
  const free = h.disk ? `${Fmt.bytes(h.disk.free_bytes)} free of ${Fmt.bytes(h.disk.total_bytes)}` : Fmt.MISSING;
  document.getElementById("health-facts").replaceChildren(...[
    factRow("Database", `${d.engine}, ${d.ok ? "passed its quick check" : "failed its quick check"}`),
    d.location ? factRow("Location", d.location) : [],
    factRow("Size", Fmt.bytes(d.size_bytes)),
    factRow("Schema version", d.schema_version === null ? Fmt.MISSING : String(d.schema_version)),
    factRow("Disk space", free),
    factRow("Latest prices", !h.prices.fetching ? "turned off (PORTFOLIO_FETCH_PRICES=false)"
      : h.prices.last_refresh ? `last refreshed ${Fmt.dateTime(h.prices.last_refresh)}` : "never refreshed"),
  ].flat());
  const s = h.stored;
  document.getElementById("stored-counts").textContent =
    `${plural(s.statements, "statement")}, ${s.demat_rows} demat and ${s.fund_rows} mutual fund holding rows, ` +
    `${plural(s.transactions, "transaction")}, ${plural(s.prices, "price")}, ${plural(s.snapshots, "saved snapshot")}, and ${s.audit_events === 1 ? "1 activity log entry" : `${s.audit_events} activity log entries`}.`;
  document.getElementById("stored-where").textContent = d.location ? `in the file ${d.location}, on this machine` : "in your database, on this machine";
}

function renderBackups(list, h) {
  const b = h.backups;
  document.getElementById("backups-summary").textContent =
    (b.count === 0 ? "No backups yet." : `${plural(b.count, "backup")}, the newest ${b.newest_days === 0 ? "from today" : `${plural(b.newest_days, "day")} old`}.`) +
    ` ${b.encrypted ? "Encrypted with your gpg key." : "Not encrypted (set PORTFOLIO_BACKUP_GPG_RECIPIENT to encrypt)."} ` +
    `Keeps the newest ${b.keep} in ${b.folder}.`;
  const rows = list.backups.map((x) => el("tr", null,
    el("td", null, x.name),
    el("td", { class: "num", "data-label": "Size" }, Fmt.bytes(x.size_bytes)),
    el("td", { "data-label": "Made" }, Fmt.dateTime(x.modified))));
  if (rows.length === 0) rows.push(el("tr", null, el("td", { colspan: "3", class: "empty" }, "Nothing here yet.")));
  document.querySelector("#backups tbody").replaceChildren(...rows);
}

async function loadSettings() {
  try {
    const [health, backups] = await Promise.all([api("/api/health"), api("/api/backups")]);
    renderHealth(health);
    renderBackups(backups, health);
  } catch (err) {
    document.getElementById("health-summary").textContent = `Could not load this page's data: ${err.message}`;
  }
}

async function backUpNow() {
  const btn = document.getElementById("backup-now"), status = document.getElementById("backup-status");
  btn.disabled = true;
  status.textContent = "Backing up…";
  try {
    const r = await api("/api/backups", { method: "POST" });
    status.textContent = `Saved ${r.name}.`;
    await loadSettings();
    loadAudit();
  } catch (err) {
    status.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ---- delete all data: a dialog with a typed phrase, not a prompt ----------------------------------

function openDeleteDialog() {
  const dialog = document.getElementById("delete-dialog"), input = document.getElementById("delete-phrase");
  input.value = "";
  document.getElementById("delete-confirm").disabled = true;
  dialog.showModal();
  input.focus();
}

async function deleteAllData() {
  const dialog = document.getElementById("delete-dialog"), status = document.getElementById("delete-status");
  const btn = document.getElementById("delete-confirm");
  btn.disabled = true;
  try {
    const r = await api("/api/data", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: DELETE_PHRASE }) });
    dialog.close();
    status.textContent = r.backup ? `Deleted. A backup was saved first as ${r.backup}.` : "Deleted.";
    await load();
    loadSettings();
  } catch (err) {
    dialog.close();
    status.textContent = `Nothing was deleted: ${err.message}`;
  }
}

// ---- display ------------------------------------------------------------------------------------------

function syncThemeRadios() {
  for (const radio of document.querySelectorAll('input[name="theme"]')) radio.checked = radio.value === window.Theme.get();
}

function initSettings() {
  document.getElementById("backup-now").addEventListener("click", backUpNow);
  document.getElementById("delete-data").addEventListener("click", openDeleteDialog);
  document.getElementById("delete-phrase").addEventListener("input", (e) => {
    document.getElementById("delete-confirm").disabled = e.target.value.trim() !== DELETE_PHRASE;
  });
  document.getElementById("delete-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (document.getElementById("delete-phrase").value.trim() === DELETE_PHRASE) deleteAllData();
  });
  document.getElementById("delete-cancel").addEventListener("click", () => document.getElementById("delete-dialog").close());
  for (const radio of document.querySelectorAll('input[name="theme"]')) {
    radio.addEventListener("change", () => window.Theme.set(radio.value));
  }
  document.getElementById("reset-display").addEventListener("click", () => {
    prefs.clearAll();  // everything this app keeps in the browser, and nothing else on the site
    window.Theme.set("system");
    syncThemeRadios();
    resetHoldingsView();
    document.getElementById("display-status").textContent = "Display settings reset.";
  });
  syncThemeRadios();
}
