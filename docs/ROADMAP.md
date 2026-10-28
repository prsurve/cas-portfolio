# Product roadmap

Planning document only. It replaces the "Known gaps" list in CLAUDE.md once agreed. No application code has changed.
All figures and examples are synthetic.

Legend. Priority: P0 integrity/privacy/security/blocker, P1 core, P2 advanced, P3 experimental.
Effort: S under a day, M 1-3 days, L about a week, XL more. Status: **Done**, **Partial**, **Planned** (already on the old list), **New**.

---

## 1. What the repository actually contains (verified by reading it)

### Already implemented (do not rebuild)

| Area | Evidence |
|---|---|
| Statement import (NSDL, CDSL, CAMS, KFintech), in-memory, password in header | `POST /api/statements`, `importers/cas.py` |
| Re-import by file hash and `PARSER_VERSION` | `store.save`, `statements.parser_version` |
| Reconciliation against printed totals, stored per statement | `statement_accounts`, `completeness` in the view |
| No double counting: one demat statement, one fund statement | `compute_view()` |
| Mixed-date warning | `as_of`, `mixed`, `MIXED_DATES_AFTER_DAYS` |
| Public prices: AMFI NAV, NSE/BSE close, ISIN matched, never in `as_of` views | `prices.py`, `bhavcopy.py`, `refresh.py` |
| **B1 change detective** (two dates, quantity vs price effect, sums exactly) | `changes.py`, `GET /api/changes`, Changes section |
| Value history per statement date | `history.py`, `GET /api/history` |
| **B4 confidence** (partial): accounts that do not add up, funds without cost/AMC, incomplete history, stocks without symbol | `_completeness`, Data completeness section |
| Concentration with flags | `_concentration`, Concentration section |
| Search, sort, type filter, CSV export (formula-guarded) for both tables | `tools.js`, `app.js` |
| Phone layout (card tables) | `class="cards"`, `data-label`, tests |
| Light and dark theme via `prefers-color-scheme` | `app.css` |
| Validated chart palette, pinned by a test | `tests/test_static_rules.py` |
| Backup/restore with integrity check, optional gpg | `backup.py`, `/healthz` reports backup age |
| **Privacy dashboard** (partial): export all, delete all with typed phrase | `GET /api/export`, `DELETE /api/data`, "Your data" section |
| Personal-data guard script and tests | `scripts/privacy_check.py`, `tests/test_privacy.py`, `scripts/install_hooks.sh` |
| Frozen-database migration test | `tests/test_schema.py` |
| Audit log (events only) | `audit_log` table |

### Corrections to CLAUDE.md "Known gaps"

- Item 6 (personal-data guard) is **done**: script, tests and hook installer exist. What is missing is that the hooks are **not installed in this clone** (`.git/hooks` has only samples, no `core.hooksPath`) and there is **no CI** (`.github/` does not exist). That is the real gap, so it becomes F-01.
- Item 3 is only partly accurate. `mf_transactions` and `open_units`/`history_from` are stored, but nothing computes XIRR or realised gains yet. The UI only says "Returns (XIRR) need it".
- Item 13 is housekeeping, not a roadmap item.

### Not present (verified by search)

No `localStorage`, no routing or navigation, no XIRR, no lot logic, no holding detail view, no per-holding history, no import preview (the upload route parses and saves in one call), no import audit page (the audit log is written but never shown), no health page (only `/healthz` JSON), no notes, targets, goals, stress test, FX, or report snapshots.

### Added by the second audit (backend and frontend, read from source)

- The audit log is written (imports, rejects, deletes, price refreshes) but **no endpoint reads it**.
- `POST /api/statements` parses and saves in one call, but `parse_pdf_bytes` and `save` are already separate functions in `cas.py`, so F-02 is a small change.
- `mf_transactions` are stored but **no route exposes them** except `/api/export`.
- Only the newest price per (ISIN, source) is kept, so there is **no price history**. A holding detail page can show statement values over time, not daily prices.
- The privacy guard script exists, but hooks are not installed in this clone and `.github/` does not exist.
- Three local branches were not inspected: `docs/claude-md`, `feat/readonly-data-layer`, `feat/statement-dashboard`. They are not merged and must not be pushed.

