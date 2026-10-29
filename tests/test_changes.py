"""What changed between two statement dates: holdings added, sold or repriced."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from conftest import equity
from fastapi.testclient import TestClient

from portfolio.changes import compare
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import Store

SEP, OCT = date(2026, 9, 30), date(2026, 10, 31)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/chg.db")


def _october(demat_data):
    """Next month: Infosys repriced and partly sold, TCS topped up, a new holding, the ETF repriced,
    one fund topped up, and the Upstox, HDFC and other-DP accounts gone."""
    d = demat_data.model_copy(deep=True)
    zerodha, folios = d.accounts[0], d.accounts[-1]
    etf = zerodha.mutual_funds[0].model_copy(update={"nav": D(110), "value": D(1100)})
    d.accounts = [
        zerodha.model_copy(update={"equities": [
            equity("INE009A01021", 12, "1500", "INFOSYS LIMITED"),
            equity("INE467B01029", 8, "3400", "TATA CONSULTANCY SERVICES"),
            equity("INE001A01036", 10, "100", "NEW CO"),
        ], "mutual_funds": [etf]}),
        folios.model_copy(update={"mutual_funds": [
            folios.mutual_funds[0].model_copy(update={"balance": D(60), "nav": D(85), "value": D(5100)}),
            folios.mutual_funds[1],
        ]}),
    ]
    d.statement_period = d.statement_period.model_copy(update={"to": "31-Oct-2026"})
    return d


@pytest.fixture
def two(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "sep")
    cas.save(store, cas.from_demat_data(_october(demat_data)), "oct")
    return store


def _by_isin(result):
    return {c["isin"]: c for c in result["demat"]["changes"]}


def test_demat_changes_split_into_quantity_and_price(two):
    c = _by_isin(compare(two, SEP, OCT))
    infy = c["INE009A01021"]
    assert infy["kind"] == "less" and (infy["quantity_before"], infy["quantity_after"]) == (20, 12)
    assert (infy["value_before"], infy["value_after"]) == (29000, 18000)
    assert (infy["quantity_effect"], infy["price_effect"]) == (-11600, 600)  # sells 8 at 1450; price +50 on 12
    tcs = c["INE467B01029"]
    assert tcs["kind"] == "more" and (tcs["quantity_effect"], tcs["price_effect"]) == (10200, 0)
    new = c["INE001A01036"]
    assert new["kind"] == "new" and (new["value_before"], new["value_after"]) == (0, 1000)
    assert (new["quantity_effect"], new["price_effect"]) == (1000, 0)
    for isin in ("INE040A01034", "INE002A01018"):  # HDFC Bank, Reliance: gone
        assert c[isin]["kind"] == "exited" and c[isin]["value_after"] == 0
    assert c["INE040A01034"]["quantity_effect"] == -34000 and c["INE040A01034"]["price_effect"] == 0
    assert c["INF000000001"]["kind"] == "price" and c["INF000000001"]["price_effect"] == 100


def test_totals_add_up_exactly(two):
    r = compare(two, SEP, OCT)["demat"]["totals"]
    assert (r["value_before"], r["value_after"], r["change"]) == (89700, 47300, -42400)
    assert (r["quantity_effect"], r["price_effect"]) == (-43100, 700)
    assert r["quantity_effect"] + r["price_effect"] == r["change"]


def test_changes_sorted_by_size_and_unchanged_holdings_left_out(two):
    changes = compare(two, SEP, OCT)["demat"]["changes"]
    moves = [abs(c["value_after"] - c["value_before"]) for c in changes]
    assert moves == sorted(moves, reverse=True)
    assert all(c["value_after"] != c["value_before"] or c["quantity_after"] != c["quantity_before"]
               for c in changes)


def test_fund_changes(two):
    funds = compare(two, SEP, OCT)["funds"]
    [ppfcf] = [c for c in funds["changes"] if c["key"] == "INF879O01027"]
    assert ppfcf["kind"] == "more" and (ppfcf["value_before"], ppfcf["value_after"]) == (4000, 5100)
    assert (ppfcf["quantity_effect"], ppfcf["price_effect"]) == (800, 300)
    assert all(c["key"] != "INF999Z01011" for c in funds["changes"])  # the other fund did not change


def test_disappeared_accounts_are_warned(two):
    warnings = " ".join(compare(two, SEP, OCT)["warnings"])
    for name in ("Upstox Securities", "HDFC Bank", "Some Other DP"):
        assert name in warnings
    assert "Zerodha Broking" not in warnings


def test_nothing_to_warn_when_nothing_changed(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "sep")
    d = demat_data.model_copy(deep=True)
    d.statement_period = d.statement_period.model_copy(update={"to": "31-Oct-2026"})
    cas.save(store, cas.from_demat_data(d), "oct")
    r = compare(store, SEP, OCT)
    assert r["demat"]["changes"] == [] and r["funds"]["changes"] == [] and r["warnings"] == []
    assert r["demat"]["totals"]["change"] == 0


def test_dates_must_be_in_order(two):
    with pytest.raises(ValueError):
        compare(two, OCT, SEP)
    with pytest.raises(ValueError):
        compare(two, SEP, SEP)


def test_a_date_with_no_statement_says_so(store, mf_data):
    cas.save(store, cas.from_mf_data(mf_data), "cams")  # funds only
    r = compare(store, date(2026, 8, 1), SEP)
    assert any("no demat statement" in w.lower() for w in r["warnings"])


def test_different_fund_sources_are_flagged(store, mf_data, demat_data):
    cas.save(store, cas.from_mf_data(mf_data), "cams-sep")           # funds from CAMS on 30 Sep
    cas.save(store, cas.from_demat_data(_october(demat_data)), "demat-oct")  # funds from CDSL on 31 Oct
    r = compare(store, SEP, OCT)
    assert (r["funds"]["source_before"], r["funds"]["source_after"]) == ("CAMS", "CDSL")
    assert any("different statements" in w for w in r["warnings"])


def test_endpoint(tmp_path, demat_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.db", _env_file=None)
    store = Store.from_url(settings.database_url)
    import portfolio.app as app_module
    client = TestClient(app_module.create_app(settings, store))
    assert client.get("/api/changes").status_code == 400  # fewer than two statements
    cas.save(store, cas.from_demat_data(demat_data), "sep")
    cas.save(store, cas.from_demat_data(_october(demat_data)), "oct")
    r = client.get("/api/changes")  # defaults to the two latest dates
    assert r.status_code == 200 and (r.json()["from"], r.json()["to"]) == ("2026-09-30", "2026-10-31")
    assert client.get("/api/changes?from_date=2026-09-30&to_date=2026-10-31").json() == r.json()
    assert client.get("/api/changes?from_date=2026-10-31&to_date=2026-09-30").status_code == 400
    assert client.get("/api/changes?from_date=junk&to_date=2026-09-30").status_code == 422
