"""Concentration: how much of the portfolio sits in a few positions or a few issuers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from casparser.types import Bond, MutualFund
from conftest import equity
from fastapi.testclient import TestClient

from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.store import Store

TODAY = date(2026, 10, 27)
# the standard fixture: HDFC Bank 34000, Infosys 29000, TCS 17000, Reliance 8700, Parag Parikh fund 4000,
# a liquid ETF 1000 and another fund 1000
VALUES = [34000, 29000, 17000, 8700, 4000, 1000, 1000]
TOTAL = sum(VALUES)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/conc.db")


def view(store, demat_data, **kw):
    cas.save(store, cas.from_demat_data(demat_data), "d")
    return build_view(store, today=TODAY, **kw)["concentration"]


def test_top_weights_and_count(store, demat_data):
    c = view(store, demat_data)
    assert TOTAL == 94700 and c["positions"] == 7
    assert c["top1"] == pytest.approx(34000 / TOTAL * 100, abs=1e-3)
    assert c["top5"] == pytest.approx(sum(VALUES[:5]) / TOTAL * 100, abs=1e-3)
    assert c["top10"] == pytest.approx(100.0)  # fewer than ten positions: all of them
    assert c["largest"]["label"] == "HDFC BANK LIMITED"


def test_effective_number_of_holdings(store, demat_data):
    c = view(store, demat_data)
    hhi = sum((v / TOTAL) ** 2 for v in VALUES)
    assert c["effective_holdings"] == pytest.approx(1 / hhi, abs=1e-3)
    assert 1 < c["effective_holdings"] < c["positions"]  # concentrated: fewer "effective" holdings than positions


def test_positions_above_the_threshold(store, demat_data):
    c = view(store, demat_data)
    assert c["alert_pct"] == 10 and [p["label"] for p in c["above_threshold"]] == [
        "HDFC BANK LIMITED", "INFOSYS LIMITED", "TATA CONSULTANCY SERVICES LIMITED"]  # Reliance is 9.2%
    c30 = build_view(store, today=TODAY, concentration_alert_pct=D(30))["concentration"]
    assert [p["label"] for p in c30["above_threshold"]] == ["HDFC BANK LIMITED", "INFOSYS LIMITED"]
    assert build_view(store, today=TODAY, concentration_alert_pct=D(50))["concentration"]["above_threshold"] == []


def test_top_positions_are_ranked_with_weights(store, demat_data):
    rows = view(store, demat_data)["top_positions"]
    assert [r["value"] for r in rows] == VALUES and len(rows) == 7
    assert rows[0]["weight"] == pytest.approx(35.9029, abs=1e-3)
    assert sum(r["weight"] for r in rows) == pytest.approx(100.0, abs=1e-2)


def test_the_same_fund_in_demat_and_in_a_folio_is_one_position(store, demat_data):
    folios = demat_data.accounts[-1]
    again = MutualFund(name="LIQ1 - Liquid fund", isin="INF000000001", balance=D(5), nav=D(100), value=D(500), folio="777")
    demat_data.accounts[-1] = folios.model_copy(update={"mutual_funds": [*folios.mutual_funds, again],
                                                        "balance": folios.balance + D(500)})
    c = view(store, demat_data)
    assert c["positions"] == 7  # not 8
    assert [r["value"] for r in c["top_positions"]].count(1500) == 1  # 1000 in demat + 500 in the folio, once


def test_issuers_group_securities_and_funds(store, demat_data):
    z, u = demat_data.accounts[0], demat_data.accounts[1]
    demat_data.accounts[0] = z.model_copy(update={"bonds": [
        Bond(name="SGB 2028 I", isin="IN0020200047", num_bonds=D(4), value=D(28000), market_price=D(7000)),
        Bond(name="SGB 2029 II", isin="IN0020210012", num_bonds=D(1), value=D(7000), market_price=D(7000)),
    ], "balance": z.balance + D(35000)})
    demat_data.accounts[1] = u.model_copy(update={"equities": [*u.equities, equity("INE009A07123", 2, "100", "INFOSYS NCD")],
                                                  "balance": u.balance + D(200)})
    c = view(store, demat_data)
    issuers = {r["label"]: r["value"] for r in c["top_issuers"]}
    assert c["top_issuers"][0]["label"] == "Government of India" and issuers["Government of India"] == 35000  # two SGB series, one issuer
    assert issuers["HDFC BANK LIMITED"] == 34000
    assert issuers["INFOSYS LIMITED"] == 29200  # the shares and the NCD share the issuer code; named after the bigger one
    assert c["largest_issuer"]["label"] == "Government of India"
    assert c["largest_issuer"]["weight"] == pytest.approx(35000 / (TOTAL + 35200) * 100, abs=1e-3)
    assert len(c["top_issuers"]) <= 8


def test_funds_are_grouped_by_fund_house(store, demat_data):
    c = view(store, demat_data)
    all_issuers = {r["label"]: r["value"] for r in c["top_issuers"]}
    assert all_issuers["PPFAS Mutual Fund"] == 4000
    assert all_issuers["Unknown fund house"] == 2000  # the ETF and the other fund have no fund house we know


def test_nothing_to_measure(store):
    assert build_view(store, today=TODAY)["concentration"] is None


def test_the_api_uses_the_configured_threshold(tmp_path, demat_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.db", concentration_alert_pct=30, _env_file=None)
    store = Store.from_url(settings.database_url)
    cas.save(store, cas.from_demat_data(demat_data), "d")
    import portfolio.app as app_module
    c = TestClient(app_module.create_app(settings, store)).get("/api/portfolio").json()["concentration"]
    assert c["alert_pct"] == 30 and len(c["above_threshold"]) == 2


def test_default_threshold_is_ten_percent():
    assert Settings(_env_file=None).concentration_alert_pct == 10
