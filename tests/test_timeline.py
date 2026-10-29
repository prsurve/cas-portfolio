from datetime import date

from fastapi.testclient import TestClient

from conftest import INVESTOR, scheme, txn
from casparser.enums import CASFileType, FileType, TransactionType
from casparser.types import CASData, Folio, StatementPeriod

from portfolio.app import create_app
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import Store
from portfolio.timeline import build_timeline


def statement(to, txns):
    return CASData(
        statement_period=StatementPeriod(**{"from": "01-Jan-2026", "to": to}),
        folios=[Folio(folio="1234567/89", amc="Demo Mutual Fund", PAN="ABCDE1234F", schemes=[  # privacy-ok
            scheme("Demo Fund - Direct Growth", "INF000000001", "20", "10", "200", transactions=txns)])],
        investor_info=INVESTOR, cas_type=CASFileType.DETAILED, file_type=FileType.CAMS)


def test_overlapping_statements_do_not_double_count_transactions(tmp_path):
    store = Store.from_url(f"sqlite:///{tmp_path}/t.db")
    a, b = txn(date(2026, 2, 5), 100, "10", 10, "10"), txn(date(2026, 3, 5), 100, "10", 10, "20")
    cas.save(store, cas.from_mf_data(statement("31-Mar-2026", [a, b])), "a" * 64)
    cas.save(store, cas.from_mf_data(statement("30-Apr-2026", [b, txn(date(2026, 4, 5), 50, "5", 10, "25")])), "b" * 64)
    out = build_timeline(store)
    tx = [e for e in out["events"] if e["kind"] == "transaction"]
    assert len(tx) == 3, "the March purchase printed on both statements is listed once"
    assert sum(1 for e in out["events"] if e["kind"] == "statement") == 2
    dates = [e["date"] for e in out["events"]]
    assert dates == sorted(dates, reverse=True)


def test_two_identical_transactions_in_one_statement_both_stay(tmp_path):
    store = Store.from_url(f"sqlite:///{tmp_path}/t.db")
    same = txn(date(2026, 2, 5), 100, "10", 10, "10")
    cas.save(store, cas.from_mf_data(statement("31-Mar-2026", [same, same.model_copy()])), "c" * 64)
    assert sum(1 for e in build_timeline(store)["events"] if e["kind"] == "transaction") == 2


def test_labels_limit_and_endpoint(tmp_path):
    store = Store.from_url(f"sqlite:///{tmp_path}/t.db")
    redeem = txn(date(2026, 3, 5), -100, "-10", 10, "0", TransactionType.REDEMPTION)
    cas.save(store, cas.from_mf_data(statement("31-Mar-2026", [txn(date(2026, 2, 5), 100, "10", 10, "10"), redeem])), "d" * 64)
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/t.db", backup_dir=str(tmp_path / "bk")), store))
    body = client.get("/api/timeline?limit=2").json()
    assert body["total"] == 3 and len(body["events"]) == 2
    titles = {e["title"] for e in client.get("/api/timeline").json()["events"]}
    assert {"Statement imported", "SIP purchase", "Redemption"} <= titles
    assert "PAN" not in client.get("/api/timeline").text
    assert Store.from_url("sqlite://") and build_timeline(Store.from_url("sqlite://")) == {"total": 0, "events": []}