### Not yet inspected

I have read the HTML and the function list of `app.js`, and the CSS variables and rules that tests pin. I have **not** opened the page in a browser in this session, so the UI audit below is from source, not from looking at it. Section 3 says so where it matters. Visual checks are the first task of the UI phase.

---

## 2. Principles that constrain every item

Privacy rule, statement-only, `Decimal`, no double counting, statement value vs latest value kept apart, nothing estimated shown as verified, localhost bind, strict CSP (no `innerHTML`, no inline style), CSRF header, Pi/ARM64/Python 3.13, synthetic tests. **No new runtime dependency unless a row below justifies it.** No frontend framework: the page is about 760 lines of JS with a tested `el()` helper, and the CSP rules fit it well. Re-evaluate only if the page count passes about eight and shared state becomes hard to follow.

---

## 3. UI/UX audit (from source)

Concrete findings, each checkable in the files named.

1. **One long page, 11 sections, no navigation.** `index.html` stacks upload, totals, prices, completeness, history, changes, allocation, concentration, funds, stocks, statements, data. The user scrolls past upload and prices to reach holdings. No anchors, no sticky header, no current-section cue.
2. **Landing order is wrong for a returning user.** Upload is first; the portfolio total comes second. After the first import the upload form is rarely needed.
3. **Upload is a single step with no preview.** The route saves immediately. The user cannot see ignored rows, reconciliation, or duplicates before saving. After saving, `renderUploadResult` shows a summary.
4. **No holding detail.** A holding exists as a table row. Nothing shows its history across statements, its source statement, per-account split beyond an expandable row, or how its numbers were derived.
5. **Two separate holdings tables with different columns and filters.** Funds have search and sort but no type or AMC filter; stocks have type and "multiple accounts". No single holdings list, no asset-class filter across both, no filter by source.
6. **No saved preferences.** Sort order, filters and theme choice reset on reload. `prefers-color-scheme` follows the OS only; there is no override.
7. **Focus styles are partial.** `app.css` line 52 covers `button`, `.btn` and `input`. `select`, `a`, `summary` and table sort buttons rely on the browser default or their own rule. Needs a keyboard pass (unverified in a browser).
8. **Help is prose paragraphs, not attached to terms.** "Statement value", "gain", "at latest price" and "Returns (XIRR)" are explained in running text or not at all.
9. **Import history is invisible.** Parser version, warnings and "outdated" appear in the statements table, but the audit log, per-statement reconciliation detail and ignored rows are not browsable.
10. **No empty state for a new install** was found in the HTML: every data section is `hidden`, so a fresh install shows an upload card and an empty totals area. A guided first-run would help. (Unverified visually.)
11. **Further findings from the second audit (source only):**
    - No `<main>`, `<nav>` or skip link (`index.html`).
    - No focus ring on `select` or on expandable rows `tr.holding[tabindex]` (`app.css:52`).
    - `.sr-only` lacks `white-space: nowrap`.
    - Search re-renders the full table on every keystroke with no debounce (`app.js` around 748-750), and `setupChanges` refetches `/api/changes` on every `load()`.
    - A failed statement delete still calls `load()` (`app.js` around 708-711).
    - Stale text "a later feature" about prices (`app.js:231`), although prices exist.
    - Destructive actions use `confirm()` and `prompt()` (`app.js` around 705, 716).
    - Upload has no drag-and-drop, no progress beyond "Reading…", and errors are not moved into focus.
    - One breakpoint only (640px); no tablet layout.
    - White text on the `--series-2` and `--series-3` segments is marginal, and contrast of text tokens is not tested.
    - Tooltips are `title` attributes only, unreachable by touch and keyboard.
