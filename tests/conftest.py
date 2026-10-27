from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from casparser.enums import CASFileType, FileType, TransactionType
from casparser.types import (
    CASData, DematAccount, DematOwner, Equity, Folio, InvestorInfo, MutualFund, NSDLCASData, Scheme,
    SchemeValuation, StatementPeriod, TransactionData,
)

D = Decimal
INVESTOR = InvestorInfo(name="TEST USER", email="t@example.com", address="1 Test Street", mobile="9999999999")


def scheme(name, isin, units, nav, cost, *, scheme_type="EQUITY", transactions=(), calculated=None):
    return Scheme(
        scheme=name, rta_code="X1", rta="CAMS", type=scheme_type, isin=isin, amfi="100001",
        open=D(0), close=D(units), close_calculated=D(calculated if calculated is not None else units),
        valuation=SchemeValuation(date=date(2026, 9, 30), nav=D(nav), cost=D(cost) if cost is not None else None,
                                  value=D(units) * D(nav)),
        transactions=list(transactions),
    )


def txn(day, amount, units, nav, balance, kind=TransactionType.PURCHASE_SIP):
    return TransactionData(date=day, description="SIP Purchase", amount=D(amount), units=D(units),
                           nav=D(nav), balance=D(balance), type=kind)


@pytest.fixture
def mf_data():
    return CASData(
        statement_period=StatementPeriod(**{"from": "01-Apr-2026", "to": "30-Sep-2026"}),
        folios=[
            Folio(folio="1234567/89", amc="Parag Parikh Mutual Fund", PAN="ABCDE1234F", schemes=[
                scheme("Parag Parikh Flexi Cap Fund - Direct Growth", "INF879O01027", "100.5", "80", "6000",
                       transactions=[txn(date(2026, 9, 5), 1000, "12.5", 80, "100.5")]),
            ]),
            Folio(folio="99887766", amc="HDFC Mutual Fund", PAN="ABCDE1234F", schemes=[
                scheme("HDFC Liquid Fund - Direct Growth", "INF179K01VQ6", "2", "5000", "9500", scheme_type="DEBT"),
                # fully redeemed: transactions kept, no holding
                scheme("HDFC Index Nifty 50 - Direct", "INF179K01WA6", "0", "200", None,
                       transactions=[txn(date(2026, 6, 1), -5000, "-25", 200, "0", TransactionType.REDEMPTION)]),
            ]),
            # same scheme in a second folio: merged on the dashboard
            Folio(folio="5555444433", amc="Parag Parikh Mutual Fund", PAN="ABCDE1234F", schemes=[
                scheme("Parag Parikh Flexi Cap Fund - Direct Growth", "INF879O01027", "10", "80", "700"),
            ]),
        ],
        investor_info=INVESTOR,
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )


def equity(isin, qty, price, name=None):
    return Equity(name=name or isin, isin=isin, num_shares=D(qty), price=D(price), value=D(qty) * D(price))


def demat(dp_name, client_id, equities, kind="CDSL Demat Account", mfs=()):
    return DematAccount(
        name=dp_name, type=kind, dp_id="12081600", client_id=client_id, folios=0,
        balance=sum((e.value for e in equities), D(0)) + sum((m.value for m in mfs), D(0)),
        owners=[DematOwner(name="TEST USER", PAN="ABCDE1234F")], equities=list(equities), mutual_funds=list(mfs),
    )


@pytest.fixture
def demat_data():
    return NSDLCASData(
        accounts=[
            demat("ZERODHA BROKING LIMITED", "12345678", [
                equity("INE009A01021", 12, "1450", "INFOSYS LIMITED"),
                equity("INE467B01029", 5, "3400", "TATA CONSULTANCY SERVICES"),
                equity("INE000000000", 0, "10"),  # zero balance row: ignored
            ], mfs=[MutualFund(name="Liquid ETF", isin="INF000000001", balance=D(10), nav=D(100), value=D(1000))]),
            demat("UPSTOX SECURITIES PRIVATE LIMITED", "87654321", [equity("INE009A01021", 8, "1450")]),
            demat("HDFC BANK LIMITED", "11112222", [equity("INE040A01034", 20, "1700", "HDFC BANK")],
                  kind="NSDL Demat Account"),
            demat("SOME OTHER DP LTD", "99998888", [equity("INE002A01018", 3, "2900")]),
            DematAccount(name="CAMS", type="Mutual Fund Folios", folios=2, balance=D(5000), owners=[],
                         equities=[], mutual_funds=[
                             MutualFund(name="PPFCF - Parag Parikh Flexi Cap Fund - Direct Plan Growth",
                                        isin="INF879O01027", balance=D(50), nav=D(80), value=D(4000),
                                        total_cost=D(3000), folio="1234567/89"),
                             MutualFund(name="XYZ1 - Some Other Fund", isin="INF999Z01011", balance=D(10),
                                        nav=D(100), value=D(1000), folio="55554444"),
                         ]),
        ],
        statement_period=StatementPeriod(**{"from": "01-Sep-2026", "to": "30-Sep-2026"}),
        investor_info=INVESTOR,
        file_type=FileType.CDSL,
    )
