"use strict";
// Plain node: no test framework. Run with `node tests/tools.test.js`; prints "ok" or throws.
const assert = require("node:assert/strict");
const T = require("../src/portfolio/static/tools.js");

// ---- search ----------------------------------------------------------------------
assert.equal(T.matchesQuery(["Infosys Limited", "INFY", "INE009A01021"], ""), true);
assert.equal(T.matchesQuery(["Infosys Limited", "INFY"], "  "), true);
assert.equal(T.matchesQuery(["Infosys Limited", "INFY", "INE009A01021"], "infosys"), true);
assert.equal(T.matchesQuery(["Infosys Limited", "INFY", "INE009A01021"], "ine009"), true);
assert.equal(T.matchesQuery(["Infosys Limited", "Zerodha Broking"], "infosys zerodha"), true, "all terms, any field");
assert.equal(T.matchesQuery(["Infosys Limited", "Zerodha Broking"], "infosys upstox"), false, "every term must match");
assert.equal(T.matchesQuery(["Infosys", null, undefined, ""], "infosys"), true, "missing fields are ignored");
assert.equal(T.matchesQuery([null], "x"), false);
assert.equal(T.matchesQuery(["a.b (c)"], "a.b ("), true, "no regex surprises from punctuation");

// ---- sort ------------------------------------------------------------------------
const rows = [{ n: "b", v: 5 }, { n: "a", v: null }, { n: "c", v: 9 }, { n: "d", v: 5 }, { n: "e", v: undefined }];
const names = (list) => list.map((r) => r.n).join("");
assert.equal(names(T.sortRows(rows, (r) => r.v, "desc")), "cbdae", "numbers high to low, ties keep order, empty last");
assert.equal(names(T.sortRows(rows, (r) => r.v, "asc")), "bdcae", "empty last in ascending order too");
assert.equal(names(T.sortRows(rows, (r) => r.n, "asc")), "abcde");
assert.equal(names(T.sortRows(rows, (r) => r.n, "desc")), "edcba");
assert.equal(names(rows), "bacde", "the input is not reordered");
const funds = [{ n: "Fund 10" }, { n: "fund 2" }, { n: "Fund 1" }];
assert.deepEqual(T.sortRows(funds, (r) => r.n, "asc").map((r) => r.n), ["Fund 1", "fund 2", "Fund 10"], "natural, case-insensitive");
assert.deepEqual(T.sortRows([{ v: NaN }, { v: 1 }], (r) => r.v, "asc").map((r) => r.v).map(String), ["1", "NaN"], "NaN counts as empty");
assert.deepEqual(T.sortRows([{ v: -3 }, { v: 2 }, { v: -10 }], (r) => r.v, "asc").map((r) => r.v), [-10, -3, 2]);

