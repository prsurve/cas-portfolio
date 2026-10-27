"""One holding, explained: every number traces to a statement, a date and a method."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from casparser.types import StatementPeriod
from fastapi.testclient import TestClient

from portfolio.config import Settings
from portfolio.holdings import holding_detail
from portfolio.importers import cas
from portfolio.store import Store

TODAY = date(2026, 10, 27)
INFY, TCS, PPFCF, LIQUID_FUND = "INE009A01021", "INE467B01029", "INF879O01027", "INF179K01VQ6"
ETF = "INF000000001"


@pytest.fixture
def store(tmp_path, mf_data, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/holding.db")
    older = demat_data.model_copy(deep=True)
    older.statement_period = StatementPeriod(**{"from": "01-Aug-2026", "to": "31-Aug-2026"})
    older.accounts[0].equities.pop(1)  # TCS was not held yet
    for i, st in enumerate((cas.from_demat_data(older), cas.from_demat_data(demat_data), cas.from_mf_data(mf_data))):
        cas.save(s, st, str(i) * 64)
    return s


def calc(detail, id_):
    return next(c for c in detail["calculations"] if c["id"] == id_)


def test_unknown_holding_is_none(store):
    assert holding_detail(store, "INE000000000", today=TODAY) is None


def test_stock_in_two_accounts_shows_each_account_and_sums_to_the_view(store):
    d = holding_detail(store, INFY, today=TODAY)
    assert d["kind"] == "Stock" and d["name"] == "INFOSYS LIMITED"
    [part] = d["components"]
    assert part["source"] == "demat" and part["quantity"] == 20 and part["value"] == 20 * 1450
    assert {a["label"] for a in part["accounts"]} == {"Upstox Securities", "Zerodha Broking"}
    c = calc(d, "statement_value_demat")
    assert c["value"] == 29000 and sum(i["value"] for i in c["inputs"]) == c["value"]  # the inputs ARE the sum
    assert c["as_of"] == "2026-09-30" and c["source"] == "CDSL" and "dated 2026-09-30" in c["method"]
    assert "price shown" in c["limitation"]


def test_stock_has_no_cost_and_says_why(store):
    d = holding_detail(store, INFY, today=TODAY)
    assert not any(c["id"] in ("cost", "gain") for c in d["calculations"])
    assert any("do not include what you paid" in n for n in d["notes"])


def test_weight_is_value_over_the_dashboard_total(store):
    d = holding_detail(store, INFY, today=TODAY)
    w = calc(d, "weight")
    holding, total = w["inputs"][0]["value"], w["inputs"][1]["value"]
    assert holding == 29000 and w["value"] == pytest.approx(holding / total * 100, abs=1e-3)
    assert d["weight"] == w["value"]


def test_fund_with_cost_explains_cost_and_gain(store):
    d = holding_detail(store, PPFCF, today=TODAY)
    assert d["kind"] == "Mutual fund" and d["amc"] == "Parag Parikh Mutual Fund"
    [part] = d["components"]
    assert part["source"] == "folio" and len(part["folios"]) == 2  # two folios merge into one holding
    value, cost = calc(d, "statement_value_folio"), calc(d, "cost")
    assert sum(i["value"] for i in value["inputs"]) == value["value"] == 110.5 * 80
    assert sum(i["value"] for i in cost["inputs"]) == cost["value"] == 6700
    gain = calc(d, "gain")
    assert gain["value"] == value["value"] - cost["value"]
    assert "not a return over time" in gain["method"]


def test_fund_without_cost_says_no_gain_is_shown(tmp_path, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/demat-only.db")
    cas.save(s, cas.from_demat_data(demat_data), "d" * 64)  # its folio section has one fund with a cost and one without
    d = holding_detail(s, "INF999Z01011", today=TODAY)
    cost = calc(d, "cost")
    assert cost["value"] is None and "no amount invested" in cost["limitation"]
    assert not any(c["id"] == "gain" for c in d["calculations"])
    with_cost = holding_detail(s, "INF879O01027", today=TODAY)
    assert calc(with_cost, "cost")["value"] == 3000 and calc(with_cost, "gain")["value"] == 1000


def test_fund_history_note_follows_what_the_transactions_cover(store):
    d = holding_detail(store, PPFCF, today=TODAY)
    history = d["components"][0]["history"]
    assert history in ("complete", "incomplete", "unknown")
    has_note = any("XIRR" in n for n in d["notes"])
    assert has_note == (history != "complete"), "a note about missing returns appears exactly when history is not complete"


def test_latest_value_is_shown_beside_the_statement_value_with_source_and_date(store):
    store.save_prices("NSE", {INFY: (Decimal("1500"), date(2026, 10, 26))})
    d = holding_detail(store, INFY, today=TODAY)
    latest = calc(d, "latest_value")
    assert latest["value"] == 20 * 1500 and latest["source"] == "NSE" and latest["as_of"] == "2026-10-26"
    labels = {i["label"]: i["value"] for i in latest["inputs"]}
    assert labels["Statement value"] == 29000 and labels["Change from statement value"] == 1000
    assert calc(d, "statement_value_demat")["value"] == 29000, "the statement value is never replaced by the price"
    assert d["value"] == 29000, "totals stay statement-based"


def test_missing_latest_price_is_explained_not_estimated(store):
    latest = calc(holding_detail(store, INFY, today=TODAY), "latest_value")
    assert latest["value"] is None and "Nothing is estimated" in latest["limitation"]


def test_price_older_than_the_statement_is_not_used(store):
    store.save_prices("NSE", {INFY: (Decimal("1"), date(2026, 9, 1))})
    assert calc(holding_detail(store, INFY, today=TODAY), "latest_value")["value"] is None


def test_a_share_never_gets_a_fund_nav(store):
    store.save_prices("AMFI", {INFY: (Decimal("5"), date(2026, 10, 26))})
    assert calc(holding_detail(store, INFY, today=TODAY), "latest_value")["value"] is None


def test_history_has_one_point_per_change_and_never_sums_across_statements(store):
    d = holding_detail(store, TCS, today=TODAY)
    assert [p["date"] for p in d["history"]] == ["2026-09-30"], "TCS was not held in August"
    infy = holding_detail(store, INFY, today=TODAY)
    values = {p["date"]: p["value"] for p in infy["history"]}
    assert values == {"2026-08-31": 20 * 1450, "2026-09-30": 20 * 1450}
    assert all(len(p["parts"]) == 1 for p in infy["history"])
    assert "not a return" in infy["history_note"]


def test_fund_history_points_name_the_statement_each_part_came_from(store):
    """The demat statements list this fund in their folio section too, so before the CAMS statement it came from there."""
    d = holding_detail(store, PPFCF, today=TODAY)
    assert [(p["date"], p["parts"][0]["issuer"], p["parts"][0]["statement_date"], p["value"]) for p in d["history"]] == [
        ("2026-08-31", "CDSL", "2026-08-31", 4000.0), ("2026-09-30", "CAMS", "2026-09-30", 8840.0)]
    assert all(p["parts"][0]["source"] == "folio" for p in d["history"])


def test_same_isin_in_demat_and_folio_is_both_counted_and_labelled(store, tmp_path, mf_data, demat_data):
    # the liquid ETF is a demat holding; give the same ISIN a folio too
    mf = cas.from_mf_data(mf_data)
    mf.mfs.append({**mf.mfs[0], "isin": ETF, "scheme": "Liquid ETF", "folio_masked": "****9", "units": Decimal(1),
                   "value": Decimal(100), "cost": None})
    s = Store.from_url(f"sqlite:///{tmp_path}/both.db")
    cas.save(s, cas.from_demat_data(demat_data), "a" * 64)
    cas.save(s, mf, "b" * 64)
    d = holding_detail(s, ETF, today=TODAY)
    assert [c["source"] for c in d["components"]] == ["demat", "folio"]
    assert d["value"] == 1000 + 100
    assert any("both counted" in n or "both are counted" in n for n in d["notes"])


def test_route(store, tmp_path):
    from portfolio.app import create_app
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/holding.db", _env_file=None), store))
    r = client.get(f"/api/holdings/{INFY}")
    assert r.status_code == 200 and r.json()["isin"] == INFY and r.json()["calculations"]
    assert client.get("/api/holdings/INE000000000").status_code == 404
    # a fund without an ISIN is reached by its name, which can contain spaces and slashes
    assert client.get("/api/holdings/Some%20Fund%2FPlan").status_code == 404


def test_no_personal_data_in_the_detail(store):
    text = str(holding_detail(store, INFY, today=TODAY)) + str(holding_detail(store, PPFCF, today=TODAY))
    for private in ("ABCDE1234F", "TEST USER", "12345678", "1234567/89", "9999999999", "5555444433"):
        assert private not in text
