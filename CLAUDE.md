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

There is no `--reload`, so restart the server after code changes.

## Layout

| File | Role |
|---|---|
| `src/portfolio/importers/cas.py` | Parses PDFs via `casparser` into `ParsedStatement`. Also holds the CLI, `diagnose`, ISIN enrichment, broker/AMC mapping and `PARSER_VERSION` |
| `src/portfolio/store.py` | SQLAlchemy Core schema plus a tiny `_migrate()` that adds columns; SQLite now, Postgres-compatible |
| `src/portfolio/portfolio_view.py` | Dashboard JSON: merges by ISIN or scheme, computes totals and allocation, picks the fund source |
| `src/portfolio/app.py` | FastAPI: `POST/GET/DELETE /api/statements`, `GET /api/portfolio`, static page |
| `src/portfolio/static/` | Vanilla JS/CSS page. DOM is built with `el()`; never use `innerHTML`, because the strict CSP blocks inline scripts |
| `tests/conftest.py` | Fixtures built from **casparser's own pydantic models** (`CASData`, `NSDLCASData`) |

## Data model and rules

- **Statement kinds:** `mf` comes from CAMS/KFintech; `demat` comes from NSDL/CDSL.
  - A demat statement fills `stock_holdings` (`asset_type`: `equity`, `fund` or `bond`) and also `mf_holdings` from its "Mutual Fund Folios" section.
  - CAMS/KFintech fills `mf_holdings` and `mf_transactions`.
- **Never sum across statements.**
  - Demat holdings come from the latest demat statement only.
  - Mutual funds come from exactly one statement: the newest of (latest CAMS/KFintech, latest NSDL/CDSL) that has fund rows. On the same date, CAMS/KFintech wins because it has transactions.
- **Merge keys:** demat holdings merge by ISIN across accounts; funds merge by ISIN, falling back to scheme name, across folios.
- **Money:** always `Decimal`, converted to float only in `_jsonable`. Statement values are as of the statement date; there are no live prices.
  - **Storage:** use the `Money` column type (`money.py`) for amounts and quantities. On SQLite it stores integer millionths, because a NUMERIC column would store a float. Older databases are converted once on open (`PRAGMA user_version` 0 → 1). Values that need more than 6 decimals or exceed ~9.2 trillion are not supported on SQLite.
- **Fund gains:** computed only over funds that have a cost (`costed_value - cost`). Funds without cost are counted in `mf_without_cost`.
- **Re-imports:** the same file hash is a no-op unless it was stored with an older `PARSER_VERSION`, in which case it is replaced. **Bump `PARSER_VERSION` whenever parsing changes what gets imported**, otherwise re-uploads are silently ignored.
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
- **Folio rows carry `total_cost`** (amount invested) when the statement prints that column, and no AMC name. The AMC comes from the ISIN issuer code (`AMC_BY_ISSUER`, characters 4–7).
- **The diagnose output also lists ISINs that aren't problems:** mutual fund scheme description blocks ("ISIN : … RTA : …") and securities sold during the period (transactions only). It labels the likely reason for each.
- **Wrong password** raises `IncorrectPasswordError`; an unknown issuer raises `CASParseError`. Both become a user-facing `CasImportError`.

## Conventions

- **Git:** never commit to `main`; use a branch and pull request per change. Commit with the GitHub noreply address, not a personal email.
- **Tests first for parser changes:** build inputs from casparser models (`model_copy(update=…)`) so a casparser format change breaks tests, not imports. Reproduce each real-statement quirk with made-up numbers.
- **Schema changes:** add the column to the `Table` **and** to `Store._migrate()`, since `create_all` won't alter existing tables. Test the migration on a copy of the DB, never on `data/portfolio.db` itself.
- **Security:** keep the app bound to `127.0.0.1` and reach it remotely through Cloudflare Tunnel with Access. Non-GET requests need `X-Requested-With: portfolio` (CSRF). Keep the CSP strict.

## Known gaps and future improvements

Rough priority order:

1. **Prices between statements.** Revalue holdings daily from public end-of-day data: AMFI `NAVAll.txt` for funds, and the NSE/BSE bhavcopy for stocks, ETFs and SGBs. Show "statement value" next to "latest value".
2. **Performance over time.** Each statement is already stored separately, so chart total value per statement date (month by month) and growth by asset class.
3. **Mutual fund XIRR and realized gains** from `mf_transactions`. This needs a CAMS/KFintech detailed statement; the CDSL folio section has no transactions.
4. **Stock purchase cost.** NSDL/CDSL has none. Options: import broker holdings or tradebook CSV exports (Zerodha Console, Upstox, HDFC Securities) to get average cost and stock P&L.
5. **Vested (US stocks):** a CSV/PDF statement import with a USD→INR rate. No public API exists.
6. **Personal-data guard:** a test or pre-commit hook that fails on PAN-shaped strings, long account IDs or `*.pdf` and `*.db` files in the commit.
7. **App authentication** beyond Cloudflare Access (for example a passkey or single password), if it is ever exposed differently.
8. **Run as a service:** a systemd unit for uvicorn on the Pi, plus encrypted off-device backups of `data/portfolio.db`.
9. **Allocation charts:** allocation is currently shown as tables.
10. **Coverage of the AMC map:** extend `AMC_BY_ISSUER` when funds show "Unknown" under allocation by AMC.
11. **NSDL statements:** parsing has only been exercised against a real CDSL statement and synthetic NSDL fixtures. Verify with a real NSDL CAS.
12. **Postgres/Supabase:** add `psycopg` and set `PORTFOLIO_DATABASE_URL`. The schema is already portable.
13. **Housekeeping:** the Starlette `TestClient` deprecation warning (the `httpx2` switch); prune old statements' rows once history charts exist.
