import sqlite3

import pytest

from portfolio.store import SQLITE_SCHEMA_VERSION, DatabaseTooNew, Store


def test_database_from_a_newer_schema_is_refused_and_left_alone(tmp_path):
    path = tmp_path / "new.db"
    Store.from_url(f"sqlite:///{path}")
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    before = path.read_bytes()

    with pytest.raises(DatabaseTooNew, match="UPGRADING"):
        Store.from_url(f"sqlite:///{path}")
    assert path.read_bytes() == before


def test_current_and_new_databases_open(tmp_path):
    path = tmp_path / "ok.db"
    Store.from_url(f"sqlite:///{path}")
    Store.from_url(f"sqlite:///{path}")  # reopening the current version works
    Store.from_url("sqlite://")  # in memory