12. **Strengths to keep:** accessible table markup (`scope`, `sr-only`), `aria-live` regions, the card-table pattern, restrained charts, dates beside figures, completeness checks.

---

## 4. Proposed information architecture

Single-page app with hash routing (`#/overview`), no framework. One `app.js` split into small modules loaded as plain scripts (`nav.js`, `overview.js`, `holdings.js`, `imports.js`, ...), each exposing render functions that use `el()`. DOM-free logic stays in `tools.js` and is tested with node. Pages are lazy: a page fetches its data on first visit and nothing renders offscreen.

```
Overview            landing: value, allocation, changes, data quality, quick actions
Holdings            one list: all asset classes, filters, search
  └ Holding detail  #/holdings/<isin>
Performance         history, comparison, returns (when data allows)
Transactions        MF transactions, timeline (later: lots)
Imports             guided upload, statement list, audit trail, reconciliation
Reports             exports, snapshots (later)
Settings            data & privacy, backups, prices/sources, display, health
```

Rules: seven items on desktop sidebar; on phones a bottom bar with Overview, Holdings, Imports, More (Performance, Transactions, Reports, Settings). Hide Transactions and Reports until they have content (do not ship empty pages). Concentration and Completeness are **not** pages: they are Overview panels that link to the filtered Holdings list. Search is a field in the Holdings header, not a palette (a palette does not pay off with about 8 pages).

### Desktop wireframe (Overview)

```
┌ sidebar ┐ ┌ Overview ───────────────────────────────────── [Upload] [Refresh prices] ┐
│ Overview│ │ Total (statement value)  as of 2026-09-30   Stocks 12 Aug · Funds 30 Sep │
│ Holdings│ │ Latest market value (priced 38 of 41)  27 Oct, NSE/BSE/AMFI              │
│ Perform.│ │ ┌ Allocation (stacked bar + table) ┐ ┌ Since previous statement ───────┐ │
│ Txns    │ │ │                                   │ │ +added −removed ~changed, top 5 │ │
│ Imports │ │ └───────────────────────────────────┘ └─────────────────────────────────┘ │
│ Reports │ │ ┌ Data quality: 3 issues → fix ───┐ ┌ Concentration: 1 flag → view ──────┐ │
│ Settings│ │ └─────────────────────────────────┘ └────────────────────────────────────┘ │
└─────────┘ └──────────────────────────────────────────────────────────────────────────┘
```

### Mobile wireframe (Overview)

```
[Overview]            [⋯]
Total  ₹ x,xx,xxx
as of 30 Sep · mixed dates ⚠
[Upload] [Refresh]
Allocation (stacked bar)
  Stocks      xx%
  Funds       xx%
Since last statement ▸
Data quality: 3 issues ▸
Concentration: 1 flag ▸
─────────────────────────
Overview Holdings Imports More
```

### Key workflows

1. **First run:** empty Overview shows three steps (what to download, upload, check result) and a link to a synthetic demo (F-24).
2. **Upload:** Imports → pick file + password → preview (counts, ignored rows, reconciliation, duplicate/outdated notice) → Save or Discard → summary with links to the issues.
3. **Check a number:** Holdings → row → detail → "How is this calculated?" lists statement, date, quantity, price, method.
4. **Fix data quality:** Overview issue → filtered Holdings or Imports row with the reason and the action (re-upload which PDF).

---

## 5. Design system plan

Keep the existing CSS variables and extend them as tokens; do not add a CSS framework.

