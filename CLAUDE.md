# CLAUDE.md

Self-hosted portfolio dashboard for Indian investors, built **only from uploaded statements** (CAS PDFs). Single user per install. Developed on a Raspberry Pi (aarch64, Python 3.13).

**Statement-only by design.** Don't add broker API integrations, stored credentials or scraping.

## Privacy rule (most important)

Never commit or push personal details. That covers PAN, full demat client IDs or folio numbers, a user's name or contact details, statement PDFs or their filenames/IDs, `data/` (the SQLite DB), `.env`, and real totals, quantities or prices from anyone's statements. Tests and docs use made-up values. Stock names and ISINs are fine.

Before any push, scan the files being pushed for PAN-shaped strings (`[A-Z]{5}[0-9]{4}[A-Z]`), 8+ digit IDs and real figures, and confirm `.gitignore` still covers `*.pdf`, `data/`, `*.db` and `.env`. Never ask a user to share a statement PDF: `--diagnose` runs locally and prints what's needed, and they can describe the shape of a line with the numbers replaced.

## Commands

```bash
# install (PyPI directly; the piwheels mirror on this Pi times out and broke resolution)
python3 -m venv .venv
.venv/bin/pip install --index-url https://pypi.org/simple -r requirements.lock

# run (localhost only; there is no app login)
PYTHONPATH=src .venv/bin/uvicorn portfolio.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

# tests
.venv/bin/pytest -q

# command-line import: dry run / save / list ISINs in the PDF that weren't imported
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas statement.pdf [--save] [--diagnose]
```

There is no `--reload`, so restart the server after code changes. (Users of the optional systemd setup: `systemctl --user restart portfolio.service`.) A server started before a merge answers 404 for new routes, so restart after every pull. The page files are read from disk on each request, so a running server can show a newer page than its API supports until restarted.

## Layout

| File | Role |
|---|---|
| `src/portfolio/importers/cas.py` | Parses PDFs via `casparser` into `ParsedStatement`. Also holds the CLI, `diagnose`, ISIN enrichment, broker/AMC mapping and `PARSER_VERSION` |
| `src/portfolio/store.py` | SQLAlchemy Core schema plus a tiny `_migrate()` that adds columns; SQLite now, Postgres-compatible |
| `src/portfolio/portfolio_view.py` | Dashboard: `compute_view()` (Decimal) and `build_view()` (JSON floats): merges by ISIN or scheme, computes totals and allocation, picks the fund source |
| `deploy/`, `scripts/install_systemd.sh` | **Optional, for other users; the maintainer does not run the app as a service.** systemd **user** units (dashboard bound to 127.0.0.1, daily backup timer, price refresh timer). The installer refuses while port 8000 is in use and has `--render DIR` for testing |
| `src/portfolio/backup.py` | SQLite backups: `create_backup()` (online backup API, verified, optional gpg encryption to a public key, retention), `restore_backup()`, `check_database()`, CLI (`python -m portfolio.backup`) |
| `src/portfolio/prices.py` | AMFI `NAVAll.txt`: pure parser, fixed-URL fetcher, `refresh_nav()` (stores only held ISINs, newest price), CLI (`python -m portfolio.prices`) |
| `src/portfolio/bhavcopy.py` | NSE zip / BSE CSV end-of-day files: dated fixed URLs walked back over weekends and holidays, parser, `refresh_exchange()` (NSE first; BSE only for ISINs NSE lacks) |
| `src/portfolio/refresh.py` | `refresh_all()`: runs every source; each can fail alone, and it is an error only if something failed and nothing was priced |
| `src/portfolio/changes.py` | `compare()`: what differs between two statement dates (two `compute_view(as_of=…)` results). Each change splits into a quantity effect and a price effect that sum exactly to the value change |
| `src/portfolio/history.py` | `value_history()`: one point per statement date, each built with `build_view(as_of=…)` so the no-double-count rules hold |
| `src/portfolio/app.py` | FastAPI: `POST/GET/DELETE /api/statements`, `GET /api/portfolio`, `GET /api/history`, `GET /api/changes`, `POST /api/prices/refresh`, `GET /healthz`, static page |
| `src/portfolio/static/` | Vanilla JS/CSS page. DOM is built with `el()`; never use `innerHTML`, because the strict CSP blocks inline scripts. `tools.js` holds the DOM-free search, sort and CSV helpers (tested with node: `tests/tools.test.js`); CSV text is guarded against spreadsheet formulas. **Charts** follow the dataviz rules: asset-class colours are fixed per entity (`ASSET_SLOT`), never by rank; every other group is one colour; no donuts, no dual axes, no number on every point; a label sits inside a segment only if it fits; the table is the legend. The four `--series-*` colours in `app.css` were validated with the skill's `validate_palette.js` for light and dark (`tests/test_static_rules.py` pins them: re-validate if you change one). Set widths with `element.style.x = …` (CSSOM), never a `style` attribute, which the CSP blocks. **Phones (≤640px):** tables with `class="cards"` render each row as a card, labelled from a `data-label` on every cell (the first cell is the card's title); a new column needs a `data-label` (tested). `.table-wrap` must stay `position: relative`: the absolutely positioned `.sr-only` text otherwise escapes its scroll box and widens the whole page. Keep grid column minimums under a phone's width (`min(340px, 100%)`). To check phone width, headless Chromium cannot go below about 485px wide, so load the page in a 360px `<iframe>` (the app's frame-blocking headers must be dropped for that test only) and measure `scrollWidth` against `clientWidth` |
| `tests/conftest.py` | Fixtures built from **casparser's own pydantic models** (`CASData`, `NSDLCASData`) |

