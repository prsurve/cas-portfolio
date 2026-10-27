# cas-portfolio

A self-hosted dashboard for Indian investors that shows all your stocks, ETFs, bonds and mutual funds in one place. It's built from the monthly **Consolidated Account Statement (CAS)** PDFs you already receive. There are no broker logins, no API keys and no scraping.

| You upload | It shows |
|---|---|
| CAS from **NSDL** or **CDSL** (enough on its own) | Stocks, ETFs and bonds/SGBs in every demat account (any broker), merged by ISIN, plus mutual fund folios with amount invested |
| CAS from **CAMS** or **KFintech** (optional) | Mutual funds with transaction history |

- **Every account is checked against its statement total,** so you can see if anything wasn't read.
- **A stock held at several brokers appears once,** with a line per account.
- **Mutual funds come from one statement only,** the newest of the two, so nothing is counted twice.

**Not covered yet:** live prices (values are as of the statement date), purchase cost for stocks (NSDL/CDSL statements don't include it), and US stocks.

> This is a personal tool, not financial advice, and not affiliated with NSDL, CDSL, CAMS or KFintech. Always check the numbers against your statement.

## Getting your statements

- **NSDL/CDSL CAS:** sent monthly by email by whichever depository holds your accounts, as a PDF whose password is your PAN in capitals.
  - NSDL: subscribe at https://nsdlcas.nsdl.com/ under "Subscribe to e-CAS".
  - CDSL: ask your broker to register your email for statements.
- **CAMS/KFintech CAS (optional):** request a *detailed* statement from the CAMS or KFintech website. You choose its password when requesting it.

## Setup

Requires Python 3.11 or newer.

```bash
git clone https://github.com/prsurve/cas-portfolio.git
cd cas-portfolio
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

`requirements.lock` pins versions tested on Linux (aarch64, Python 3.13). On a Raspberry Pi, add `--index-url https://pypi.org/simple` if the piwheels mirror is slow.

## Run

```bash
PYTHONPATH=src .venv/bin/uvicorn portfolio.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000.

**The app has no login of its own.** Keep it bound to `127.0.0.1`. To reach it from other devices, use an SSH tunnel, or a reverse proxy or tunnel (such as Cloudflare Tunnel) with an access policy limited to you. Never expose it to the internet without authentication.

Settings are optional; see [.env.example](.env.example). The database is `data/portfolio.db` by default.

## Upload a statement

1. Choose the PDF and enter its password, then click **Upload**. If the password fails as typed, it's retried in capitals.
2. Check the summary against the PDF:
   - **Demat accounts:** each one shows "read ₹X of statement total ₹X". A red "missing" amount means some rows weren't read.
   - **Mutual funds:** number of holdings, value and amount invested.
3. If something looks wrong, delete the statement under **Imported statements**.

Upload a new statement each month. The newest statement replaces the older one on the dashboard. Uploading the same file twice does nothing.

### Command line

```bash
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas statement.pdf             # dry run: print what was read
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas statement.pdf --save      # import
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas statement.pdf --diagnose  # list ISINs in the PDF that weren't imported
```

## Privacy

- The PDF is parsed in memory and never written to disk. The password is used only to open it.
- The database keeps parsed holdings and mutual fund transactions only. It doesn't keep your PAN, name, address, phone, email, or full folio or client numbers (only the last 4 characters).
- Uploads and deletions need a custom request header (CSRF protection), and the page sets a strict Content Security Policy.
- Your data stays on your machine. Nothing is sent anywhere.

## Reporting problems

Statements differ between depositories and over time. If an account doesn't add up, open an issue describing what kind of holding is missing and what its line looks like, with the numbers replaced by X. **Never attach your statement PDF or paste unredacted output:** it contains your PAN, account numbers and holdings.

## Development

```bash
.venv/bin/pip install pytest httpx
.venv/bin/pytest -q
```

Parser tests build their input from casparser's own data models, so a change in casparser's output breaks the tests rather than silently breaking imports. See [CLAUDE.md](CLAUDE.md) for the data rules and known statement quirks.

```
src/portfolio/
  importers/cas.py     parse NSDL/CDSL and CAMS/KFintech PDFs (via casparser), CLI, diagnose
  store.py             schema + data access (SQLite; Postgres-compatible)
  portfolio_view.py    dashboard numbers: merge by ISIN/scheme, totals, allocation
  history.py           value at each statement date (no double counting)
  app.py               FastAPI: upload, statements, portfolio JSON
  static/              dashboard page (vanilla JS)
```

## Credits

PDF parsing is done by [casparser](https://github.com/codereverser/casparser) (MIT), including its bundled ISIN database.

## License

[MIT](LICENSE)
