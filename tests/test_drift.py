from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from portfolio.app import create_app
from portfolio.config import Settings
from portfolio.drift import InvalidTargets, clean_targets, compute_drift
from portfolio.importers import cas

H = {"X-Requested-With": "portfolio"}
CLASSES = [{"label": "Stocks", "value": 600.0}, {"label": "Mutual funds (folios)", "value": 400.0}]


def test_gap_is_share_now_minus_target_and_values_add_to_zero():
    d = compute_drift(CLASSES, D(1000), {"Stocks": D(50), "Mutual funds (folios)": D(30), "Bonds & SGBs": D(20)})
    by = {r["label"]: r for r in d["rows"]}
    assert by["Stocks"]["weight"] == 60 and by["Stocks"]["gap_pct"] == 10 and by["Stocks"]["gap_value"] == 100
    assert by["Mutual funds (folios)"]["gap_pct"] == 10 and by["Mutual funds (folios)"]["gap_value"] == 100
    assert by["Bonds & SGBs"]["value"] == 0 and by["Bonds & SGBs"]["gap_pct"] == -20 and by["Bonds & SGBs"]["gap_value"] == -200
    assert sum(r["gap_value"] for r in d["rows"]) == 0
    assert d["largest_gap_pct"] == 20


def test_no_targets_or_no_value_gives_no_drift():
    assert compute_drift(CLASSES, D(1000), {}) is None
    assert compute_drift([], D(0), {"Stocks": D(100)}) is None


@pytest.mark.parametrize("bad", [
    [], {"Cash": 100}, {"Stocks": 101}, {"Stocks": -1}, {"Stocks": "abc"}, {"Stocks": True},
    {"Stocks": 60, "Bonds & SGBs": 30}, {"Stocks": "NaN"}, {"Stocks": "Infinity"},
])
def test_invalid_targets_are_rejected(bad):
    with pytest.raises(InvalidTargets):
        clean_targets(bad)


def test_valid_targets():
    assert clean_targets({"Stocks": 60, "Bonds & SGBs": "40"}) == {"Stocks": D(60), "Bonds & SGBs": D(40)}
    assert clean_targets({}) == {}


def make(tmp_path, mf_data=None):
    from portfolio.store import Store
    store = Store.from_url(f"sqlite:///{tmp_path}/t.db")
    if mf_data is not None:
        cas.save(store, cas.from_mf_data(mf_data), "m" * 64)
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db", backup_dir=str(tmp_path / "bk"))
    return store, TestClient(create_app(settings, store))


def test_targets_endpoint_round_trip_export_and_delete_all(tmp_path, mf_data):
    store, client = make(tmp_path, mf_data)
    first = client.get("/api/targets").json()
    assert first["targets"] == {} and first["drift"] is None and "Stocks" in first["classes"]
    r = client.put("/api/targets", json={"targets": {"Stocks": 40, "Mutual funds (folios)": 60}}, headers=H)
    assert r.status_code == 200 and r.json()["targets"]["Stocks"] == 40
    funds = next(x for x in r.json()["drift"]["rows"] if x["label"] == "Mutual funds (folios)")
    assert funds["target"] == 60 and funds["weight"] == 100  # only funds are held in this fixture
    assert client.get("/api/export").json()["targets"][0]["asset_class"] in ("Mutual funds (folios)", "Stocks")
    assert client.put("/api/targets", json={"targets": {"Stocks": 10}}, headers=H).status_code == 400
    assert client.put("/api/targets", content=b"nope", headers=H).status_code == 400
    assert client.put("/api/targets", json={"targets": {"Stocks": 40, "Mutual funds (folios)": 60}}).status_code == 403  # CSRF header
    client.request("DELETE", "/api/data", json={"confirm": "delete all data"}, headers=H)
    assert store.get_targets() == {}
    client.put("/api/targets", json={"targets": {"Stocks": 100}}, headers=H)
    assert client.put("/api/targets", json={"targets": {}}, headers=H).json()["targets"] == {}
