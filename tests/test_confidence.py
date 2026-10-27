"""Data confidence: each issue names the figures it affects and what to do; each figure says how far it can be trusted."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from casparser.types import StatementPeriod
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from portfolio.app import create_app
from portfolio.config import Settings
from portfolio.confidence import FIGURES, assess, data_confidence
from portfolio.importers import cas
from portfolio.portfolio_view import compute_view
from portfolio.store import Store, statement_accounts

TODAY = date(2026, 10, 27)
STOCK_ISINS = ["INE009A01021", "INE467B01029", "INE040A01034", "INE002A01018", "INF000000001"]
FUND_ISINS = ["INF879O01027", "INF179K01VQ6"]
EXTRAS = {"outdated": 0, "statement_dates": 1, "price_sources": [], "last_refresh": None, "last_failure": None}


@pytest.fixture
def store(tmp_path, mf_data, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/c.db")
    cas.save(s, cas.from_demat_data(demat_data), "a" * 64)
    cas.save(s, cas.from_mf_data(mf_data), "b" * 64)
    return s


def report(store, today=TODAY, **extras):
    return assess(compute_view(store, today=today), {**EXTRAS, **extras})


def issue(r, id_):
    return next((i for i in r["issues"] if i["id"] == id_), None)


def figure(r, id_):
    return next(f for f in r["figures"] if f["id"] == id_)


def price_everything(store, day=date(2026, 10, 26)):
    store.save_prices("NSE", {i: (Decimal("100"), day) for i in STOCK_ISINS})
    store.save_prices("AMFI", {i: (Decimal("100"), day) for i in FUND_ISINS})


def test_empty_install_has_nothing_to_assess(tmp_path):
    r = report(Store.from_url(f"sqlite:///{tmp_path}/e.db"))
    assert r["empty"] is True and r["issues"] == [] and figure(r, "total")["status"] == "unavailable"


def test_without_prices_latest_value_is_unavailable_and_each_issue_says_what_to_do(store):
    r = report(store)
    for id_ in ("demat_no_price", "fund_no_price"):
        i = issue(r, id_)
        assert i["severity"] == "attention" and i["affects"] == ["Value at latest price"] and "Refresh prices" in i["action"]
    assert figure(r, "latest")["status"] == "unavailable"
    assert figure(r, "total")["status"] == "ok", "missing prices never touch the statement-based total"


def test_with_every_price_present_those_issues_go_and_the_figure_is_ok(store):
    price_everything(store)
    r = report(store)
    assert issue(r, "demat_no_price") is None and issue(r, "fund_no_price") is None
    assert figure(r, "latest")["status"] == "ok"
    assert {"demat_prices", "fund_prices"} <= {p["id"] for p in r["passed"]}


def test_a_price_older_than_a_week_is_flagged(store):
    price_everything(store, day=date(2026, 10, 15))
    i = issue(report(store), "old_prices")
    assert i and i["count"] == 7 and i["affects"] == ["Value at latest price"]


def test_a_gap_in_an_account_total_partly_discredits_total_allocation_and_changes(store):
    demat = next(s for s in store.list_statements() if s["kind"] == "demat")
    with store.engine.begin() as conn:
        conn.execute(update(statement_accounts).where(statement_accounts.c.statement_id == demat["id"],
                                                      statement_accounts.c.section == "demat",
                                                      statement_accounts.c.client_id_masked.like("%5678"))
                     .values(statement_total=36000))  # printed 36000, 35400 read
    r = report(store, statement_dates=2)
    i = issue(r, "demat_gap")
    assert i["count"] == 1 and set(i["affects"]) == {"Total value", "Allocation and concentration", "Change since the previous statement"}
    assert "were not recognised" in i["action"] and "--diagnose" in i["action"]
    assert "printed ₹36,000.00, read ₹35,400.00" in i["detail"], "amounts use rupee formatting, not raw decimals"
    assert "wrong" not in i["title"].lower(), "a gap means unrecognised rows, not that the statement is wrong"
    for id_ in ("total", "allocation", "changes"):
        f = figure(r, id_)
        assert f["status"] == "partial" and i["title"] in f["caveats"]


def test_a_statement_without_stored_totals_is_unverified_not_wrong(store):
    with store.engine.begin() as conn:
        conn.execute(delete(statement_accounts))
    i = issue(report(store), "demat_unverified")
    assert i and "not verified" in i["detail"] and "Upload the same PDF again" in i["action"]


def test_funds_without_cost_make_the_gain_partial(tmp_path, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/d.db")
    cas.save(s, cas.from_demat_data(demat_data), "d" * 64)  # its folio section: one fund with a cost, one without
    r = report(s)
    i = issue(r, "fund_no_cost")
    assert i["count"] == 1 and i["affects"] == ["Mutual fund gain"] and "detailed CAMS or KFintech" in i["action"]
    assert figure(r, "fund_gain")["status"] == "partial"


def test_stock_gain_is_always_unavailable_and_says_why(store):
    f = figure(report(store), "stock_gain")
    assert f["status"] == "unavailable" and "do not print what you paid" in f["basis"] and f["caveats"] == []


def test_xirr_follows_how_many_funds_have_complete_history(store):
    r = report(store)
    f = figure(r, "xirr")
    funds = compute_view(store, today=TODAY)["completeness"]["funds"]
    history = funds["history"]
    expected = "unavailable" if history["complete"] == 0 else "partial" if history["complete"] < funds["count"] else "ok"
    assert f["status"] == expected and f["figure"] == FIGURES["xirr"]


def test_funds_from_a_demat_statement_have_unknown_history_and_the_issue_says_how_to_fix_it(tmp_path, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/d.db")
    cas.save(s, cas.from_demat_data(demat_data), "d" * 64)
    r = report(s)
    assert issue(r, "history_unknown")["affects"] == ["Mutual fund return (XIRR)"]
    assert figure(r, "xirr")["status"] == "unavailable"


def test_statements_far_apart_are_flagged_as_mixed(tmp_path, mf_data, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/m.db")
    older = demat_data.model_copy(deep=True)
    older.statement_period = StatementPeriod(**{"from": "01-Jul-2026", "to": "31-Jul-2026"})
    cas.save(s, cas.from_demat_data(older), "o" * 64)
    cas.save(s, cas.from_mf_data(mf_data), "m" * 64)
    i = issue(report(s), "mixed_dates")
    assert i and "61 days apart" in i["title"] and "Total value" in i["affects"]


def test_old_statements_are_flagged_as_stale(store):
    r = report(store, today=date(2026, 12, 31))
    assert issue(r, "stale_demat") and issue(r, "stale_mf")
    assert figure(r, "total")["status"] == "partial"


def test_a_statement_read_by_an_older_version_says_to_upload_it_again(store):
    i = issue(report(store, outdated=1), "outdated_parser")
    assert i and "Upload the same PDF again" in i["action"]


def test_changes_need_two_dates(store):
    assert figure(report(store), "changes")["status"] == "unavailable"
    assert figure(report(store, statement_dates=2), "changes")["status"] == "ok"


def test_a_note_changes_no_figure(store):
    """A stock with no NSE symbol is shown by name, and no figure depends on the symbol."""
    from sqlalchemy import update as upd
    from portfolio.store import stock_holdings
    with store.engine.begin() as conn:
        conn.execute(upd(stock_holdings).values(symbol=None))
    r = report(store)
    n = issue(r, "no_symbol")
    assert n["severity"] == "note" and n["affects"] == [] and n["action"] is None
    assert figure(r, "total")["status"] == "ok"
    assert [i["severity"] for i in r["issues"]] == sorted((i["severity"] for i in r["issues"]), key={"attention": 0, "note": 1}.get), "attention comes first"


def test_every_issue_is_complete(store):
    r = report(store, outdated=1)
    for i in r["issues"]:
        assert i["title"] and set(i["affects"]) <= set(FIGURES.values()) and i["affect_ids"] is not None
        if i["severity"] == "attention":
            assert i["affects"] and i["action"], f"{i['id']} must say what it affects and what to do"


def test_sources_give_dates_and_counts_only(store):
    price_everything(store)
    store.audit("prices_refreshed", "funds 2 of 2")
    out = data_confidence(store, compute_view(store, today=TODAY))["sources"]
    nse = next(p for p in out["prices"] if p["source"] == "NSE")
    assert nse["count"] == 5 and nse["newest"] == "2026-10-26" and nse["oldest"] == "2026-10-26" and nse["fetched_at"]
    assert out["last_refresh"] and out["last_refresh_failed"] is None
    assert out["statements"]["demat"]["issuer"] == "CDSL"


def test_route_and_no_personal_data(store, tmp_path):
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/c.db", _env_file=None), store))
    r = client.get("/api/confidence")
    assert r.status_code == 200 and r.json()["figures"] and r.json()["sources"]["statements"]
    for private in ("ABCDE1234F", "TEST USER", "12345678", "87654321", "1234567/89", "99887766", "5555444433", "t@example.com", "INFOSYS"):
        assert private not in r.text
