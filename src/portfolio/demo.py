"""Demo mode: a made-up portfolio for trying the app without a statement.

`PORTFOLIO_DEMO=true` builds a throw-away database in a temporary folder, fills it with two invented statements
(a CAMS-style fund statement and a demat statement) through the normal import code, and serves that. Nothing here
belongs to anyone. The real database, its backups and the price sources are never touched, and the temporary
folder is deleted when the server stops.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from casparser.enums import CASFileType, FileType, TransactionType
from casparser.types import (
    CASData, DematAccount, DematOwner, Equity, Folio, InvestorInfo, NSDLCASData, Scheme,
    SchemeValuation, StatementPeriod, TransactionData,
)

from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import Store

D = Decimal
INVESTOR = InvestorInfo(name="DEMO USER", email="demo@example.com", address="1 Demo Street", mobile="9999999999")  # privacy-ok
PAN = "ABCDE1234F"  # privacy-ok: made up
MONTHS = 18


def _fund(name: str, isin: str, monthly: int, nav_then: int, nav_now: int, end: date) -> Scheme:
    """A SIP of `monthly` rupees for MONTHS months at a price that drifts from nav_then to nav_now."""
    txns, units = [], D(0)
    for i in range(MONTHS):
        day = end - timedelta(days=30 * (MONTHS - i))
        nav = D(nav_then) + (D(nav_now) - D(nav_then)) * i / MONTHS
        bought = (D(monthly) / nav).quantize(D("0.001"))
        units += bought
        txns.append(TransactionData(date=day, description="SIP Purchase", amount=D(monthly), units=bought, nav=nav.quantize(D("0.01")),
                                    balance=units, type=TransactionType.PURCHASE_SIP))
    value = (units * D(nav_now)).quantize(D("0.01"))
    return Scheme(scheme=name, rta_code="D1", rta="CAMS", type="EQUITY", isin=isin, amfi="100001", open=D(0), close=units,
                  close_calculated=units, valuation=SchemeValuation(date=end, nav=D(nav_now), cost=D(monthly * MONTHS), value=value),
                  transactions=txns)


def _equity(isin: str, name: str, qty: int, price: int) -> Equity:
    return Equity(name=name, isin=isin, num_shares=D(qty), price=D(price), value=D(qty) * D(price))


def demo_statements(today: date | None = None) -> tuple[cas.ParsedStatement, cas.ParsedStatement]:
    end = (today or date.today()) - timedelta(days=3)
    start = end - timedelta(days=30 * MONTHS + 30)
    period = {"from": start.strftime("%d-%b-%Y"), "to": end.strftime("%d-%b-%Y")}
    mf = CASData(
        statement_period=StatementPeriod(**period),
        folios=[Folio(folio="0000111122", amc="Demo Asset Management", PAN=PAN, schemes=[  # privacy-ok: made-up folio
            _fund("Demo Flexi Cap Fund - Direct Growth", "INF000D01011", 5000, 100, 128, end),
            _fund("Demo Index 50 Fund - Direct Growth", "INF000D01029", 3000, 200, 231, end),
        ])],
        investor_info=INVESTOR, cas_type=CASFileType.DETAILED, file_type=FileType.CAMS)
    stocks = [_equity("INE009A01021", "INFOSYS LIMITED", 15, 1500), _equity("INE467B01029", "TATA CONSULTANCY SERVICES", 6, 3500),
              _equity("INE040A01034", "HDFC BANK", 30, 1650)]
    account = DematAccount(
        name="DEMO BROKING LIMITED", type="CDSL Demat Account", dp_id="00000000", client_id="00000001", folios=0,  # privacy-ok
        balance=sum((e.value for e in stocks), D(0)), owners=[DematOwner(name="DEMO USER", PAN=PAN)], equities=stocks, mutual_funds=[])
    demat = NSDLCASData(accounts=[account], statement_period=StatementPeriod(**period), investor_info=INVESTOR, file_type=FileType.CDSL)
    return cas.from_mf_data(mf), cas.from_demat_data(demat)


def demo_settings(settings: Settings) -> tuple[Settings, Store]:
    """Settings that point at a temporary database, and that database filled with the demo portfolio."""
    folder = Path(tempfile.mkdtemp(prefix="portfolio-demo-"))
    atexit.register(shutil.rmtree, folder, True)
    demo = settings.model_copy(update={
        "database_url": f"sqlite:///{folder / 'demo.db'}", "backup_dir": str(folder / "backups"),
        "backup_gpg_recipient": None, "fetch_prices": False, "demo": True})
    store = Store.from_url(demo.database_url)
    for number, statement in enumerate(demo_statements()):
        cas.save(store, statement, f"demo-{number}".ljust(64, "0"))
    return demo, store
