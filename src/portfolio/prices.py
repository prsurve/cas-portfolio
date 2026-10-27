"""Latest mutual fund NAVs from AMFI's public end-of-day file.

    PYTHONPATH=src .venv/bin/python -m portfolio.prices      # refresh every source from the command line

This is the app's only outbound request: one GET of a fixed public URL. It
sends nothing about your holdings (no parameters, cookies or ISIN list). The
whole file is downloaded and matched here, and only the ISINs you hold are
stored, newest price only.

Nothing is estimated. A fund with no usable NAV simply has none.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
SOURCE = "AMFI"
MAX_BYTES = 10 * 1024 * 1024  # the file is about 1.5 MB
TIMEOUT = 30

ISIN = re.compile(r"IN[A-Z0-9]{10}")
MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
DATE = re.compile(r"(\d{2})-([A-Za-z]{3})-(\d{4})")


class PriceFetchError(RuntimeError):
    """A user-facing problem getting prices."""


@dataclass
class Nav:
    price: Decimal
    date: date


@dataclass
class NavTable:
    navs: dict[str, Nav] = field(default_factory=dict)
    rows: int = 0       # well-formed data lines
    skipped: int = 0    # malformed lines and rows with an unusable NAV or date
    ambiguous: int = 0  # ISINs listed twice, same date, different NAV: dropped


def _date(text: str) -> date | None:
    m = DATE.fullmatch(text.strip())
    if not m or m.group(2).upper() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
    except ValueError:
        return None


def parse_navall(text: str, strict: bool = False) -> NavTable:
    """NAVAll.txt: a header, then category and fund house lines, then `;`-separated rows:
    code; ISIN (payout or growth); ISIN (reinvestment); name; plan; option; NAV; date.

    The ISIN can be in either column. An ISIN listed more than once (matured fixed
    horizon funds) keeps its latest date; the same date with different NAVs is dropped.
    """
    table = NavTable()
    seen: dict[str, list[Nav]] = defaultdict(list)
    for line in text.splitlines():
        if ";" not in line or line.startswith("Scheme Code"):
            continue  # headings, blank lines, the header
        cells = [c.strip() for c in line.split(";")]
        if len(cells) != 8:
            table.skipped += 1
            continue
        table.rows += 1
        try:
            price = Decimal(cells[6])
        except InvalidOperation:
            price = Decimal(0)
        day = _date(cells[7])
        if price <= 0 or day is None:
            table.skipped += 1
            continue
        for isin in {c.upper() for c in cells[1:3] if ISIN.fullmatch(c.upper())}:
            seen[isin].append(Nav(price, day))
    for isin, navs in seen.items():
        latest = max(n.date for n in navs)
        current = [n for n in navs if n.date == latest]
        if len({n.price for n in current}) > 1:
            table.ambiguous += 1
        else:
            table.navs[isin] = current[0]
    if strict and not table.rows:
        raise PriceFetchError("The AMFI NAV file was not in the expected format, so nothing was updated.")
    return table


def fetch_navall(opener: Callable[..., Any] = urllib.request.urlopen) -> str:
    request = urllib.request.Request(AMFI_URL, method="GET", headers={"User-Agent": "cas-portfolio"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - network, TLS and HTTP errors all mean the same to the user
        raise PriceFetchError(f"Could not download the AMFI NAV file ({type(exc).__name__}).") from exc
    if len(body) > MAX_BYTES:
        raise PriceFetchError("The AMFI NAV file is unexpectedly too large, so it was ignored.")
    return body.decode("utf-8", errors="replace")


def refresh_nav(store: Any, fetch: Callable[[], str] = fetch_navall) -> dict[str, Any]:
    """Download the file and store the latest NAV for every fund ISIN you hold."""
    held = store.held_fund_isins()
    report: dict[str, Any] = {"held": len(held), "priced": 0, "unpriced": len(held), "latest_date": None,
                              "skipped": 0, "ambiguous": 0}
    if not held:
        return report  # nothing to price, so nothing to download
    table = parse_navall(fetch(), strict=True)  # a failure above raises before anything is stored
    found = {isin: (table.navs[isin].price, table.navs[isin].date) for isin in held if isin in table.navs}
    store.save_prices(SOURCE, found)
    newest = max((d for _, d in found.values()), default=None)
    report.update(
        priced=len(found), unpriced=len(held) - len(found), skipped=table.skipped, ambiguous=table.ambiguous,
        latest_date=newest.isoformat() if newest else None,
    )
    return report


def main() -> int:
    from portfolio.config import get_settings
    from pathlib import Path

    from portfolio.backup import safety_backup
    from portfolio.refresh import refresh_all
    from portfolio.store import Store

    settings = get_settings()
    if not settings.fetch_prices:
        print("Price fetching is turned off (PORTFOLIO_FETCH_PRICES=false).", file=sys.stderr)
        return 1
    try:
        result = refresh_all(Store.from_url(settings.database_url, before_upgrade=lambda path: safety_backup(
            path, Path(settings.backup_dir), settings.backup_gpg_recipient)))
    except PriceFetchError as exc:
        print(exc, file=sys.stderr)
        return 1
    for label, key in (("Fund NAVs (AMFI)", "nav"), ("Stocks, ETFs, bonds (NSE/BSE)", "exchange")):
        r = result[key]
        if r:
            print(f"{label}: {r['priced']} of {r['held']} priced" + (f", newest {r['latest_date']}." if r["latest_date"] else "."))
    for key, message in result["errors"].items():
        print(f"Warning: {message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
