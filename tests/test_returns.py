"""XIRR of mutual funds, checked against rates worked out by hand.

The dates are chosen so the day counts are whole years (365 or 730 days), which makes the true rate exact:
 - put in 1000, get 1100 a year later                         -> 10%
 - put in 100 and 100 a year apart, 231 two years after the first -> 10%   (231x^2 - 100x - 100 = 0, x = 10/11)
 - put in 1000, take out 500 after a year, 660 left after two     -> 10%   (660x^2 + 500x - 1000 = 0, x = 10/11)
"""
from __future__ import annotations

import time
from datetime import date
from decimal import Decimal as D

import pytest

from portfolio.returns import cash_flows, compute_returns, fund_return, xirr

Y0, Y1, Y2 = date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1)  # 365 days apart each


def rate(flows):
    r = xirr(flows)
    assert r.reason is None, r.reason
    return r.rate


def row(day, kind, amount, units, isin="INF000000001"):
    return {"date": day, "type": kind, "amount": D(amount), "units": D(units), "isin": isin, "scheme": "Some Fund"}


def fund(value, units, history="complete", nav_date=Y2, key="INF000000001"):
    return {"key": key, "scheme": "Some Fund", "isin": key, "amc": "Some AMC", "value": D(value), "units": D(units),
            "nav_date": nav_date, "history": history}


# ---- the solver ----------------------------------------------------------------------------

def test_one_purchase_one_value_is_exactly_ten_percent():
    assert rate([(Y0, D(-1000)), (Y1, D(1100))]) == D("0.1")


def test_two_purchases_worked_out_by_hand():
    assert rate([(Y0, D(-100)), (Y1, D(-100)), (Y2, D(231))]) == D("0.1")


def test_a_redemption_in_between_worked_out_by_hand():
    assert rate([(Y0, D(-1000)), (Y1, D(500)), (Y2, D(660))]) == D("0.1")


def test_a_loss_is_a_negative_rate():
    assert rate([(Y0, D(-1000)), (Y1, D(900))]) == D("-0.1")


def test_no_gain_no_loss_is_zero():
    assert abs(rate([(Y0, D(-100)), (Y1, D(-100)), (Y2, D(200))])) < D("0.000001")


def test_order_of_the_flows_does_not_matter_and_same_day_flows_are_merged():
    a = rate([(Y1, D(1100)), (Y0, D(-1000))])
    b = rate([(Y0, D(-600)), (Y0, D(-400)), (Y1, D(1100))])
    assert a == b == D("0.1")


def test_a_gain_of_more_than_a_hundred_percent_a_year_is_found():
    assert rate([(Y0, D(-100)), (Y1, D(500))]) == D("4")


def test_flows_in_one_direction_have_no_rate():
    r = xirr([(Y0, D(-100)), (Y1, D(-50))])
    assert r.rate is None and "one direction" in r.reason
    assert xirr([]).rate is None


def test_flows_that_fit_two_rates_give_none():
    """-100, +230, -132 a year apart balances at both 10% and 20%: an honest answer is that there is no single rate."""
    r = xirr([(Y0, D(-100)), (Y1, D(230)), (Y2, D(-132))])
    assert r.rate is None and "more than one rate" in r.reason


def test_the_answer_is_deterministic():
    flows = [(date(2024, 3, 1), D(-5000)), (date(2024, 9, 17), D(-3000)), (date(2025, 11, 2), D(-2000)), (date(2026, 9, 30), D(11400))]
    assert xirr(flows) == xirr(list(reversed(flows)))


