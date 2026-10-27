# cas-portfolio

A self-hosted dashboard for Indian investors that shows all your stocks, ETFs, bonds and mutual funds in one place. It's built from the monthly **Consolidated Account Statement (CAS)** PDFs you already receive. There are no broker logins, no API keys and no scraping.

| You upload | It shows |
|---|---|
| CAS from **NSDL** or **CDSL** (enough on its own) | Stocks, ETFs and bonds/SGBs in every demat account (any broker), merged by ISIN, plus mutual fund folios with amount invested |
| CAS from **CAMS** or **KFintech** (optional) | Mutual funds with transaction history |

- **Every account is checked against its statement total,** so you can see if anything wasn't read.
- **A stock held at several brokers appears once,** with a line per account.
- **Mutual funds come from one statement only,** the newest of the two, so nothing is counted twice.

**Latest prices:** click **Refresh prices** to value everything at the latest public end-of-day price, shown next to the statement value: mutual fund NAVs from AMFI, and stock, ETF and gold bond closing prices from NSE (BSE for what NSE doesn't list). A holding with no price shows a dash; nothing is estimated.

**Not covered yet:** purchase cost for stocks (NSDL/CDSL statements don't include it), and US stocks.

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

## Optional: run as a service

You don't need this. Running the app by hand (see **Run** above) is the normal way, and nothing here is installed unless you run the installer. It is for people who want the dashboard always on, restarted after a crash or reboot, with the backup and price refresh scheduled. It uses systemd **user** units: no root is needed for them, and the dashboard still listens on `127.0.0.1` only.

```bash
# stop any server you started by hand first (Ctrl+C): it holds port 8000
scripts/install_systemd.sh
sudo loginctl enable-linger "$USER"    # once: lets user services run at boot and after you log out
```

| Unit | What it does |
|---|---|
| `portfolio.service` | the dashboard, restarted if it fails; files it creates are readable by you only |
| `portfolio-backup.timer` | a database backup at about 02:30 every day (missed runs happen when the Pi is back) |
| `portfolio-prices.timer` | refreshes public prices at 20:00 Indian time on weekdays and at 08:00 daily |

```bash
systemctl --user status portfolio.service            # is it running?
journalctl --user -u portfolio.service -f            # its log
systemctl --user list-timers 'portfolio-*'           # when the jobs run next
systemctl --user restart portfolio.service           # after pulling new code
curl -s localhost:8000/healthz                       # ok / degraded, and the age of the last backup
```

There is no `--reload`, so **restart the service after every code change** (`git pull` alone is not enough). To stop using it: `systemctl --user disable --now portfolio.service portfolio-backup.timer portfolio-prices.timer`.
If you keep the app offline (`PORTFOLIO_FETCH_PRICES=false`), disable `portfolio-prices.timer` too, since the refresh then exits with an error.

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

## Backups

The database is the only parsed copy of your statements, so back it up.

```bash
PYTHONPATH=src .venv/bin/python -m portfolio.backup create    # a verified copy, safe while the app runs
PYTHONPATH=src .venv/bin/python -m portfolio.backup list
PYTHONPATH=src .venv/bin/python -m portfolio.backup check     # integrity check of the live database
PYTHONPATH=src .venv/bin/python -m portfolio.backup restore data/backups/portfolio-<stamp>.db --force
```

- Backups go to `data/backups/` (private, git-ignored); the newest 14 are kept (`PORTFOLIO_BACKUP_KEEP`).
- **Keep copies off the device, encrypted.** Set `PORTFOLIO_BACKUP_GPG_RECIPIENT` to a gpg public key and backups are encrypted to it; the plaintext copy is deleted, and if encryption fails none is left behind. The Pi needs only the public key. Keep the private key somewhere else: you need it to restore. Then copy the `.gpg` files anywhere.
- **Restore:** stop the app first. The backup is verified before anything is replaced, an existing database is only replaced with `--force`, and it is kept beside the restored one (`.before-restore-<time>`).
- `GET /healthz` reports `ok` or `degraded` (HTTP 503), the database schema version and the age of the newest backup in days. It shows no portfolio data, so a monitor can poll it.
- Run `create` by hand whenever you like. If you use the optional service installer below, it schedules a daily backup too. Copying the encrypted `.gpg` files off the device is up to you.

## Privacy

- The PDF is parsed in memory and never written to disk. The password is used only to open it.
- The database keeps parsed holdings and mutual fund transactions only. It doesn't keep your PAN, name, address, phone, email, or full folio or client numbers (only the last 4 characters).
- Uploads and deletions need a custom request header (CSRF protection), and the page sets a strict Content Security Policy.
- Your data stays on your machine. The app makes outbound requests only when you click **Refresh prices** (or run `python -m portfolio.prices`): plain GETs of public files at fixed URLs, AMFI's NAV file and the NSE and BSE end-of-day files (the NSE and BSE file names contain the date). They send nothing about your holdings: no parameters, cookies or ISIN list. The files are matched locally and only the prices of what you hold are kept. If an exchange refuses the request, the app reports it and stops; it does not work around it. Set `PORTFOLIO_FETCH_PRICES=false` to turn all of this off and keep the app fully offline.

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
  prices.py            AMFI fund NAVs (parser, fetcher, refresh)
  bhavcopy.py          NSE/BSE end-of-day prices for stocks, ETFs and bonds
  refresh.py           refresh every price source; each can fail on its own
  changes.py           what changed between two statement dates
  app.py               FastAPI: upload, statements, portfolio JSON
  static/              dashboard page (vanilla JS)
```

## Credits

PDF parsing is done by [casparser](https://github.com/codereverser/casparser) (MIT), including its bundled ISIN database.

## License

[MIT](LICENSE)
