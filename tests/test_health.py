"""Health and backups for the Settings page: counts and dates only, never portfolio data."""
from __future__ import annotations

import os
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import portfolio.backup as backup_module
import portfolio.health as health_module
from portfolio.app import create_app
from portfolio.backup import BackupError
from portfolio.config import Settings
from portfolio.health import _relative, system_health
from portfolio.importers import cas
from portfolio.store import Store

CSRF = {"X-Requested-With": "portfolio"}


@pytest.fixture
def env(tmp_path, mf_data, demat_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/h.db", backup_dir=str(tmp_path / "backups"), _env_file=None)
    store = Store.from_url(settings.database_url)
    cas.save(store, cas.from_demat_data(demat_data), "a" * 64)
    cas.save(store, cas.from_mf_data(mf_data), "b" * 64)
    return TestClient(create_app(settings, store)), store, settings, tmp_path


def test_a_new_install_with_no_backup_asks_for_one(env):
    _, store, settings, _ = env
    h = system_health(store, settings)
    assert h["status"] == "attention" and any("no backup yet" in a for a in h["attention"])
    assert h["database"]["ok"] and h["database"]["engine"] == "SQLite" and h["database"]["size_bytes"] > 0
    assert h["backups"] == {"count": 0, "newest_days": None, "encrypted": False, "keep": 14, "folder": settings.backup_dir}
    assert h["prices"] == {"fetching": True, "last_refresh": None}


def test_counts_are_rows_only(env):
    _, store, settings, _ = env
    stored = system_health(store, settings)["stored"]
    assert stored["statements"] == 2 and stored["demat_rows"] == 6 and stored["fund_rows"] >= 3
    assert stored["transactions"] >= 1 and stored["audit_events"] == 2


def test_after_a_backup_everything_is_fine(env):
    client, _, _, _ = env
    assert client.get("/api/health").json()["status"] == "attention"
    made = client.post("/api/backups", headers=CSRF)
    assert made.status_code == 201 and made.json()["name"].startswith("portfolio-")
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["attention"] == [] and h["backups"]["count"] == 1 and h["backups"]["newest_days"] == 0
    listed = client.get("/api/backups").json()
    assert [b["name"] for b in listed["backups"]] == [made.json()["name"]] and listed["backups"][0]["size_bytes"] > 0


def test_backup_needs_the_csrf_header_and_is_verified(env):
    client, _, _, tmp = env
    assert client.post("/api/backups").status_code == 403
    name = client.post("/api/backups", headers=CSRF).json()["name"]
    assert backup_module.check_database(tmp / "backups" / name) == []
    assert "backup_created" in [e["event"] for e in client.get("/api/audit").json()["events"]]


def test_retention_keeps_only_the_newest(env, monkeypatch):
    client, _, settings, tmp = env
    settings.backup_keep = 2
    days = [datetime(2026, 10, d, 9, 0, 0, tzinfo=timezone.utc) for d in (1, 2, 3)]
    stamps = iter(days)
    monkeypatch.setattr(backup_module, "_now", lambda: next(stamps))
    for _ in range(3):
        assert client.post("/api/backups", headers=CSRF).status_code == 201
    newest_two = [f"portfolio-{d:%Y%m%d-%H%M%S}.db" for d in (days[2], days[1])]
    assert [b["name"] for b in client.get("/api/backups").json()["backups"]] == newest_two


def test_a_failed_backup_is_503_and_leaks_nothing(env, monkeypatch):
    client, _, _, _ = env
    import portfolio.app as app_module

    def broken(*args, **kwargs):
        raise BackupError("gpg failed at /home/someone/.gnupg for key secret-key-id")
    monkeypatch.setattr(app_module, "create_backup", broken)
    r = client.post("/api/backups", headers=CSRF)
    assert r.status_code == 503 and "gnupg" not in r.text and "secret" not in r.text


def test_an_old_backup_is_flagged(env):
    client, _, _, tmp = env
    name = client.post("/api/backups", headers=CSRF).json()["name"]
    old = time.time() - 45 * 86400
    os.utime(tmp / "backups" / name, (old, old))
    h = client.get("/api/health").json()
    assert h["status"] == "attention" and any("45 days old" in a for a in h["attention"])


def test_low_disk_space_is_flagged(env, monkeypatch):
    _, store, settings, _ = env
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(health_module.shutil, "disk_usage", lambda p: usage(10**12, 10**12 - 1000, 1000))
    assert any("disk space" in a for a in system_health(store, settings)["attention"])


def test_last_price_refresh_comes_from_the_audit_log(env):
    _, store, settings, _ = env
    store.audit("prices_refreshed", "funds 2 of 3")
    last = system_health(store, settings)["prices"]["last_refresh"]
    assert datetime.fromisoformat(last) > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_a_failed_database_check_is_flagged(env, monkeypatch):
    _, store, settings, _ = env
    monkeypatch.setattr(store, "quick_check", lambda: "row 3 missing from index")
    h = system_health(store, settings)
    assert h["database"]["ok"] is False and "row 3 missing" not in str(h)  # the database's own words stay out


def test_location_never_shows_an_absolute_path_outside_the_working_folder(tmp_path):
    assert _relative(tmp_path / "x.db") == "x.db"
    assert _relative(Path.cwd() / "data" / "portfolio.db") == "data/portfolio.db"


def test_health_holds_no_personal_data(env):
    client, _, _, _ = env
    client.post("/api/backups", headers=CSRF)
    text = client.get("/api/health").text + client.get("/api/backups").text
    for private in ("ABCDE1234F", "TEST USER", "12345678", "87654321", "1234567/89", "99887766", "t@example.com", "INFOSYS", "INE009A01021"):
        assert private not in text
