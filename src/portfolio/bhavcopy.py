"""End-of-day prices for stocks, ETFs and bonds from the exchanges' public bhavcopy files.

NSE publishes a zip and BSE a plain CSV, both in the same layout (one row per
ISIN with a closing price). The file name carries the trade date, so the
latest file is found by trying today and walking back over weekends and
holidays.

Like the AMFI download, this sends nothing about your holdings: a plain GET of
a fixed public URL that differs only by date. If an exchange refuses, the app
says so and stops. It does not retry with other headers or otherwise work
around the refusal.

NSE is tried first. BSE is downloaded only if NSE lacks some of your ISINs.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from portfolio.prices import Nav, NavTable, PriceFetchError

URLS = {
    "NSE": "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip",
    "BSE": "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV",
}
MAX_BYTES = 10 * 1024 * 1024  # the files are under 1 MB, compressed or not
TIMEOUT = 30
LOOKBACK_DAYS = 7
REQUIRED = {"ISIN", "ClsPric", "TradDt"}
IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(IST).date()


def _looks_like_the_file(text: str) -> bool:
    first = text.lstrip("﻿").split("\n", 1)[0]
    return all(column in first for column in REQUIRED)


def fetch_day(exchange: str, day: date, opener: Callable[..., Any] = urllib.request.urlopen) -> str | None:
    """The file for one trade date, or None if the exchange has none for it."""
    url = URLS[exchange].format(d=day.strftime("%Y%m%d"))
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "cas-portfolio"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403, 429):
            raise PriceFetchError(f"{exchange} refused the request (HTTP {exc.code}). The app does not work "
                                  "around that; try again later or turn off price fetching.") from exc
        raise PriceFetchError(f"{exchange} answered HTTP {exc.code}.") from exc
    except Exception as exc:  # noqa: BLE001 - network and TLS errors all mean the same to the user
        raise PriceFetchError(f"Could not download the {exchange} price file ({type(exc).__name__}).") from exc
    if len(body) > MAX_BYTES:
        raise PriceFetchError(f"The {exchange} price file is unexpectedly too large, so it was ignored.")
    if exchange == "NSE":
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            return None  # a web page instead of a zip: no file for that day
        members = [m for m in archive.infolist() if m.filename.lower().endswith(".csv")]
        if not members:
            return None
        if members[0].file_size > MAX_BYTES:
            raise PriceFetchError("The NSE price file is unexpectedly too large, so it was ignored.")
        with archive.open(members[0]) as member:
            body = member.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise PriceFetchError("The NSE price file is unexpectedly too large, so it was ignored.")
    text = body.decode("utf-8", errors="replace")
    # BSE answers 200 with a web page for a day it has no file for.
    return text if _looks_like_the_file(text) else None


def fetch_latest(exchange: str, today: date, opener: Callable[..., Any] = urllib.request.urlopen) -> tuple[date, str]:
    for back in range(LOOKBACK_DAYS):
        day = today - timedelta(days=back)
        text = fetch_day(exchange, day, opener=opener)
        if text is not None:
            return day, text
    raise PriceFetchError(f"No {exchange} price file was found for the last {LOOKBACK_DAYS} days.")


def parse_bhavcopy(text: str) -> NavTable:
    """One closing price per ISIN. Unusable rows are counted and skipped; an ISIN listed
    twice with different prices is dropped rather than guessed."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not REQUIRED <= set(reader.fieldnames or ()):
        raise PriceFetchError("The exchange file was not in the expected format, so nothing was updated.")
    table = NavTable()
    seen: dict[str, list[Nav]] = defaultdict(list)
    for row in reader:
        isin = (row.get("ISIN") or "").strip().upper()
        if not isin:
            continue
        table.rows += 1
        try:
            price = Decimal((row.get("ClsPric") or "").strip())
            day = date.fromisoformat((row.get("TradDt") or "").strip())
        except (InvalidOperation, ValueError):
            table.skipped += 1
            continue
        if price <= 0:
            table.skipped += 1
            continue
        seen[isin].append(Nav(price, day))
    for isin, found in seen.items():
        if len({(n.price, n.date) for n in found}) > 1:
            table.ambiguous += 1
        else:
            table.navs[isin] = found[0]
    return table


def refresh_exchange(store: Any, fetch: Callable[[str], tuple[date, str]] | None = None) -> dict[str, Any]:
    """Store the latest close for every ISIN you hold: NSE first, BSE only for what NSE lacks."""
    fetch = fetch or (lambda exchange: fetch_latest(exchange, today_ist()))
    held = store.held_exchange_isins()
    report: dict[str, Any] = {"held": len(held), "priced": 0, "unpriced": len(held), "latest_date": None,
                              "sources": {}, "warnings": []}
    if not held:
        return report
    _, text = fetch("NSE")  # a failure here raises before anything is stored
    nse = parse_bhavcopy(text)
    found = {"NSE": {i: (nse.navs[i].price, nse.navs[i].date) for i in held if i in nse.navs}, "BSE": {}}
    missing = held - set(found["NSE"])
    if missing:
        try:
            _, text = fetch("BSE")
            bse = parse_bhavcopy(text)
            found["BSE"] = {i: (bse.navs[i].price, bse.navs[i].date) for i in missing if i in bse.navs}
        except PriceFetchError as exc:
            report["warnings"].append(str(exc))  # keep what NSE gave
    for source, prices in found.items():
        store.save_prices(source, prices)
    priced = sum(len(p) for p in found.values())
    newest = max((d for p in found.values() for _, d in p.values()), default=None)
    report.update(priced=priced, unpriced=len(held) - priced, latest_date=newest.isoformat() if newest else None,
                  sources={s: len(p) for s, p in found.items() if p})
    return report
