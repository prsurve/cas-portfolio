"""Backups: consistent copies, retention, safe restore, integrity checks and the health endpoint."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio import backup
from portfolio.backup import BackupError, check_database, create_backup, list_backups, restore_backup
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import Store, mf_holdings

T0 = datetime(2026, 10, 27, 20, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, mf_data):
    path = tmp_path / "data" / "portfolio.db"
    path.parent.mkdir()
    store = Store.from_url(f"sqlite:///{path}")
    cas.save(store, cas.from_mf_data(mf_data), "cams")
    return path, store


def _units(path: Path):
    store = Store.from_url(f"sqlite:///{path}")
    [sid] = [s["id"] for s in store.list_statements()]
    return sorted(r["units"] for r in store.rows_for(mf_holdings, sid))


# ---- creating ----------------------------------------------------------------------


def test_backup_is_a_consistent_openable_copy_with_private_permissions(db, tmp_path):
    path, store = db
    dest = tmp_path / "backups"
    out = create_backup(path, dest, now=T0)  # the source stays open and in use
    assert out.name == "portfolio-20261027-200000.db" and out.parent == dest  # privacy-ok
    assert _units(out) == _units(path)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and stat.S_IMODE(dest.stat().st_mode) == 0o700
    assert check_database(out) == []


def test_a_backup_never_overwrites_another(db, tmp_path):
    path, _ = db
    create_backup(path, tmp_path / "b", now=T0)
    with pytest.raises(BackupError, match="already exists"):
        create_backup(path, tmp_path / "b", now=T0)


def test_missing_database_is_an_error_not_an_empty_backup(tmp_path):
    with pytest.raises(BackupError, match="not found"):
        create_backup(tmp_path / "nope.db", tmp_path / "b", now=T0)
    assert not list((tmp_path / "b").glob("*")) if (tmp_path / "b").exists() else True


def test_retention_keeps_the_newest_and_touches_nothing_else(db, tmp_path):
    path, _ = db
    dest = tmp_path / "b"
    for i in range(5):
        create_backup(path, dest, now=T0 + timedelta(days=i), keep=3)
    kept = [p.name for p in list_backups(dest)]
    assert kept == ["portfolio-20261031-200000.db", "portfolio-20261030-200000.db", "portfolio-20261029-200000.db"]  # privacy-ok
    (dest / "notes.txt").write_text("mine")
    create_backup(path, dest, now=T0 + timedelta(days=9), keep=1)
    assert [p.name for p in list_backups(dest)] == ["portfolio-20261105-200000.db"]  # privacy-ok
    assert (dest / "notes.txt").read_text() == "mine"


# ---- checking ----------------------------------------------------------------------


def test_check_finds_damage_and_wrong_files(db, tmp_path):
    path, _ = db
    assert check_database(path) == []
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 100)
    assert check_database(junk)
    empty = tmp_path / "empty.db"
    import sqlite3
    sqlite3.connect(empty).execute("CREATE TABLE other (x)").connection.commit()
    assert any("statements" in p for p in check_database(empty))
    assert check_database(tmp_path / "missing.db")


# ---- restoring ---------------------------------------------------------------------


def test_restore_brings_the_data_back_and_keeps_what_it_replaced(db, tmp_path):
    path, store = db
    out = create_backup(path, tmp_path / "b", now=T0)
    before = _units(path)
    store.delete_statement(store.list_statements()[0]["id"])  # lose the data
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(out, path)  # refuses to overwrite by default
    aside = restore_backup(out, path, force=True, now=T0)
    assert _units(path) == before
    assert aside is not None and aside.exists() and ".before-restore-" in aside.name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_restore_into_a_new_place(db, tmp_path):
    path, _ = db
    out = create_backup(path, tmp_path / "b", now=T0)
    target = tmp_path / "fresh" / "portfolio.db"
    assert restore_backup(out, target) is None and _units(target) == _units(path)


def test_a_bad_backup_never_touches_the_database(db, tmp_path):
    path, _ = db
    bad = tmp_path / "portfolio-20261027-200000.db"  # privacy-ok
    bad.write_bytes(b"garbage" * 200)
    original = path.read_bytes()
    with pytest.raises(BackupError, match="not a usable backup"):
        restore_backup(bad, path, force=True)
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.before-restore-*"))


# ---- encryption --------------------------------------------------------------------


def test_failed_encryption_leaves_no_plaintext(db, tmp_path):
    path, _ = db
    fake = tmp_path / "fakegpg"
    fake.write_text("#!/bin/sh\necho 'no public key' >&2\nexit 2\n")
    fake.chmod(0o755)
    with pytest.raises(BackupError, match="Encryption failed"):
        create_backup(path, tmp_path / "b", recipient="someone", gpg=str(fake), now=T0)
    assert list((tmp_path / "b").glob("*")) == []  # nothing readable was left behind


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg is not installed")
def test_real_gpg_round_trip_with_a_throwaway_key(db, tmp_path, monkeypatch):
    path, _ = db
    home = tempfile.mkdtemp(prefix="g")  # short path: gpg's agent socket has a length limit
    os.chmod(home, 0o700)
    monkeypatch.setenv("GNUPGHOME", home)
    try:
        made = subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
                               "backup-test@example.invalid", "default", "default", "never"],
                              capture_output=True, timeout=120)
        if made.returncode != 0:
            pytest.skip("gpg could not create a test key here")
        plain = create_backup(path, tmp_path / "plain", now=T0)
        enc = create_backup(path, tmp_path / "enc", recipient="backup-test@example.invalid", now=T0)
        assert enc.name == "portfolio-20261027-200000.db.gpg" and [p.name for p in (tmp_path / "enc").iterdir()] == [enc.name]  # privacy-ok
        assert b"Parag Parikh" in plain.read_bytes() and b"Parag Parikh" not in enc.read_bytes()
        assert b"SQLite format" not in enc.read_bytes()
        target = tmp_path / "restored" / "portfolio.db"
        restore_backup(enc, target)
        assert _units(target) == _units(path)
    finally:
        subprocess.run(["gpgconf", "--kill", "all"], env={**os.environ, "GNUPGHOME": home}, capture_output=True)
        shutil.rmtree(home, ignore_errors=True)


# ---- settings and command line -----------------------------------------------------


def test_database_path_from_the_url(tmp_path):
    assert backup.db_path("sqlite:///./data/portfolio.db") == Path("./data/portfolio.db")
    assert backup.db_path(f"sqlite:///{tmp_path}/x.db") == Path(f"{tmp_path}/x.db")
    with pytest.raises(BackupError, match="pg_dump"):
        backup.db_path("postgresql+psycopg://u:p@h/db")


def test_command_line(db, tmp_path, monkeypatch, capsys):
    path, _ = db
    monkeypatch.setattr("portfolio.config.get_settings", lambda: Settings(
        database_url=f"sqlite:///{path}", backup_dir=str(tmp_path / "bk"), backup_keep=2, _env_file=None))
    assert backup.main(["create"]) == 0 and "Backup written" in capsys.readouterr().out
    assert backup.main(["list"]) == 0 and "portfolio-" in capsys.readouterr().out
    assert backup.main(["check"]) == 0 and "ok" in capsys.readouterr().out
    assert backup.main(["restore", str(next((tmp_path / "bk").iterdir()))]) == 1  # would overwrite: refused
    assert "already exists" in capsys.readouterr().err


# ---- the health endpoint -----------------------------------------------------------


@pytest.fixture
def client(db, tmp_path):
    path, store = db
    settings = Settings(database_url=f"sqlite:///{path}", backup_dir=str(tmp_path / "bk"), _env_file=None)
    import portfolio.app as app_module
    return TestClient(app_module.create_app(settings, store)), settings, store, path


def test_health_is_ok_and_shows_no_data(client):
    c, *_ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "schema_version": 1, "last_backup_days": None}
    assert "no-store" in r.headers["Cache-Control"]


def test_health_reports_the_age_of_the_last_backup(client):
    c, settings, _, path = client
    create_backup(path, Path(settings.backup_dir), now=datetime.now(timezone.utc) - timedelta(days=3))
    old = Path(settings.backup_dir).iterdir().__next__()
    ts = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    os.utime(old, (ts, ts))
    assert c.get("/healthz").json()["last_backup_days"] == 3


def test_health_fails_loudly_when_the_database_is_damaged(client, monkeypatch):
    c, _, store, _ = client
    monkeypatch.setattr(store, "quick_check", lambda: "database disk image is malformed")
    r = c.get("/healthz")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert "malformed" not in r.text  # no database internals in the response