## Data model and rules

- **Statement kinds:** `mf` comes from CAMS/KFintech; `demat` comes from NSDL/CDSL.
  - A demat statement fills `stock_holdings` (`asset_type`: `equity`, `fund` or `bond`) and also `mf_holdings` from its "Mutual Fund Folios" section.
  - CAMS/KFintech fills `mf_holdings` and `mf_transactions`.
- **Never sum across statements.**
  - Demat holdings come from the latest demat statement only.
  - Mutual funds come from exactly one statement: the newest of (latest CAMS/KFintech, latest NSDL/CDSL) that has fund rows. On the same date, CAMS/KFintech wins because it has transactions.
  - The total can still mix dates, because stocks and funds may come from statements of different dates. `as_of` in the view gives the range, and `mixed` is true when the two are more than `MIXED_DATES_AFTER_DAYS` (7) apart. The total tile shows the range and an alert appears when it is mixed.
- **Broker names** shown on the page come from the DP name printed on the statement (`broker_label()` in `cas.py`: legal suffixes dropped, case tidied), so any broker works with no list to maintain. The stored `broker` key from `BROKER_PATTERNS` is no longer used for display.
- **Merge keys:** demat holdings merge by ISIN across accounts; funds merge by ISIN, falling back to scheme name, across folios.
- **Money:** always `Decimal`, converted to float only in `_jsonable`. Statement values are as of the statement date. Latest prices are shown beside them, never mixed into the statement-based total.
  - **Storage:** use the `Money` column type (`money.py`) for amounts and quantities. On SQLite it stores integer millionths, because a NUMERIC column would store a float. Older databases are converted once on open (`PRAGMA user_version` 0 → 1). Values that need more than 6 decimals or exceed ~9.2 trillion are not supported on SQLite.
