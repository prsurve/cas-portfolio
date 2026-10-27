# Portfolio

One dashboard for your mutual funds and stocks, built from the monthly statements you already receive. There are no broker logins and no API keys.

| You upload | It shows |
|---|---|
| CAS from **NSDL** or **CDSL** (enough on its own) | Stocks, ETFs, bonds/SGBs in every demat account, merged by ISIN, plus mutual fund folios with amount invested |
| CAS from **CAMS** or **KFintech** (optional) | Mutual funds with transaction history |

Mutual funds come from one statement only, the newest of the two, so nothing is counted twice.

Not covered yet: Vested (US stocks), and purchase cost for stocks (it isn't on the NSDL/CDSL statement).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install --index-url https://pypi.org/simple -r requirements.lock
```

`requirements.lock` pins versions known to work on the Raspberry Pi (aarch64, Python 3.13).

## Run

```bash
PYTHONPATH=src .venv/bin/uvicorn portfolio.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000.

The app has no login of its own. Keep it bound to `127.0.0.1`. To reach it from other devices, put it behind Cloudflare Tunnel **with a Cloudflare Access policy** limited to your email.

## Upload a statement

1. In the dashboard, choose the PDF and enter its password, then click **Upload**.
   - NSDL/CDSL: the password is your PAN in capitals.
   - CAMS/KFintech: the password you chose when requesting the statement.
2. Check the summary it shows against the PDF:
   - **Mutual funds:** number of funds, total value and amount invested.
   - **Stocks:** each demat account is listed with its broker. Its "statement total" should match that account's value in the PDF summary.
3. If something looks wrong, delete the statement under **Imported statements**.

Upload a new statement each month. The newest statement of each type replaces the older one on the dashboard. Uploading the same file twice does nothing.

You can also check a PDF from the command line before importing it:

```bash
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas ~/cas/statement.pdf          # dry run
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas ~/cas/statement.pdf --save   # import
```

If an account's rows don't add up to its statement total, the upload shows a warning with the missing amount. To see which rows weren't read:

```bash
PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas ~/cas/statement.pdf --diagnose
```

This lists every ISIN printed in the PDF that wasn't imported, with the text around it. It runs locally and saves nothing.

## Privacy

- The PDF is parsed in memory and never written to disk. The password is used only to open it.
- The database keeps the parsed holdings and mutual fund transactions only. It doesn't keep your PAN, name, address, phone, email, or full folio or client numbers (only the last 4 characters).
- Uploads and deletions need a custom request header, which blocks cross-site form attacks. The page sets a strict Content Security Policy.

## Test

```bash
.venv/bin/pytest -q
```

## Layout

```
src/portfolio/
  importers/cas.py     parse CAMS/KFintech and NSDL/CDSL PDFs (casparser)
  store.py             schema + data access (SQLite now, Postgres later)
  portfolio_view.py    dashboard numbers: merge by ISIN/scheme, totals, allocation
  app.py               FastAPI: upload, statements, portfolio JSON
  static/              dashboard page
```
