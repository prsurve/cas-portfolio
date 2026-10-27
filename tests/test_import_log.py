"""What was imported: one statement in detail, and the audit trail."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from portfolio.app import create_app
from portfolio.config import Settings
from portfolio.import_log import audit_events, statement_detail
from portfolio.importers import cas
from portfolio.store import Store


@pytest.fixture
def store(tmp_path, mf_data, demat_data):
    s = Store.from_url(f"sqlite:///{tmp_path}/log.db")
    cas.save(s, cas.from_demat_data(demat_data), "a" * 64)
    cas.save(s, cas.from_mf_data(mf_data), "b" * 64)
    return s


@pytest.fixture
def client(store, tmp_path):
    return TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/log.db", _env_file=None), store))


def test_demat_statement_detail_has_counts_and_one_check_per_account_and_the_folio_section(store):
    """Counts are rows read: the same share in two accounts is two rows (Infosys, here)."""
    demat = next(s for s in store.list_statements() if s["kind"] == "demat")
    d = statement_detail(store, demat["id"])
    assert d["issuer"] == "CDSL" and d["period_to"] == "2026-09-30" and d["parser_version"] == cas.PARSER_VERSION
    assert d["counts"]["stocks"] == 6 and d["counts"]["funds"] == 2 and d["counts"]["transactions"] == 0
    sections = [c["section"] for c in d["reconciliation"]]
    assert sections.count("demat") == 4 and sections.count("folios") == 1
    assert d["has_reconciliation"] and all(c["ok"] and c["gap"] == 0 for c in d["reconciliation"])
    assert not d["outdated"]


def test_a_gap_beyond_the_tolerance_is_not_ok(store):
    from sqlalchemy import update
    from portfolio.store import statement_accounts
    demat = next(s for s in store.list_statements() if s["kind"] == "demat")
    with store.engine.begin() as conn:
        conn.execute(update(statement_accounts).where(statement_accounts.c.statement_id == demat["id"],
                                                      statement_accounts.c.section == "folios")
                     .values(statement_total=6000))  # printed 6000, read 5000
    folios = next(c for c in statement_detail(store, demat["id"])["reconciliation"] if c["section"] == "folios")
    assert folios["ok"] is False and folios["gap"] == 1000 and folios["statement_total"] == 6000


def test_statement_without_stored_reconciliation_says_so(store):
    from sqlalchemy import delete
    from portfolio.store import statement_accounts
    with store.engine.begin() as conn:
        conn.execute(delete(statement_accounts))
    d = statement_detail(store, store.list_statements()[0]["id"])
    assert d["reconciliation"] == [] and d["has_reconciliation"] is False


def test_mf_statement_detail_counts_transactions(store):
    mf = next(s for s in store.list_statements() if s["kind"] == "mf")
    d = statement_detail(store, mf["id"])
    assert d["issuer"] == "CAMS" and d["counts"]["transactions"] >= 1 and d["counts"]["funds"] == 3


def test_unknown_statement_is_none_and_404(store, client):
    assert statement_detail(store, 9999) is None
    assert client.get("/api/statements/9999").status_code == 404


def test_statement_route_matches_the_list_entry(client):
    listed = client.get("/api/statements").json()["statements"][0]
    detail = client.get(f"/api/statements/{listed['id']}").json()
    assert {k: detail[k] for k in listed} == listed


def test_audit_trail_is_newest_first_and_limited(store, client):
    store.audit("statement_rejected", "wrong_password")
    store.audit("statement_deleted", "#7")
    events = audit_events(store)
    assert [e["event"] for e in events[:2]] == ["statement_deleted", "statement_rejected"]
    assert [e["event"] for e in events[2:]] == ["statement_imported", "statement_imported"]
    assert len(client.get("/api/audit?limit=1").json()["events"]) == 1
    assert len(client.get("/api/audit?limit=0").json()["events"]) == 1, "the limit is at least 1"
    assert len(client.get("/api/audit?limit=100000").json()["events"]) == len(events), "and at most 200"
    assert client.get("/api/audit?limit=abc").status_code == 422


def test_upload_summary_reports_each_check_and_the_folio_section(demat_data, mf_data):
    s = cas.summary(cas.from_demat_data(demat_data))
    assert all(a["ok"] for a in s["accounts"])
    assert s["folio_check"] == {"statement_total": 5000.0, "imported_value": 5000.0, "gap": 0.0, "ok": True}
    assert "folio_check" not in cas.summary(cas.from_mf_data(mf_data))


def test_nothing_personal_in_detail_or_audit(store, client):
    text = client.get("/api/audit").text
    for sid in (s["id"] for s in store.list_statements()):
        text += client.get(f"/api/statements/{sid}").text
    for private in ("ABCDE1234F", "TEST USER", "12345678", "87654321", "1234567/89", "99887766", "5555444433", "t@example.com"):
        assert private not in text
