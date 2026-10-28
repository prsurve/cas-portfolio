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
  // The default timers are wrapped, not passed as methods: browsers throw "Illegal invocation" if setTimeout is called
  // with another object as `this`, which node does not (this once left the search box doing nothing).
  function debounce(fn, wait, timers = { set: (f, w) => setTimeout(f, w), clear: (h) => clearTimeout(h) }) {
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
    // File and disk sizes: "2.4 MB". Decimal units, one decimal place above bytes.
    bytes: (n) => {
      if (missing(n)) return MISSING;
      const units = ["B", "KB", "MB", "GB", "TB"];
      let v = n, i = 0;
      while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
      return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`;
    },
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

  // ---- remembered display settings ---------------------------------------------------------------------
  // What the page remembers in this browser: how the holdings list is sorted and filtered, saved views, the two dates
  // being compared, the theme and the last page. Settings only, never holdings, amounts or search text.

  const HOLDING_CLASSES = ["all", "equity", "fund", "bond", "mf"];
  const HOLDING_SORT_KEYS = ["name", "quantity", "price", "value", "gain", "latest", "weight"];
  const MAX_VIEWS = 20;
  const MAX_NAME = 40;

  // Anything read back from storage is checked: a hand-edited or old value falls back to the default.
  function cleanView(raw) {
    const v = raw && typeof raw === "object" ? raw : {};
    return {
      cls: HOLDING_CLASSES.includes(v.cls) ? v.cls : "all",
      amc: typeof v.amc === "string" && v.amc.length > 0 && v.amc.length <= 100 ? v.amc : "all",
      multi: v.multi === true,
      key: HOLDING_SORT_KEYS.includes(v.key) ? v.key : "value",
      dir: v.dir === "asc" ? "asc" : "desc",
    };
  }
  const cleanViewName = (name) => String(name === null || name === undefined ? "" : name).replace(/\s+/g, " ").trim().slice(0, MAX_NAME);

  function cleanViews(raw) {
    if (!Array.isArray(raw)) return [];
    const seen = new Set(), out = [];
    for (const item of raw) {
      const name = cleanViewName(item && item.name);
      if (!name || seen.has(name.toLowerCase())) continue;
      seen.add(name.toLowerCase());
      out.push({ name, view: cleanView(item.view) });
      if (out.length === MAX_VIEWS) break;
    }
    return out;
  }

  // The saved views with one added, or replaced if the name is already used (ignoring case). Null if the name is empty.
  function addView(views, name, view) {
    const clean = cleanViewName(name);
    if (!clean) return null;
    const rest = views.filter((v) => v.name.toLowerCase() !== clean.toLowerCase());
    return cleanViews([...rest, { name: clean, view }]);
  }
  const removeView = (views, name) => views.filter((v) => v.name !== name);

  // Reads and writes JSON under "portfolio.<name>" in a storage object. A missing or blocked storage (private mode)
  // makes every call do nothing and every read give the fallback.
  function createPrefs(storage) {
    const key = (name) => `portfolio.${name}`;
    return {
      get(name, fallback) {
        try {
          const raw = storage.getItem(key(name));
          return raw === null ? fallback : JSON.parse(raw);
        } catch (e) { return fallback; }
      },
      set(name, value) { try { storage.setItem(key(name), JSON.stringify(value)); } catch (e) { /* nothing to do */ } },
      remove(name) { try { storage.removeItem(key(name)); } catch (e) { /* nothing to do */ } },
      // Removes everything this app stored, and nothing else on the same site.
      clearAll() {
        try {
          const mine = [];
          for (let i = 0; i < storage.length; i += 1) if (String(storage.key(i)).startsWith("portfolio.")) mine.push(storage.key(i));
          mine.forEach((k) => storage.removeItem(k));
        } catch (e) { /* nothing to do */ }
      },
    };
  }

  // Short definitions shown next to a heading (help.js). One place, so the wording is the same everywhere.
  const GLOSSARY = {
    sources: "Each figure comes from the statement you uploaded, as of that statement's date. Latest prices from AMFI, NSE and BSE are shown beside them and are never added into the statement total.",
    previous: "The two newest statement dates compared. Each change is split into buying or selling (quantity) and price moves, which add up to the change in value.",
    quality: "What the app could not read or could not check, and which figures it affects. A gap means rows were not recognised, not that the statement is wrong.",
    allocation: "Share of the statement total by asset class. Values are as of each statement's date, so stocks and funds may be from different days.",
    concentration: "How much of the total sits in one position or issuer. Funds are not looked through to the shares inside them.",
    returns: "XIRR is the yearly rate at which the money put in, the money taken out and the statement value balance. It covers only the history the statement shows, so it is not a fund's published return, and a fund with incomplete history is skipped, not estimated.",
    history: "Total value held at each statement date. It rises with new money as well as growth, so it is not a return.",
    changes: "Compares two statement dates. Quantity effect plus price effect equals the change in value.",
  };

  const api = { GLOSSARY, matchesQuery, sortRows, csvCell, toCsv, debounce, fmt, parseRoute, cleanView, cleanViews, cleanViewName, addView, removeView, createPrefs, MAX_VIEWS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PortfolioTools = api;
})(typeof self !== "undefined" ? self : this);
