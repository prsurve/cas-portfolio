"use strict";

// Search, sort and CSV helpers for the holdings tables. Plain functions with no DOM access, so
// tests/tools.test.js can run them in node. In the page they are exposed as PortfolioTools.
(function (root) {
  const isEmpty = (v) => v === null || v === undefined || v === "" || (typeof v === "number" && Number.isNaN(v));

  // Every whitespace-separated term must appear (case-insensitively) in at least one field.
  // Plain substring search: the query is never treated as a pattern.
  function matchesQuery(fields, query) {
    const terms = String(query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return true;
    const haystack = fields.filter((f) => !isEmpty(f)).join(" ").toLowerCase();
    return terms.every((t) => haystack.includes(t));
  }

  // A sorted copy. Numbers sort numerically, text naturally ("Fund 2" before "Fund 10") and without
  // regard to case. Empty values go last in either direction, and equal values keep their order.
  function sortRows(rows, accessor, dir) {
    const sign = dir === "desc" ? -1 : 1;
    const decorated = rows.map((row, index) => ({ row, index, value: accessor(row) }));
    decorated.sort((a, b) => {
      const ea = isEmpty(a.value), eb = isEmpty(b.value);
      if (ea || eb) return ea && eb ? a.index - b.index : ea ? 1 : -1;
      const c = typeof a.value === "number" && typeof b.value === "number"
        ? a.value - b.value
        : String(a.value).localeCompare(String(b.value), undefined, { numeric: true, sensitivity: "base" });
      return c * sign || a.index - b.index;
    });
    return decorated.map((d) => d.row);
  }

  // One CSV field. Text that starts with = + - @ or a control character is prefixed with an apostrophe,
  // because a spreadsheet would run it as a formula and statement text is not under our control.
  // Numbers are written as they are, so a negative amount stays a number.
  function csvCell(value) {
    if (isEmpty(value)) return "";
    if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
    let text = String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  // columns: [{ header, value(row) }]. CRLF line endings, a header row even when there are no rows.
  function toCsv(columns, rows) {
    const lines = [columns.map((c) => csvCell(c.header)).join(",")];
    for (const row of rows) lines.push(columns.map((c) => csvCell(c.value(row))).join(","));
    return `${lines.join("\r\n")}\r\n`;
  }

  // Calls fn once, `wait` ms after the last call. Used so typing in a search box redraws a table once, not per key.
  function debounce(fn, wait, timers = { set: setTimeout, clear: clearTimeout }) {
    let handle = null;
    return (...args) => {
      if (handle !== null) timers.clear(handle);
      handle = timers.set(() => { handle = null; fn(...args); }, wait);
    };
  }

  // One display rule for numbers and dates. Missing is a dash, never 0, so an unknown value cannot pass for a real one.
  const MISSING = "–";
  const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
  const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  const qty = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });
  const pct1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
  const date = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });
  const missing = (n) => n === null || n === undefined || (typeof n === "number" && !Number.isFinite(n));

  const fmt = {
    MISSING,
    money: (n) => (missing(n) ? MISSING : inr0.format(n)),
    price: (n) => (missing(n) ? MISSING : inr2.format(n)),
    quantity: (n) => (missing(n) ? MISSING : qty.format(n)),
    // A signed percentage such as +4.2%. Missing gives a dash like every other figure.
    percent: (n) => (missing(n) ? MISSING : `${pct1.format(n)}%`),
    // ISO date (or datetime) to "27 Oct 2026". Parsed as a local date so the day never shifts with the time zone.
    // A timestamp such as "27 Oct 2026, 6:30 pm" in the viewer's own time zone.
    dateTime: (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : MISSING),
    day: (iso) => (iso ? date.format(new Date(`${String(iso).slice(0, 10)}T00:00:00`)) : MISSING),
  };

  // "#/holdings" gives { page: "holdings", arg: null }; "#/holdings/INE009A01021" adds the argument (decoded).
  // Anything that is not a known page ("#main", a typo) gives null, so it changes nothing.
  function parseRoute(hash, pages) {
    const m = /^#\/([a-z]+)(?:\/(.*))?$/.exec(String(hash || ""));
    if (!m || !pages.includes(m[1])) return null;
    let arg = null;
    if (m[2]) {
      try { arg = decodeURIComponent(m[2]); } catch (e) { return null; }
    }
    return { page: m[1], arg };
  }

  const api = { matchesQuery, sortRows, csvCell, toCsv, debounce, fmt, parseRoute };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PortfolioTools = api;
})(typeof self !== "undefined" ? self : this);
