from __future__ import annotations

from datetime import date

import pytest

from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.store import Store

TODAY = date(2026, 10, 27)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/view.db")


def test_empty(store):
    v = build_view(store, today=TODAY)
    assert v["totals"]["value"] == 0 and v["stocks"] == [] and v["funds"] == []
    assert v["statements"] == {"demat": None, "mf": None}


def test_combined_view(store, mf_data, demat_data):
    cas.save(store, cas.from_mf_data(mf_data), "mf1")
    cas.save(store, cas.from_demat_data(demat_data), "demat1")
    v = build_view(store, today=TODAY)
    t = v["totals"]

    # Stocks: INFY held at Zerodha (12) and Upstox (8) appears once.
    assert t["stocks_count"] == 4 and t["multi_broker_stocks"] == 1
    infy = next(s for s in v["stocks"] if s["isin"] == "INE009A01021")
    assert infy["quantity"] == 20 and infy["value"] == 20 * 1450 and infy["price"] == 1450
    assert infy["brokers"] == ["upstox", "zerodha"] and len(infy["accounts"]) == 2
    assert t["stocks_value"] == 20 * 1450 + 5 * 3400 + 20 * 1700 + 3 * 2900
    assert t["demat_funds_value"] == 1000 and t["bonds_value"] == 0
    assert t["demat_value"] == t["stocks_value"] + 1000 and t["demat_count"] == 5
    etf = next(s for s in v["stocks"] if s["isin"] == "INF000000001")
    assert etf["asset_type"] == "fund"

    # Funds: Flexi Cap in two folios merges; costs and gains add up.
    assert t["funds_count"] == 2
    assert v["funds"][0]["scheme"].startswith("HDFC Liquid")  # sorted by value: 10000 > 8840
    flexi = next(f for f in v["funds"] if f["scheme"].startswith("Parag Parikh"))
    assert len(flexi["folios"]) == 2
    assert flexi["units"] == 110.5 and flexi["cost"] == 6700 and flexi["value"] == 8840
    assert flexi["gain"] == 2140 and flexi["gain_pct"] == pytest.approx(31.9403, abs=1e-3)
    assert t["mf_value"] == 8840 + 10000 and t["mf_cost"] == 6700 + 9500 and t["mf_gain"] == 2640

    assert t["value"] == t["demat_value"] + t["mf_value"]
    classes = {a["label"]: a["weight"] for a in v["allocation"]["asset_class"]}
    assert set(classes) == {"Stocks", "ETFs & demat funds", "Mutual funds (folios)"}  # no empty Bonds row
    assert sum(classes.values()) == pytest.approx(100)
    brokers = {a["label"] for a in v["allocation"]["broker"]}
    assert {"Zerodha", "Upstox", "HDFC Securities", "SOME OTHER DP LTD"} == brokers
    assert {a["label"] for a in v["allocation"]["fund_type"]} == {"Equity", "Debt"}

    s = v["statements"]["mf"]
    assert (s["issuer"], s["period_to"], s["age_days"], s["stale"]) == ("CAMS", "2026-09-30", 27, False)


def test_newer_statement_replaces_older(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "old")
    demat_data.statement_period = demat_data.statement_period.model_copy(update={"to": "31-Oct-2026"})
    demat_data.accounts = demat_data.accounts[:1]
    cas.save(store, cas.from_demat_data(demat_data), "new")
    v = build_view(store, today=TODAY)
    assert v["statements"]["demat"]["period_to"] == "2026-10-31"
    assert v["totals"]["demat_count"] == 3  # only the newest statement counts; nothing summed across


def test_stale_statement_flagged(store, mf_data):
    cas.save(store, cas.from_mf_data(mf_data), "mf1")
    assert build_view(store, stale_after_days=20, today=TODAY)["statements"]["mf"]["stale"] is True


def test_funds_without_cost(store, mf_data):
    s = mf_data.folios[1].schemes[0]
    mf_data.folios[1].schemes[0] = s.model_copy(update={"valuation": s.valuation.model_copy(update={"cost": None})})
    cas.save(store, cas.from_mf_data(mf_data), "mf1")
    t = build_view(store, today=TODAY)["totals"]
    assert t["mf_without_cost"] == 1
    assert t["mf_cost"] == 6700 and t["mf_gain"] == 2140  # gain only over funds that have cost



def test_funds_from_depository_statement_alone(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat1")
    v = build_view(store, today=TODAY)
    assert v["statements"]["mf"]["issuer"] == "CDSL"
    assert v["totals"]["mf_value"] == 5000 and v["totals"]["mf_cost"] == 3000 and v["totals"]["mf_without_cost"] == 1
    assert v["totals"]["value"] == v["totals"]["demat_value"] + 5000


def test_fund_source_is_one_statement_never_both(store, mf_data, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat-sep")     # folios worth 5000, to 30 Sep
    cas.save(store, cas.from_mf_data(mf_data), "cams-sep")            # funds worth 18840, to 30 Sep
    v = build_view(store, today=TODAY)
    assert v["statements"]["mf"]["issuer"] == "CAMS" and v["totals"]["mf_value"] == 18840  # same date: CAMS wins

    demat_data.statement_period = demat_data.statement_period.model_copy(update={"to": "31-Oct-2026"})
    cas.save(store, cas.from_demat_data(demat_data), "demat-oct")
    v = build_view(store, today=date(2026, 11, 5))
    assert v["statements"]["mf"]["issuer"] == "CDSL" and v["totals"]["mf_value"] == 5000  # newer wins, not summed


# ---- as-of range of the total -------------------------------------------------------


def _with_end(data, end):
    data.statement_period = data.statement_period.model_copy(update={"to": end})
    return data


def test_as_of_is_none_without_statements(store):
    assert build_view(store, today=TODAY)["as_of"] is None


def test_as_of_single_date_when_statements_agree(store, mf_data, demat_data):
    cas.save(store, cas.from_mf_data(mf_data), "mf1")
    cas.save(store, cas.from_demat_data(demat_data), "demat1")
    assert build_view(store, today=TODAY)["as_of"] == {
        "from": "2026-09-30", "to": "2026-09-30", "spread_days": 0, "mixed": False}


def test_as_of_one_statement_alone(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat1")  # its folio section supplies the funds too
    a = build_view(store, today=TODAY)["as_of"]
    assert a["from"] == a["to"] == "2026-09-30" and a["mixed"] is False


def test_small_gap_is_not_flagged(store, mf_data, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat1")
    cas.save(store, cas.from_mf_data(_with_end(mf_data, "05-Oct-2026")), "mf1")
    a = build_view(store, today=TODAY)["as_of"]
    assert (a["from"], a["to"], a["spread_days"], a["mixed"]) == ("2026-09-30", "2026-10-05", 5, False)


def test_wide_gap_is_flagged(store, mf_data, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat1")  # stocks to 30 Sep
    cas.save(store, cas.from_mf_data(_with_end(mf_data, "25-Oct-2026")), "mf1")  # funds to 25 Oct
    a = build_view(store, today=TODAY)["as_of"]
    assert (a["from"], a["to"], a["spread_days"], a["mixed"]) == ("2026-09-30", "2026-10-25", 25, True)
