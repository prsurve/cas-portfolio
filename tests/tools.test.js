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

console.log("ok");