// ---- CSV -------------------------------------------------------------------------
assert.equal(T.csvCell("plain"), "plain");
assert.equal(T.csvCell(null), "");
assert.equal(T.csvCell(undefined), "");
assert.equal(T.csvCell(12.5), "12.5");
assert.equal(T.csvCell(-3), "-3", "negative numbers are not text, so they are left alone");
assert.equal(T.csvCell(NaN), "");
assert.equal(T.csvCell(Infinity), "");
assert.equal(T.csvCell("a,b"), '"a,b"');
assert.equal(T.csvCell('say "hi"'), '"say ""hi"""');
assert.equal(T.csvCell("two\nlines"), '"two\nlines"');
// text that a spreadsheet would run as a formula is defused
for (const bad of ["=SUM(A1:A9)", "+1+1", "-2+3", "@cmd", "\tx", "\rx"]) {
  assert.ok(T.csvCell(bad).replace(/^"/, "").startsWith("'"), `formula guard: ${JSON.stringify(bad)}`);
}
assert.equal(T.csvCell("=1,2"), `"'=1,2"`, "guarded and then quoted");
assert.equal(T.csvCell("A-B"), "A-B", "a dash in the middle is fine");

const cols = [{ header: "Name", value: (r) => r.name }, { header: "Qty", value: (r) => r.qty }, { header: "Note, x", value: (r) => r.note }];
const out = T.toCsv(cols, [{ name: "Infosys", qty: 20, note: null }, { name: "=evil()", qty: -1.5, note: 'a "b"' }]);
assert.equal(out, 'Name,Qty,"Note, x"\r\nInfosys,20,\r\n\'=evil(),-1.5,"a ""b"""\r\n');
assert.equal(T.toCsv(cols, []), 'Name,Qty,"Note, x"\r\n', "headers even when there are no rows");

// debounce: many calls inside the wait collapse into one call with the last arguments
{
  const pending = new Map(); let next = 1;
  const timers = { set: (f) => { pending.set(next, f); return next++; }, clear: (h) => pending.delete(h) };
  const fire = () => { const [[h, f]] = [...pending]; pending.delete(h); f(); };
  const seen = [];
  const d = T.debounce((v) => seen.push(v), 100, timers);
  d("a"); d("ab"); d("abc");
  assert.equal(pending.size, 1, "earlier timers are cancelled");
  assert.deepEqual(seen, []);
  fire();
  assert.deepEqual(seen, ["abc"]);
  d("x"); fire();
  assert.deepEqual(seen, ["abc", "x"], "usable again after firing");
}

// debounce with the real timers must not call setTimeout with another object as `this` (browsers throw "Illegal invocation")
{
  const realSet = globalThis.setTimeout, realClear = globalThis.clearTimeout;
  const log = [];
  globalThis.setTimeout = function (f, w) { if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation"); log.push(w); return 7; };
  globalThis.clearTimeout = function (h) { if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation"); log.push(`clear ${h}`); };
  try {
    const d = T.debounce(() => {}, 150);
    d(); d();
    assert.deepEqual(log, [150, "clear 7", 150]);
  } finally { globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear; }
}

// formatters: one rule for missing values, negatives, percentages and dates
{
  const F = T.fmt;
  const nb = (x) => x.replace(/\u00a0/g, " ");  // Intl may use a no-break space
  assert.equal(nb(F.money(1234567.4)), "₹12,34,567", "Indian digit grouping, no decimals");
  assert.equal(nb(F.money(-1500)), "-₹1,500");
  assert.equal(nb(F.money(0)), "₹0", "a real zero stays a zero");
  for (const gap of [null, undefined, NaN, Infinity]) assert.equal(F.money(gap), "–", `missing: ${gap}`);
  assert.equal(nb(F.price(1450.256)), "₹1,450.26");
  assert.equal(F.price(null), "–");
  assert.equal(F.quantity(12.34567), "12.346");
  assert.equal(F.quantity(undefined), "–");
  assert.equal(F.percent(4.2), "+4.2%");
  assert.equal(F.percent(-0.5), "-0.5%");
  assert.equal(F.percent(0), "0%");
  assert.equal(F.percent(null), "–");
  assert.match(F.day("2026-09-30"), /^30 \w+ 2026$/, "day, month name, year");
  assert.equal(F.day("2026-09-30T23:59:59+00:00").slice(0, 2), "30", "time and zone never shift the day");
  assert.equal(F.bytes(0), "0 B");
  assert.equal(F.bytes(999), "999 B");
  assert.equal(F.bytes(2_400_000), "2.4 MB");
  assert.equal(F.bytes(512_000_000_000), "512.0 GB");
  assert.equal(F.bytes(null), "–");
  assert.match(F.dateTime("2026-10-27T18:30:00+00:00"), /2026/);
  assert.equal(F.dateTime(null), "–");
  assert.equal(F.day(null), "–");
  assert.equal(F.day(""), "–");
}

// parseRoute: only known pages count, so "#main" or a typo changes nothing; a second part is the argument
{
  const pages = ["overview", "holdings"];
  assert.deepEqual(T.parseRoute("#/holdings", pages), { page: "holdings", arg: null });
  assert.deepEqual(T.parseRoute("#/holdings/", pages), { page: "holdings", arg: null });
  assert.deepEqual(T.parseRoute("#/holdings/INE009A01021", pages), { page: "holdings", arg: "INE009A01021" });
  assert.deepEqual(T.parseRoute("#/holdings/Some%20Fund%2FPlan", pages), { page: "holdings", arg: "Some Fund/Plan" });
  for (const bad of ["", "#", "#main", "#/nope", "#/Holdings", "#/holdings/%E0%A4%A", null, undefined]) {
    assert.equal(T.parseRoute(bad, pages), null, `ignored: ${bad}`);
  }
}

// remembered settings: anything read back is checked, views are bounded, storage failures are harmless
{
  assert.deepEqual(T.cleanView(null), { cls: "all", amc: "all", multi: false, key: "value", dir: "desc" });
  assert.deepEqual(T.cleanView({ cls: "mf", amc: "HDFC Mutual Fund", multi: true, key: "gain", dir: "asc" }),
    { cls: "mf", amc: "HDFC Mutual Fund", multi: true, key: "gain", dir: "asc" });
  assert.deepEqual(T.cleanView({ cls: "<script>", key: "drop table", dir: "sideways", multi: "yes", amc: "x".repeat(101) }),
    { cls: "all", amc: "all", multi: false, key: "value", dir: "desc" }, "bad values fall back");
  assert.equal(T.cleanView({ query: "secret holding name" }).query, undefined, "search text is never kept");

  assert.equal(T.cleanViewName("  Gold   funds \n"), "Gold funds");
  assert.equal(T.cleanViewName("x".repeat(80)).length, 40);
  assert.equal(T.cleanViewName(null), "");

  let views = T.addView([], "Funds by gain", { cls: "mf", key: "gain", dir: "desc" });
  assert.equal(views.length, 1);
  views = T.addView(views, "funds BY gain", { cls: "all" });
  assert.equal(views.length, 1, "same name ignoring case replaces");
  assert.equal(views[0].view.cls, "all");
  assert.equal(T.addView(views, "   ", {}), null, "an empty name is refused");
  assert.deepEqual(T.removeView(views, "funds BY gain"), []);
  let many = [];
  for (let i = 0; i < 30; i += 1) many = T.addView(many, `view ${i}`, {}) || many;
  assert.equal(many.length, T.MAX_VIEWS, "no more than the limit");
  assert.deepEqual(T.cleanViews("not a list"), []);
  assert.equal(T.cleanViews([{ name: "A" }, { name: "a" }, { name: "" }, null]).length, 1, "duplicates and blanks dropped");

  const data = new Map();
  const fake = { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k),
    get length() { return data.size; }, key: (i) => [...data.keys()][i] };
  const prefs = T.createPrefs(fake);
  assert.equal(prefs.get("x", "fallback"), "fallback");
  prefs.set("view", { a: 1 });
  assert.deepEqual(prefs.get("view", null), { a: 1 });
  assert.ok(data.has("portfolio.view"), "keys are prefixed");
  data.set("portfolio.theme", "dark"); data.set("other-app.setting", "keep me");
  data.set("portfolio.broken", "{not json");
  assert.equal(prefs.get("broken", "ok"), "ok", "unreadable JSON gives the fallback");
  prefs.remove("view");
  assert.equal(prefs.get("view", null), null);
  prefs.clearAll();
  assert.deepEqual([...data.keys()], ["other-app.setting"], "clears this app's keys and nothing else");
  const broken = T.createPrefs(null);
  assert.equal(broken.get("x", 7), 7); broken.set("x", 1); broken.remove("x"); broken.clearAll();  // no storage: no errors
  const throwing = T.createPrefs({ getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } });
  assert.equal(throwing.get("x", 3), 3); throwing.set("x", 1);
}

// ---- glossary ---------------------------------------------------------------------
for (const [k, v] of Object.entries(T.GLOSSARY)) assert.ok(typeof v === "string" && v.length > 20 && v.length < 400, `glossary ${k} is a short sentence`);

console.log("ok");