def test_a_monthly_sip_is_solved_quickly():
    flows = [(date(2023 + (m // 12), m % 12 + 1, 5), D(-5000)) for m in range(36)] + [(date(2026, 10, 5), D(210000))]
    start = time.perf_counter()
    r = xirr(flows)
    elapsed = time.perf_counter() - start
    assert r.rate is not None and D("0.05") < r.rate < D("0.30")
    assert elapsed < 3, f"too slow for the Pi: {elapsed:.2f}s for 37 flows"


# ---- cash flows from statement rows -----------------------------------------------------------

def test_purchases_are_money_in_and_redemptions_money_out():
    flows, problem = cash_flows([row(Y0, "PURCHASE", 1000, 10), row(Y1, "REDEMPTION", -400, -4), row(Y1, "DIVIDEND_PAYOUT", 25, 0)])
    assert problem is None
    assert flows == [(Y0, D(-1000)), (Y1, D(400)), (Y1, D(25))]


def test_reinvested_dividends_move_no_money():
    flows, _ = cash_flows([row(Y0, "PURCHASE", 1000, 10), row(Y1, "DIVIDEND_REINVEST", 50, 0.5)])
    assert flows == [(Y0, D(-1000))]


def test_stamp_duty_counts_as_money_in():
    flows, _ = cash_flows([row(Y0, "PURCHASE", 999, 10), row(Y0, "STAMP_DUTY_TAX", 1, 0)])
    assert sum(a for _, a in flows) == D(-1000)


@pytest.mark.parametrize("kind", ["TDS_TAX", "GIFT_IN", "REVERSAL", "SEGREGATION", "MISC", "UNKNOWN"])
def test_kinds_whose_effect_is_unclear_are_not_guessed(kind):
    flows, problem = cash_flows([row(Y0, "PURCHASE", 1000, 10), row(Y1, kind, 5, 0)])
    assert flows == [] and problem and "does not work out" in problem


def test_a_zero_row_of_an_unclear_kind_is_harmless():
    flows, problem = cash_flows([row(Y0, "PURCHASE", 1000, 10), row(Y1, "MISC", 0, 0)])
    assert problem is None and len(flows) == 1


@pytest.mark.parametrize("kind,amount", [("PURCHASE", -100), ("REDEMPTION", 100), ("DIVIDEND_PAYOUT", -5)])
def test_an_amount_of_the_unexpected_sign_is_not_trusted(kind, amount):
    _, problem = cash_flows([row(Y0, kind, amount, 1)])
    assert problem and "unexpected sign" in problem


# ---- one fund ------------------------------------------------------------------------------

def test_a_fund_with_complete_history_gets_a_rate_and_its_inputs():
    r = fund_return(fund(1100, 10, nav_date=Y1), [row(Y0, "PURCHASE", 1000, 10)], Y1)
    assert r["status"] == "ok" and r["xirr"] == D("0.1")
    assert (r["invested"], r["withdrawn"], r["gain"], r["flows"]) == (D(1000), D(0), D(100), 1)
    assert r["since"] == Y0 and r["days"] == 365 and r["short_history"] is False


def test_gain_counts_what_was_taken_out():
    r = fund_return(fund(660, 6), [row(Y0, "PURCHASE", 1000, 10), row(Y1, "REDEMPTION", -500, -4)], Y2)
    assert r["xirr"] == D("0.1") and r["gain"] == D(660 + 500 - 1000) and r["withdrawn"] == D(500)


def test_less_than_a_year_is_flagged():
    r = fund_return(fund(1020, 10, nav_date=date(2025, 7, 1)), [row(Y0, "PURCHASE", 1000, 10)], date(2025, 7, 1))
    assert r["status"] == "ok" and r["short_history"] is True and r["days"] == 181


@pytest.mark.parametrize("history,text", [("incomplete", "do not describe the whole holding"), ("unknown", "does not say")])
def test_funds_without_complete_history_are_skipped_with_the_reason(history, text):
    r = fund_return(fund(1100, 10, history=history), [row(Y0, "PURCHASE", 1000, 10)], Y1)
    assert r["status"] == "skipped" and r["xirr"] is None and text in r["reason"]


def test_a_fund_without_transactions_is_skipped():
    r = fund_return(fund(1100, 10), [], Y1)
    assert r["status"] == "skipped" and "No transactions" in r["reason"]


def test_transactions_that_do_not_add_up_to_the_units_held_are_skipped():
    r = fund_return(fund(1100, 12), [row(Y0, "PURCHASE", 1000, 10)], Y1)
    assert r["status"] == "skipped" and "do not add up" in r["reason"]


def test_a_fund_with_a_kind_that_is_not_valued_is_skipped():
    r = fund_return(fund(1100, 10), [row(Y0, "PURCHASE", 1000, 10), row(Y0, "TDS_TAX", 3, 0)], Y1)
    assert r["status"] == "skipped" and "does not work out" in r["reason"]


# ---- the dashboard -------------------------------------------------------------------------

def test_every_fund_is_listed_and_the_incomplete_one_says_why(populated):
    store, view = populated
    out = compute_returns(store, view)
    by_name = {r["scheme"]: r for r in out["funds"]}
    assert by_name["Fund One"]["status"] == "ok" and by_name["Fund One"]["xirr"] == D("0.1")  # 1000 in, 500 out after a year, 660 left
    assert by_name["Fund Two"]["status"] == "ok" and by_name["Fund Two"]["gain"] == D(0)
    assert by_name["Fund Three"]["status"] == "skipped" and "do not describe the whole holding" in by_name["Fund Three"]["reason"]
    assert out["valuation_date"] == Y2


def test_portfolio_rate_says_how_much_of_the_portfolio_it_covers(populated):
    store, view = populated
    p = compute_returns(store, view)["portfolio"]
    assert p["status"] == "ok" and p["funds_included"] == 2 and p["funds_total"] == 3
    assert p["value_included"] == D(660 + 100) and p["value_total"] == D(660 + 100 + 100)
    assert abs(p["coverage_pct"] - D(760) / D(860) * 100) < D("0.0001")
    assert p["invested"] == D(1000 + 100) and p["withdrawn"] == D(500)
    assert D("0.05") < p["xirr"] < D("0.12")


def test_without_a_cams_statement_nothing_is_calculated(tmp_path, demat_data):
    from portfolio.importers import cas
    from portfolio.portfolio_view import compute_view
    from portfolio.store import Store
    store = Store.from_url(f"sqlite:///{tmp_path}/d.db")
    cas.save(store, cas.from_demat_data(demat_data), "d" * 64)
    out = compute_returns(store, compute_view(store, today=date(2026, 10, 27)))
    assert all(r["status"] == "skipped" and "No transactions" in r["reason"] for r in out["funds"])
    assert out["portfolio"]["status"] == "skipped" and out["portfolio"]["funds_included"] == 0


def test_returns_route_and_no_personal_data(populated, tmp_path):
    from fastapi.testclient import TestClient
    from portfolio.app import create_app
    from portfolio.config import Settings
    store, _ = populated
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/r.db", _env_file=None), store))
    body = client.get("/api/returns").json()
    one = next(f for f in body["funds"] if f["scheme"] == "Fund One")
    assert one["status"] == "ok" and one["xirr"] == pytest.approx(0.1, abs=1e-6) and one["since"] == "2025-01-01"
    assert body["portfolio"]["funds_included"] == 2 and body["method"]
    for private in ("ABCDE1234F", "TEST USER", "t@example.com", "9999999999", "1111/22"):
        assert private not in client.get("/api/returns").text
