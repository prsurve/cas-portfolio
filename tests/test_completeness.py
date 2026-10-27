"""Data completeness: how much of what the statement printed was read, and what is missing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from conftest import equity
from sqlalchemy import select, text

from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.store import Store, statement_accounts

TODAY = date(2026, 10, 27)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/comp.db")


# ---- what the parser records -------------------------------------------------------


def test_reconciliation_rows_for_a_demat_statement(demat_data):
    rows = cas.reconciliation_rows(cas.from_demat_data(demat_data))
    assert [(r["section"], r["name"]) for r in rows] == [
        ("demat", "ZERODHA BROKING LIMITED"), ("demat", "UPSTOX SECURITIES PRIVATE LIMITED"),
        ("demat", "HDFC BANK LIMITED"), ("demat", "SOME OTHER DP LTD"), ("folios", "Mutual fund folios")]
    z = rows[0]
    assert z["client_id_masked"] == "****5678" and z["depository"] == "CDSL"
    assert z["statement_total"] == z["imported_value"] == D(12 * 1450 + 5 * 3400 + 1000)
    assert (rows[-1]["statement_total"], rows[-1]["imported_value"]) == (D(5000), D(5000))
    assert rows[-1]["client_id_masked"] is None


def test_mf_statements_have_no_reconciliation_rows(mf_data):
    assert cas.reconciliation_rows(cas.from_mf_data(mf_data)) == []


def test_rows_never_hold_a_full_client_id(demat_data):
    assert "12345678" not in repr(cas.reconciliation_rows(cas.from_demat_data(demat_data)))


# ---- storage -----------------------------------------------------------------------


def test_rows_are_stored_replaced_and_deleted_with_the_statement(store, demat_data):
    st = cas.from_demat_data(demat_data)
    sid, _ = cas.save(store, st, "h")
    stored = store.rows_for(statement_accounts, sid)
    assert len(stored) == 5 and stored[0]["statement_total"] == D(35400)
    # an older parser version is replaced by the new parse, rows included
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE statements SET parser_version = 1"))
    sid2, created = cas.save(store, st, "h")
    assert created and len(store.rows_for(statement_accounts, sid2)) == 5
    with store.engine.connect() as conn:
        assert len(conn.execute(select(statement_accounts)).all()) == 5  # not doubled
    store.delete_statement(sid2)
    with store.engine.connect() as conn:
        assert conn.execute(select(statement_accounts)).all() == []


def test_old_database_gets_the_new_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    sqlite3.connect(db).executescript("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts DATETIME NOT NULL, "
                                      "event VARCHAR(32) NOT NULL, detail TEXT); PRAGMA user_version = 1;")
    store = Store.from_url(f"sqlite:///{db}")
    assert store.rows_for(statement_accounts, 1) == []


# ---- the completeness block --------------------------------------------------------


def test_clean_demat_statement(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "d")
    c = build_view(store, today=TODAY)["completeness"]
    d = c["demat"]
    assert d["available"] is True and d["all_match"] is True and len(d["accounts"]) == 4
    assert d["statement_total"] == d["imported_value"] == 89700 and d["gap"] == 0
    assert all(a["ok"] for a in d["accounts"]) and d["accounts"][0]["label"] == "Zerodha Broking"
    assert d["folios"] == {"statement_total": 5000, "imported_value": 5000, "gap": 0, "ok": True}
    assert c["stocks"] == {"count": 4, "without_symbol": 0}


def test_an_account_that_does_not_add_up_is_listed(store, demat_data):
    acct = demat_data.accounts[1]
    demat_data.accounts[1] = acct.model_copy(update={"balance": acct.balance + D(500)})
    cas.save(store, cas.from_demat_data(demat_data), "d")
    d = build_view(store, today=TODAY)["completeness"]["demat"]
    assert d["all_match"] is False and d["gap"] == 500
    bad = [a for a in d["accounts"] if not a["ok"]]
    assert [(a["label"], a["gap"]) for a in bad] == [("Upstox Securities", 500)]
    assert 0 < d["coverage_pct"] < 100


def test_statement_stored_before_rows_existed_says_so(store, demat_data):
    st = cas.from_demat_data(demat_data)
    store.save_statement(file_sha256="old", issuer="CDSL", kind="demat", cas_type=None, period_from=None,
                         period_to=date(2026, 9, 30), warnings=[], stocks=st.stocks, mfs=st.mfs)  # no accounts
    c = build_view(store, today=TODAY)["completeness"]
    assert c["demat"] == {"available": False}


def test_no_demat_statement(store, mf_data):
    cas.save(store, cas.from_mf_data(mf_data), "m")
    assert build_view(store, today=TODAY)["completeness"]["demat"] is None


def test_fund_gaps_are_counted(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "d")  # folios: one with cost, one without; one AMC unknown
    f = build_view(store, today=TODAY)["completeness"]["funds"]
    assert (f["count"], f["with_cost"], f["without_cost"], f["unknown_amc"]) == (2, 1, 1, 1)
    assert f["history"] == {"complete": 0, "incomplete": 0, "unknown": 2}  # a depository statement has no transactions


def test_fund_history_from_a_cams_statement(store, mf_data):
    s = mf_data.folios[0].schemes[0]
    mf_data.folios[0].schemes[0] = s.model_copy(update={"open": D(40)})  # held before the statement began
    cas.save(store, cas.from_mf_data(mf_data), "m")
    f = build_view(store, today=TODAY)["completeness"]["funds"]
    assert f["count"] == 2 and f["unknown_amc"] == 0 and f["without_cost"] == 0
    assert f["history"] == {"complete": 1, "incomplete": 1, "unknown": 0}  # the merged Flexi Cap fund is incomplete


def test_stock_without_a_known_symbol(store, demat_data):
    z = demat_data.accounts[0]
    demat_data.accounts[0] = z.model_copy(update={"equities": [*z.equities, equity("INE0ZZZ01011", 1, "10")],
                                                  "balance": z.balance + D(10)})
    cas.save(store, cas.from_demat_data(demat_data), "d")
    assert build_view(store, today=TODAY)["completeness"]["stocks"] == {"count": 5, "without_symbol": 1}


def test_empty(store):
    assert build_view(store, today=TODAY)["completeness"] == {
        "demat": None, "funds": None, "stocks": None}