- **Fund gains:** computed only over funds that have a cost (`costed_value - cost`). Funds without cost are counted in `mf_without_cost`.
- **Re-imports:** the same file hash is a no-op unless it was stored with an older `PARSER_VERSION`, in which case it is replaced. **Bump `PARSER_VERSION` whenever parsing changes what gets imported**, otherwise re-uploads are silently ignored.
- **Reconciliation is stored.** `statement_accounts` holds, per statement, the total printed for each demat account and for the "Mutual Fund Folios" section against what was read (`reconciliation_rows()` in `cas.py`). `compute_view()` turns it into `completeness` (accounts that don't add up, funds without cost or AMC, funds whose transaction history is incomplete or missing, stocks without an NSE symbol). Statements imported before parser version 6 have no rows, and the page says to re-upload the PDF.
- **Prices (AMFI).** `NAVAll.txt` has 8 columns (code; ISIN payout/growth; ISIN reinvestment; name; plan; option; NAV; date). The ISIN can be in either ISIN column, some values are `-`, `Redeemed` or have trailing spaces, rows with NAV 0 are dropped, and matured funds can share an ISIN (latest date wins; same date with different NAVs is dropped). A NAV is used only if it is at least as new as the statement's own valuation date, and only on today's dashboard, never in `as_of` views. A fund without a usable NAV shows a dash and is never estimated. Fetching can be turned off with `PORTFOLIO_FETCH_PRICES=false`. Demat-held ETFs trade at market price, so for a demat holding the exchange close is used, and the AMFI NAV only as a fallback for fund units that are not on an exchange. A share never gets a NAV.
- **Exchange files (NSE/BSE bhavcopy).** Same layout for both (`ISIN`, `TckrSymb`, `SctySrs`, `ClsPric`, `TradDt`). NSE is a zip and answers 404 for a day with no file; BSE answers HTTP 200 with a web page, so the content is checked, not the status. Plain GET, honest User-Agent, fixed URL with only the date varying. If an exchange refuses (401/403/429) say so and stop: never retry with other headers or otherwise work around it. The price table keeps one row per (ISIN, source); the view takes the newest date among NSE and BSE (NSE on a tie) and ignores a price older than the statement.
- **Backups** hold everything in the database, so the folder is `data/backups` (0700, files 0600, git-ignored). Backups are made with SQLite's online backup API, verified with `PRAGMA integrity_check` and the required tables before they count, and never overwrite one another. If gpg encryption is requested and fails, no plaintext copy may be left behind (a test covers it). Restore verifies first, needs `--force` to replace a database, and keeps the old one aside. `/healthz` must never return portfolio data or database internals.
- **Concentration** (`_concentration()` in `portfolio_view.py`) uses statement values over the same total as the dashboard. A position is one ISIN, so a fund held in demat and in a folio merges into one. Issuers group shares and bonds of one company by the first 7 characters of the ISIN, all `IN0…` government securities (SGBs, G-secs) as one, and funds by fund house (`amc_for`, else "Unknown fund house"). Funds are never looked through. Only single positions are flagged against `PORTFOLIO_CONCENTRATION_ALERT_PCT` (default 10, strictly greater than); fund houses are listed but not flagged.
- **Stored identifiers:** only the last 4 characters of folio and client IDs (`mask_id`). No PAN, name, address, phone or email.
- **Uploads:** the PDF is read in memory (`io.BytesIO`) and never written to disk. The password arrives in the `X-PDF-Password` header and is used only to open the file.
- **Passwords:** tried as typed, then in capitals (NSDL/CDSL use the PAN in capitals; CAMS uses whatever password the user chose).

## casparser behaviour learned from a real CDSL statement

- `FileType` values are plain strings (`"CAMS"`) on `CASData` because of `use_enum_values`, but enums elsewhere. Use `_enum_name()`.
- CDSL puts **every `INF…` ISIN in an account's `mutual_funds` list**, including ETFs (GOLDBEES and the like). These are real broker holdings: import them as `asset_type="fund"`.
- **Sovereign Gold Bonds and other G-secs** (`IN0…` ISINs) arrive in `equities`. `classify()` turns them into bonds.
- **Rows that don't match casparser's layout are dropped silently.** That's why every account's rows are checked against the account total printed on the statement (`TOLERANCE = ₹1`), with a warning if they differ. `--diagnose` extracts ISINs with pdfium independently of casparser to find gaps.
- **The same ISIN can be listed twice in one account,** once valued and once at ₹0 with the same quantity (seen with unlisted NSE shares). `_drop_duplicate_rows` drops the ₹0 copy; other repeats are kept and flagged.
- **Rows with quantity `--` but a value** (for example rights entitlements) are kept.
- **CDSL security names are messy** (`"ISSUER#SECURITY"`, "AFTER SPLIT …"). `lookup_isins()` uses casparser's bundled local ISIN database for clean names and NSE symbols, and `clean_statement_name()` is the fallback. Enrichment must never fail an import.
- **`mf_holdings.open_units` / `history_from`** record the units held when a CAMS/KFintech statement's transaction list starts, and that date. Transactions describe the whole holding only when `open_units` is zero, so XIRR and gains must skip any fund where it isn't. Both are null for the folio section of an NSDL/CDSL statement and for statements imported before parser version 5 (re-upload the CAMS PDF to fill them).
- **Folio rows carry `total_cost`** (amount invested) when the statement prints that column, and no AMC name. The AMC comes from the ISIN issuer code (`AMC_BY_ISSUER`, characters 4–7).
- **The diagnose output also lists ISINs that aren't problems:** mutual fund scheme description blocks ("ISIN : … RTA : …") and securities sold during the period (transactions only). It labels the likely reason for each.
- **Wrong password** raises `IncorrectPasswordError`; an unknown issuer raises `CASParseError`. Both become a user-facing `CasImportError`. Each carries a short `code`; the audit log records only that code, never the message, since parser messages can quote statement text.

## Conventions

- **Git:** never commit to `main`; use a branch and pull request per change. Commit with the GitHub noreply address, not a personal email.
- **Tests first for parser changes:** build inputs from casparser models (`model_copy(update=…)`) so a casparser format change breaks tests, not imports. Reproduce each real-statement quirk with made-up numbers.
- **Schema changes:** add the column to the `Table` **and** to `Store._migrate()`, since `create_all` won't alter existing tables. Test the migration on a copy of the DB, never on `data/portfolio.db` itself.
- **Security:** keep the app bound to `127.0.0.1` and reach it remotely through Cloudflare Tunnel with Access. Non-GET requests need `X-Requested-With: portfolio` (CSRF). Keep the CSP strict.

## Known gaps and future improvements

Rough priority order:

1. **Prices between statements.** Done: AMFI NAVs for funds (`prices.py`) and NSE/BSE closes for stocks, ETFs and SGBs (`bhavcopy.py`), matched by ISIN. Still open: a scheduled daily refresh (see 8), and price history (only the newest price per ISIN is kept).
2. **Performance over time.** Done: total value per statement date (`/api/history`, SVG chart). Still open: growth by asset class, and returns (the chart is value held, so it rises with new money too).
3. **Mutual fund XIRR and realized gains** from `mf_transactions`. This needs a CAMS/KFintech detailed statement; the CDSL folio section has no transactions.
4. **Stock purchase cost.** NSDL/CDSL has none. Options: import broker holdings or tradebook CSV exports (Zerodha Console, Upstox, HDFC Securities) to get average cost and stock P&L.
5. **Vested (US stocks):** a CSV/PDF statement import with a USD→INR rate. No public API exists.
6. **Personal-data guard:** a test or pre-commit hook that fails on PAN-shaped strings, long account IDs or `*.pdf` and `*.db` files in the commit.
7. **App authentication** beyond Cloudflare Access (for example a passkey or single password), if it is ever exposed differently.
8. **Off-device backups:** copy the encrypted `.gpg` files off the Pi. (An optional systemd setup for other users exists in `deploy/`; the maintainer runs the app, backups and price refresh by hand. Don't install it on the maintainer's machine.)
9. **Allocation charts:** done (a stacked bar for asset classes, a bar per row elsewhere, tables kept as the legend). Still open: a chart of growth by asset class over time.
10. **Coverage of the AMC map:** extend `AMC_BY_ISSUER` when funds show "Unknown" under allocation by AMC.
11. **NSDL statements:** parsing has only been exercised against a real CDSL statement and synthetic NSDL fixtures. Verify with a real NSDL CAS.
12. **Postgres/Supabase:** add `psycopg` and set `PORTFOLIO_DATABASE_URL`. The schema is already portable.
13. **Housekeeping:** the Starlette `TestClient` deprecation warning (the `httpx2` switch); prune old statements' rows once history charts exist.
