import json

from fastapi.testclient import TestClient

from portfolio.app import create_app
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import MAX_SNAPSHOTS, Store

H = {"X-Requested-With": "portfolio"}


def make(tmp_path, mf_data=None):
    store = Store.from_url(f"sqlite:///{tmp_path}/s.db")
    if mf_data is not None:
        cas.save(store, cas.from_mf_data(mf_data), "m" * 64)
    return store, TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/s.db", backup_dir=str(tmp_path / "bk")), store))


def test_nothing_to_save_on_an_empty_database(tmp_path):
    _, client = make(tmp_path)
    assert client.post("/api/snapshots", json={}, headers=H).status_code == 400
    assert client.get("/api/snapshots").json()["snapshots"] == []


def test_snapshot_keeps_the_figures_as_they_were(tmp_path, mf_data):
    store, client = make(tmp_path, mf_data)
    made = client.post("/api/snapshots", json={"note": "  before   the\nSIP  "}, headers=H)
    assert made.status_code == 201
    sid = made.json()["id"]
    listing = client.get("/api/snapshots").json()["snapshots"]
    assert len(listing) == 1 and listing[0]["note"] == "before the SIP" and "payload" not in listing[0] and "view" not in listing[0]
    now = client.get("/api/portfolio").json()
    saved = client.get(f"/api/snapshots/{sid}").json()
    assert saved["view"]["totals"] == now["totals"] and saved["total"] == now["totals"]["value"]
    assert saved["as_of_to"] == now["as_of"]["to"]
    # deleting the statements later does not change the snapshot
    client.request("DELETE", "/api/data", json={"confirm": "delete all data"}, headers=H)
    assert store.list_snapshots() == [], "delete-all removes snapshots too: they hold real figures"


def test_snapshot_survives_a_changed_portfolio(tmp_path, mf_data):
    store, client = make(tmp_path, mf_data)
    sid = client.post("/api/snapshots", json={}, headers=H).json()["id"]
    before = client.get(f"/api/snapshots/{sid}").json()["view"]["totals"]["value"]
    store.delete_statement(1)
    assert client.get(f"/api/snapshots/{sid}").json()["view"]["totals"]["value"] == before


def test_validation_limits_delete_and_csrf(tmp_path, mf_data):
    store, client = make(tmp_path, mf_data)
    assert client.post("/api/snapshots", json={"note": 5}, headers=H).status_code == 400
    assert client.post("/api/snapshots", json={}).status_code == 403
    long = client.post("/api/snapshots", json={"note": "x" * 500}, headers=H).json()["id"]
    assert len(client.get(f"/api/snapshots/{long}").json()["note"]) == 80
    assert client.get("/api/snapshots/999").status_code == 404
    assert client.delete(f"/api/snapshots/{long}", headers=H).status_code == 200
    assert client.delete(f"/api/snapshots/{long}", headers=H).status_code == 404
    for _ in range(MAX_SNAPSHOTS):
        assert client.post("/api/snapshots", json={}, headers=H).status_code == 201
    assert client.post("/api/snapshots", json={}, headers=H).status_code == 409


def test_export_and_counts_include_snapshots(tmp_path, mf_data):
    store, client = make(tmp_path, mf_data)
    client.post("/api/snapshots", json={}, headers=H)
    exported = client.get("/api/export").json()
    assert len(exported["report_snapshots"]) == 1 and json.loads(exported["report_snapshots"][0]["payload"])["totals"]
    assert store.table_counts()["snapshots"] == 1
    events = [e["event"] for e in client.get("/api/audit").json()["events"]]
    assert "snapshot_saved" in events