- **Tokens:** spacing scale (4/8/12/16/24/32), type scale (3 sizes plus tabular numerals for money), radius, border, `--surface-2` for nested panels. Existing colour variables stay; the four `--series-*` colours stay pinned.
- **Components (all built with `el()`):** `Page` (title, breadcrumb, actions), `Tile`, `Card`, `Table` (sortable, cards on phones, empty/loading/error rows), `FilterBar` (search + chips + saved view), `Badge` (text + icon, never colour alone), `Banner` (info/warn/error/success), `Dialog` (native `<dialog>`; replaces `confirm`/typed-phrase prompts), `Tooltip`/`Help` (a button that toggles an inline `<details>`, works on touch and keyboard), `Stepper` for import, `Money`/`Pct`/`DateLabel` formatters in `tools.js` with one INR/negative/missing rule.
- **Money display rule:** missing is "–" with a reason on hover/focus, never `0`. Negative uses a minus sign and colour. Percentages one decimal. Always the date next to the value in tables via a column header or a footnote.
- **Theme:** keep OS-follow; add Light/Dark/System in Settings stored in `localStorage` (non-sensitive). Low effort, so included in F-12.
- **Charts:** keep the dataviz rules in CLAUDE.md. Add only a line for history and a comparison bar. No new chart library.
- **Accessibility requirements:** visible focus on every interactive element (one `:focus-visible` rule), skip link, one `h1` per page that receives focus on route change, `aria-current` on nav, `aria-live` for async results, 44px touch targets, contrast pinned by test for text tokens, status never by colour only, keyboard-operable dialogs and menus, reduced-motion respected.
- **Test plan for UI:** extend `test_static_rules.py` (CSP rules for each new file, `data-label` on new cells, focus rule exists), node tests for formatters/filters/routing, and a Playwright smoke run kept **out of the default test suite** (manual or optional script) because Chromium on the Pi is heavy. The existing 360px iframe check remains the phone width test.

### Pi performance

Holdings are tens to a few hundred rows: render all, no virtual scrolling. Page-level lazy fetch; one `/api/portfolio` payload shared across pages and cached in memory until an import or refresh; sort/filter in memory; `/api/holdings/<isin>` returns one holding only; history and snapshots computed on demand and cached by statement ids. No polling.

### Pages and components: create / modify / consolidate / remove

| Action | Item |
|---|---|
| Create | `nav.js` (router, shell), Overview, Holdings, Holding detail, Imports, Settings, Help component |
| Modify | `index.html` becomes a shell; `app.js` split; `app.css` gets tokens and focus rule |
| Consolidate | Funds + Stocks tables → one Holdings table with asset-class filter; Statements + upload + completeness detail → Imports; Your data + prices bar → Settings |
| Remove | Standalone Concentration and Completeness sections (become Overview panels); the permanent prices bar (becomes an action) |

---

## 6. Consolidated backlog

Duplicates merged: B3 explainable calculations + A3 detail view → F-07; B4 + B10 + data-freshness center → F-08; B11 timeline + import audit trail → F-17 and F-15 stay separate but share the event source; export center + B12 snapshots → F-18; privacy dashboard + backup/health → F-12 sections; saved filters + dashboard customisation → F-13.

Common acceptance for every item: tests added with synthetic data, `privacy_check` passes, CSP tests pass, no `innerHTML`, financial values via `Decimal`, docs updated.

### P0

**F-01 Privacy and quality gates in CI and local hooks.** *Status: Done (CI and privacy gate)* Problem: the guard only works if someone runs it. Solution: GitHub Actions workflow running `pytest`, node tests and `privacy_check.py` on every PR; `core.hooksPath` documented; extend the check to flag `.db`, `.gpg` and `data/`. Effort S. Impact: none on app. Acceptance: a PR containing a PAN-shaped string fails CI. Risk: CI logs must not echo matched values (print file and line only). Depends: none. Note: Actions use no secrets and no statements.

**F-02 Import preview before save (dry run).** *Status: Done (`POST /api/statements/preview` and the guided upload)* (CLI dry run exists, the API has none). Problem: the user cannot see what will be saved. Solution: `POST /api/statements/preview` parses and returns the summary (counts, ignored rows, reconciliation, duplicate/outdated status) and keeps nothing; the client re-sends the file to `POST /api/statements` on Save, so the PDF is never stored server-side. Effort M. Backend: split `parse_pdf_bytes` result summarising from `save`. DB: none. Frontend: Imports stepper. Acceptance: preview writes no rows, no audit row with statement text, same-hash and outdated cases are described; wrong password gives the existing coded errors. Risk: the file is uploaded twice (fine on localhost); the password must not be logged. Depends: F-14 for the UI.

