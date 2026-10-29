"""First-in-first-out lots, checked against figures worked out by hand (made-up numbers)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from portfolio.lots import compute_lots, fund_lots, match_lots

Y0, Y1, Y2 = date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1)


def row(day, kind, amount, units):
    return {"date": day, "type": kind, "amount": D(amount), "units": D(units), "isin": "INF000000001", "scheme": "Some Fund"}


def fund(value, units, history="complete", key="INF000000001"):
    return {"key": key, "scheme": "Some Fund", "isin": key, "amc": "Some AMC", "value": D(value), "units": D(units),
            "nav_date": Y2, "history": history}


def test_a_sale_uses_the_oldest_lot_first_and_splits_across_lots():
    rows = [row(Y0, "PURCHASE", 1000, 10), row(Y1, "PURCHASE_SIP", 1200, 10), row(Y2, "REDEMPTION", -1800, -15)]
    open_lots, sales, problem = match_lots(rows)
    assert problem is None
    (sale,) = sales
    first, second = sale["pieces"]
    assert (first["units"], first["cost"], first["days_held"]) == (D(10), D(1000), 730)
    assert (second["units"], second["cost"], second["days_held"]) == (D(5), D(600), 365)
    assert sale["cost"] == D(1600) and sale["gain"] == D(200)
    assert first["proceeds"] + second["proceeds"] == D(1800)
    assert [(l["date"], l["left"]) for l in open_lots] == [(Y1, D(5))]


def test_fund_totals_and_holding_period_split():
    rows = [row(Y0, "PURCHASE", 1000, 10), row(Y1, "PURCHASE_SIP", 1200, 10), row(Y2, "REDEMPTION", -1800, -15)]
    r = fund_lots(fund(600, 5), rows, Y2)
    assert r["status"] == "ok"
    assert r["open_units"] == D(5) and r["open_cost"] == D(600) and r["unrealised_gain"] == D(0)
    assert r["realised_proceeds"] == D(1800) and r["realised_cost"] == D(1600) and r["realised_gain"] == D(200)
    # 10 units held 730 days: 1200 of the 1800 against cost 1000; 5 units held 365 days (not more than a year)
    assert r["realised_gain_held_over_a_year"] == D(200)
    assert r["realised_gain_held_up_to_a_year"] == D(0)
    (lot,) = r["open_lots"]
    assert lot["days_held"] == 365 and lot["over_a_year"] is False and lot["value"] == D(600)


@pytest.mark.parametrize("days,over", [(365, False), (366, True)])
def test_over_a_year_is_more_than_365_days(days, over):
    f = {**fund(100, 1), "nav_date": Y0 + timedelta(days=days)}
    r = fund_lots(f, [row(Y0, "PURCHASE", 100, 1)], Y2)
    assert r["open_lots"][0]["days_held"] == days and r["open_lots"][0]["over_a_year"] is over


def test_reinvested_dividends_open_lots_and_charges_are_ignored():
    rows = [row(Y0, "PURCHASE", 1000, 10), row(Y0, "STAMP_DUTY_TAX", 1, 0), row(Y1, "DIVIDEND_REINVEST", 50, 0.5),
            row(Y1, "DIVIDEND_PAYOUT", 20, 0)]
    r = fund_lots(fund(1100, "10.5"), rows, Y2)
    assert r["status"] == "ok" and r["open_cost"] == D(1050) and len(r["open_lots"]) == 2


def test_same_day_purchase_is_matched_before_the_sale():
    rows = [row(Y0, "REDEMPTION", -100, -1), row(Y0, "PURCHASE", 100, 1)]
    _, sales, problem = match_lots(rows)
    assert problem is None and sales[0]["gain"] == D(0)


def test_selling_more_than_was_bought_skips_the_fund():
    _, _, problem = match_lots([row(Y0, "PURCHASE", 100, 1), row(Y1, "REDEMPTION", -300, -2)])
    assert problem and "a purchase is missing" in problem


@pytest.mark.parametrize("bad", [row(Y0, "PURCHASE", -5, 1), row(Y0, "REDEMPTION", 5, -1), row(Y0, "TDS_TAX", 3, 0)])
def test_unclear_rows_skip_the_fund(bad):
    _, _, problem = match_lots([row(Y0, "PURCHASE", 100, 1), bad])
    assert problem


@pytest.mark.parametrize("history", ["incomplete", "unknown"])
def test_history_that_is_not_complete_is_skipped(history):
    r = fund_lots(fund(100, 1, history=history), [row(Y0, "PURCHASE", 100, 1)], Y2)
    assert r["status"] == "skipped" and r["open_lots"] == []


def test_units_that_do_not_add_up_are_skipped():
    r = fund_lots(fund(100, 2), [row(Y0, "PURCHASE", 100, 1)], Y2)
    assert r["status"] == "skipped" and "do not add up" in r["reason"]


def test_dashboard_lots_for_the_fixture(populated):
    store, view = populated
    out = compute_lots(store, view)
    by = {f["scheme"]: f for f in out["funds"]}
    one = by["Fund One"]  # 10 units for 1000 on Y0, 4 sold for 500 on Y1 (365 days), 6 left worth 110 each
    assert one["status"] == "ok" and one["realised_cost"] == D(400) and one["realised_gain"] == D(100)
    assert one["open_cost"] == D(600) and one["unrealised_gain"] == D(60)
    two = by["Fund Two"]
    assert [l["gain"] for l in two["open_lots"]] == [D(-40), D(40)]
    assert by["Fund Three"]["status"] == "skipped"
    t = out["totals"]
    assert t["funds_included"] == 2 and t["funds_total"] == 3 and t["realised_gain"] == D(100)


def test_lots_route_and_no_personal_data(populated, tmp_path):
    from fastapi.testclient import TestClient
    from portfolio.app import create_app
    from portfolio.config import Settings
    store, _ = populated
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/r.db", _env_file=None), store))
    resp = client.get("/api/lots")
    body = resp.json()
    assert resp.status_code == 200 and body["method"] and body["totals"]["funds_included"] == 2
    one = next(f for f in body["funds"] if f["scheme"] == "Fund One")
    assert one["sales"][0]["pieces"][0]["bought"] == "2025-01-01"
    for private in ("ABCDE1234F", "TEST USER", "t@example.com", "9999999999", "1111/22"):
        assert private not in resp.text