**F-03 Statement-date vs latest-value labelling audit.** *Status: Done (every Overview tile and the concentration note carry a date)* Problem: every figure must carry date and source. Solution: a checklist pass over every tile, table header and API field, adding `as_of` and `source` where missing, plus a test that each money field in `/api/portfolio` has a date in its parent object. Effort S-M. Acceptance: no money figure in the UI without a date or a shared header date. Risk: clutter, so use column headers and footnotes, not repeated labels.

**F-04 Backup before destructive and migration steps.** *Status: Done (safety backup before delete-all and schema upgrades)* Problem: `DELETE /api/data` and schema upgrades have no automatic safety copy. Solution: before delete-all and before `_migrate()` makes changes on an existing file, call `create_backup()` (skip with a notice if the directory is not writable). Effort S. Acceptance: test shows a verified backup exists after delete-all; migration test on a copy only. Risk: backups hold real data, so permissions stay 0700/0600. Depends: none.

### P1

**F-05 App shell and navigation.** *Status: Done (hash-routed shell)* Hash router, sidebar/bottom bar, page titles, `aria-current`, focus management, skip link, remembered last page. Effort L. Acceptance: every current feature reachable in at most two clicks; deep links work; keyboard and screen-reader order tested. Risk: breaking CSP (no inline handlers), router must tolerate unknown routes. Depends: F-10.

**F-06 Overview page.** *Status: Done* Total with date range, statement vs latest value, allocation, change since previous statement (reuse `/api/changes` defaults), freshness, issues, concentration flags, quick actions. Effort M. Acceptance: all figures dated; works with zero, one and two statements; mixed-date alert retained. Depends: F-05, F-03.

**F-07 Unified Holdings list and Holding detail with calculation explanations.** *Status: Done (Holdings list and detail)* One table, filters (asset class, stock/ETF/fund/bond, AMC, source statement, held in several accounts, ISIN search), allocation %, cost/P&L only where cost exists. Detail page: value at each statement date (`stock_holdings`/`mf_holdings` across statements, via `value_history`-style rules), per-account split, source statement and date, price used and where it came from, and a "How this is calculated" block. API: `GET /api/holdings` (list, the same as today's view) and `GET /api/holdings/{isin}`. DB: none. Effort L. Acceptance: missing shows "–" with reason; explanation text is generated from the same values used in the sum, so it cannot drift; tested against synthetic multi-statement fixtures. There is no stored price history, so the detail page shows statement values per date, not daily prices. Risk: explanations must not claim precision the data lacks (for example a stock "gain" without cost). Depends: F-05. Merges B3.

**F-08 Data confidence and freshness center.** *Status: Done (`/api/confidence`, Overview panels)* Extend to: missing ISIN/price/cost/transactions/classification, stale prices per source, each issue stating **which calculation it affects** and the action. Reconciliation assistant (B10) is the same list with "what to do" text; nothing modifies holdings. API: extend `completeness` items with `affects` and `action`; add `GET /api/sources` (latest date per source), which the freshness center needs. Effort M. Acceptance: every issue links to the rows concerned. Risk: wording must separate "not verified" from "wrong". Depends: F-05.

**F-09 Guided import page and audit trail.** *Status: Done (Imports page, `/api/audit`)* Imports page: accepted files, where to download, password rules, stepper (File → Preview → Saved), per-statement detail (parser version, warnings, reconciliation per account, ignored rows), and the audit events (time, type, code). `GET /api/audit` returns events with limit. Effort M-L. Acceptance: never shows parser messages that could quote statement text (existing rule: codes only). Depends: F-02, F-05.

**F-10 Design tokens, formatters and shared components.** *Status: Done* As section 5. First task before pages. Effort M. Acceptance: one `formatMoney`/`formatPct`/`formatDate` used everywhere; focus rule on all controls; node tests for formatters; a test pinning contrast of text tokens in both themes; a tablet breakpoint; `.sr-only` fixed with `white-space: nowrap`.

**F-11 MF returns: XIRR and realised gains.** *Status: Done (`returns.py`, `/api/returns`); realised gains wait for F-19* Per fund where `open_units == 0` and history is complete, XIRR from `mf_transactions` plus closing value; clearly labelled "since first transaction in the statement". Realised gains need lots (F-19). Pure module `returns.py`, `Decimal`-based with a documented solver and a fixed tolerance. Effort L. Acceptance: matches hand-computed synthetic cases (one purchase; SIP; with redemption); funds skipped are listed with reason. Risk: wrong XIRR from partial history, so skip, never estimate. Depends: none (backend).

**F-12 Settings: data and privacy, display, health.** *Status: Done (Settings, `/api/health`, `/api/backups`)* Move export/delete here; add what is stored and where (database path, masked IDs, no PAN), backup list and "back up now", database check, disk free, last price refresh, theme (System/Light/Dark), and the `localStorage` keys used (non-sensitive only). `GET /api/health` (richer than `/healthz`, never portfolio data). Effort M. Acceptance: delete uses native dialog with the typed phrase; nothing sensitive in `localStorage`. Depends: F-05, F-04.

**F-13 Remembered view state and saved views.** *Status: Done (saved views)* Persist sort, filters, last compared dates in `localStorage` (no holdings data, no amounts); named saved views are filter definitions only. Effort S-M. Acceptance: cleared by "Reset display settings". Merges dashboard customisation (show/hide Overview panels). Depends: F-07.

**F-14 Empty, loading, error states.** *Status: Done* A fresh install shows steps, not blank sections; failures say what to do (for example "start the server again"). Effort S. Depends: F-10. Also: move focus to the error or result region after an upload, and show a real progress state (not only "Reading…").

**F-32 Quick UI fixes.** *Status: Done* Problem: small defects found in the audit that need no redesign. Solution: debounce search; fetch `/api/changes` once per data change, not on every `load()`; replace the stale "a later feature" text; do not reload after a failed delete; add focus styles for `select` and expandable rows. Effort S. Priority P1. No API or DB change. Acceptance: node test for the debounce helper; static-rule test that the focus rule covers `select` and `[tabindex]`. Risk: none to data. Depends: none.

**F-15 Contextual help.** *Status: Done (headings with a "?" and `GLOSSARY` in `tools.js`)* A `Help` component with short definitions (statement value, latest value, cost basis, unrealised gain, XIRR, reconciliation) and limitations next to the number. A glossary lives in one JS object so wording is consistent. Effort S. Depends: F-10.

### P2

**F-16 Performance page and comparison mode.** *Status: Done (comparison existed; asset-class value per statement added to `/api/history` and the Performance page)* Move history and changes here; compare two dates side by side; growth by asset class over time (old items 2 and 9). Value over time is not return, so keep the existing disclaimer until F-11/F-20 exist. Effort M. Depends: F-05.

**F-17 Investment timeline.** *Status: New.* Chronological list of statement imports, MF transactions, and detected changes. Dividends and corporate actions are not in CAS data reliably, so scope to what exists. Effort M. Depends: F-09, F-11.

**F-18 Export center and report snapshots.** *Status: Partial (JSON export, table CSVs).* A Reports page: CSV per table with a header block (as-of, source, completeness note), printable HTML report (browser "Save as PDF"; no PDF library), and saved dated snapshots (`report_snapshots` table holding the computed view JSON) for later comparison. Effort L. DB: new table plus `_migrate()` and schema test. Risk: snapshots contain real figures, so they are in the DB and covered by export/delete-all/backup. Depends: F-07.

**F-19 Tax-lot explorer (B9).** *Status: New.* FIFO lots per fund from `mf_transactions` where history is complete; holding period and short/long-term flags for the user to check, not tax advice; equity lots only if a tradebook CSV is imported (old item 4). Effort XL. Depends: F-11, F-23.

**F-20 Cash-flow-adjusted performance (B8).** *Status: New.* Money-weighted (XIRR) at portfolio level for funds with full history; time-weighted needs prices between flows, which statements do not give, so do not offer it. Effort L. Depends: F-11. Risk: mixing funds with and without full history gives a misleading number, so show coverage ("covers 62% of fund value").

**F-21 Portfolio drift monitor (B7).** *Status: New.* User targets per asset class (table `targets`), deviations shown, no trade advice. Effort M. DB: new table. Depends: F-06.

**F-22 Stress test (B5).** *Status: Done (Performance page, `stressTest` in `tools.js`)* Client-side calculation on the loaded view: shocks per asset class or holding, results labelled "assumption, not a prediction". Effort S-M. No DB. Depends: F-07.

**F-23 Stock cost from tradebook CSV (old item 4).** *Status: Planned.* Import broker holdings/tradebook CSV (user export), map to ISINs, store average cost per demat holding with source. Needs a mapping UI per broker format and strict validation. Effort XL. Risk: wrong matching; show unmatched rows, never guess.

**F-24 Onboarding and demo mode.** *Status: Done (`PORTFOLIO_DEMO=true`)* A synthetic in-memory portfolio served from fixtures, shown with a visible "Demo" banner, never written to the real DB. Effort M. Depends: F-06, F-07.

**F-25 Release and migration safety.** *Status: Done (`docs/UPGRADING.md`; `Store.from_url` refuses a newer schema)* UPGRADING.md with steps: backup, pull, restart, check `/healthz`, rollback by restoring the backup; a startup check that refuses a database from a newer schema. Effort S. Depends: F-04.

**F-26 Fund overlap and hidden exposure (B2).** *Status: New.* Requires fund portfolio holdings (AMC disclosure files). There is no single reliable public ISIN-level source with a stable URL, and parsing AMC spreadsheets is brittle. Do not build until a documented source is chosen; an interim version could import a user-supplied CSV of a fund's holdings. Effort XL. Risk: showing estimated exposure as measured.

### P3

**F-27 Multi-currency (B13).** USD holdings from an uploaded CSV with a documented exchange-rate table (RBI reference rate entered by the user or fetched from a public file). Effort L. Old item 5 (Vested). Depends: F-23 pattern.

**F-28 Investment journal (B6).** Notes linked to an ISIN or date in a `notes` table, kept apart from statement data, included in export/delete/backup. Effort M. Risk: free text may hold personal details; warn once in the UI.

**F-29 Local AI analyst (B14).** Not recommended now. A Pi cannot run a useful model, and a general assistant over portfolio data raises leakage risk. Revisit only as an optional, off-by-default tool that talks to a model on `localhost`, with an allow-listed read-only data view. Effort XL.

**F-30 Command palette.** Skip: with about eight pages and a search field on Holdings it adds code without benefit.

**F-31 Off-device backups, scheduled refresh, app authentication, Postgres, AMC map coverage, NSDL real-statement verification.** *Status: Planned (old items 7, 8, 10, 11, 12).* Keep as separate small items; the maintainer runs the app by hand, so scheduled units stay optional and are not installed. CLAUDE.md "Known gaps" item 6 (personal-data guard) is done and should be removed or rewritten as F-01 when CLAUDE.md is updated.

---

## 7. Technical plan

- **Calculations stay out of the UI.** New logic goes in pure modules (`returns.py`, `lots.py`, `drift.py`) taking plain rows and returning `Decimal`; `app.py` only converts via `_jsonable`. Explanations are returned by the API next to the numbers, so the text cannot disagree with the maths.
- **Schema changes** only for F-18 (`report_snapshots`), F-21 (`targets`), F-28 (`notes`), F-23 (cost columns). Each needs the table, `_migrate()`, and the frozen-database test. Everything in Phases 1 to 3 needs **no** schema change.
- **API additions:** `/api/statements/preview`, `/api/holdings`, `/api/holdings/{isin}`, `/api/audit`, `/api/health`, `/api/sources`. All GET, no new write risk except preview, which writes nothing.
- **Testing:** synthetic fixtures built from casparser models (existing pattern); regression tests on `compute_view` before any refactor; node tests for the formatters, router and filters; static-rule tests extended for new JS files and new tables' `data-label`; migration tests only on copies; `privacy_check` in CI.
- **Never** run migrations or tests against `data/portfolio.db`.

## 8. Phased roadmap

| Phase | Items | Outcome (measurable) | Depends on |
|---|---|---|---|
| 1 Foundation | F-01, F-04, F-03, F-25 | CI green with privacy gate; backup taken before delete-all; every money figure dated; upgrade doc | none |
| 2 UI shell | F-10, F-14, F-15, F-05, F-06 | All current features reachable from navigation; Overview loads one request; focus visible everywhere | Phase 1 |
| 3 Holdings and imports | F-07, F-02, F-09, F-12, F-13 | One holdings list; detail page for every holding; preview before save; audit visible | Phase 2 |
| 4 Analytics | F-08, F-11, F-16, F-20 | XIRR on fully-covered funds with coverage shown; each data issue names the figure it affects | Phase 3 |
| 5 Reports and planning | F-18, F-21, F-22, F-24, F-17 | Dated snapshots; drift and stress views; demo mode | Phase 3 |
| 6 Advanced and optional | F-19, F-23, F-26, F-27, F-28 | As data sources are chosen | Phase 4 |

F-29 and F-30 are parked.

## 9. First sprint

Scope: **F-01, F-04, F-10 (tokens, formatters, focus rule), F-14 (empty states), F-32 (quick UI fixes), F-02 (preview endpoint only)**.

Why: they carry the highest risk reduction for the least change. F-01 and F-04 close the real privacy and data-loss gaps. F-10 and F-14 are prerequisites for every later UI item and fix audit points 7 and 10 without moving any page. F-02's backend half is needed by the guided import and touches only `app.py` and a refactor of the summary code, with tests. No schema change, no behaviour change to existing endpoints.

Done when: CI passes and fails correctly on a planted fake PAN; delete-all leaves a verified backup; formatters tested in node; fresh install shows steps; preview endpoint returns the summary and the row counts in all tables are unchanged afterwards.

## 10. Main technical risks

1. **Splitting `app.js` and adding a router** without a framework can cause state bugs. Mitigation: one shared data store object, pages that only read it, node tests for routing.
2. **Wrong or misleading returns** (XIRR on partial history, value-over-time read as performance). Mitigation: skip rather than estimate, show coverage.
3. **Holding detail history across statements** must respect the one-statement-per-source rule, or it will double count. Reuse `build_view(as_of=…)`.
4. **Preview doubles the upload** and increases the chance of logging the password. Mitigation: keep the header-only password, add a test that no log line or audit row contains it.
5. **Third-party sources** (AMC holdings, FX) have no stable official URL; building on scraping would break the project's rule.
6. **Unverified UI claims**: this audit is from source; run the page in a browser before the shell is built.
7. **Pi resources:** Playwright in the default test run would be slow; keep it optional.

## 11. Next steps, in order

1. Agree this backlog; update CLAUDE.md "Known gaps" to point here and fix item 6.
2. Open the page in a browser at desktop and 360px and confirm or correct section 3. Do this before building the shell.
3. Branch per item, Phase 1 first (F-01, F-04, F-03, F-25).
4. F-10 tokens and formatters, then F-14, F-15 and F-32.
5. F-02 backend, then the app shell F-05 and Overview F-06.
6. F-07 holdings and detail, then F-09 and F-12.
7. F-11 XIRR as a pure module in parallel with the UI work, because it has no UI dependency.
