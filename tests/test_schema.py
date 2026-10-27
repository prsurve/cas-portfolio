"""Schema upgrades: a database from the first release must open, keep its data and end up with the current schema."""
from __future__ import annotations

import sqlite3
from decimal import Decimal

from sqlalchemy import inspect

from portfolio.store import Store, metadata, mf_holdings, stock_holdings

# The first release's tables, frozen here on purpose: money as floats, none of the later columns or tables.
FIRST_RELEASE = """
CREATE TABLE statements (id INTEGER PRIMARY KEY, file_sha256 VARCHAR(64) NOT NULL UNIQUE,
    issuer VARCHAR(16) NOT NULL, kind VARCHAR(8) NOT NULL, cas_type VARCHAR(16), period_from DATE,
    period_to DATE NOT NULL, imported_at DATETIME NOT NULL, warnings TEXT);
CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL, depository VARCHAR(8) NOT NULL,
    dp_name VARCHAR(256), broker VARCHAR(32), client_id_masked VARCHAR(32), isin VARCHAR(12) NOT NULL,
    name VARCHAR(256), quantity NUMERIC NOT NULL, price NUMERIC, value NUMERIC);
CREATE TABLE mf_holdings (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL, amc VARCHAR(256),
    folio_masked VARCHAR(32), scheme VARCHAR(512) NOT NULL, isin VARCHAR(12), amfi VARCHAR(16), rta VARCHAR(32),
    scheme_type VARCHAR(32), units NUMERIC NOT NULL, nav NUMERIC, nav_date DATE, cost NUMERIC, value NUMERIC);
CREATE TABLE mf_transactions (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL, folio_masked VARCHAR(32),
    isin VARCHAR(12), scheme VARCHAR(512), date DATE, description VARCHAR(512), type VARCHAR(32),
    amount NUMERIC, units NUMERIC, nav NUMERIC, balance NUMERIC);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts DATETIME NOT NULL, event VARCHAR(32) NOT NULL, detail TEXT);
INSERT INTO statements VALUES (1, 'abc', 'CDSL', 'demat', NULL, NULL, '2026-09-30', '2026-10-01 00:00:00', NULL);
INSERT INTO stock_holdings VALUES (1, 1, 'CDSL', 'DP', NULL, '****1', 'INE009A01021', 'INFY', 12.5, 1450.25, 18128.125);
INSERT INTO mf_holdings VALUES (1, 1, NULL, '****2', 'Some Fund', 'INF000000001', NULL, NULL, NULL, 10.5, 80.1, NULL, NULL, 841.05);
INSERT INTO audit_log VALUES (1, '2026-10-01 00:00:00', 'statement_imported', 'x');
"""


def _first_release_db(path):
    con = sqlite3.connect(path)
    con.executescript(FIRST_RELEASE)
    con.commit()
    con.close()


def test_first_release_database_upgrades_and_keeps_its_data(tmp_path):
    db = tmp_path / "old.db"
    _first_release_db(db)
    store = Store.from_url(f"sqlite:///{db}")

    [stock] = store.rows_for(stock_holdings, 1)
    assert (stock["quantity"], stock["price"], stock["value"]) == (Decimal("12.5"), Decimal("1450.25"), Decimal("18128.125"))
    assert stock["asset_type"] == "equity" and stock["symbol"] is None
    [fund] = store.rows_for(mf_holdings, 1)
    assert (fund["units"], fund["nav"], fund["value"]) == (Decimal("10.5"), Decimal("80.1"), Decimal("841.05"))
    assert fund["open_units"] is None and fund["history_from"] is None
    assert store.list_statements()[0]["parser_version"] == 1  # old rows read as outdated, so a re-upload refreshes them
    assert store.schema_version() >= 1 and store.quick_check() == "ok"


def test_upgraded_schema_matches_the_current_tables(tmp_path):
    """Fails when a column is added to a Table but not to Store._migrate()."""
    db = tmp_path / "old.db"
    _first_release_db(db)
    store = Store.from_url(f"sqlite:///{db}")
    inspector = inspect(store.engine)
    for table in metadata.sorted_tables:
        have = {c["name"] for c in inspector.get_columns(table.name)}
        want = {c.name for c in table.columns}
        assert want <= have, f"{table.name} is missing {sorted(want - have)} after upgrading an old database"


def test_reopening_an_upgraded_database_changes_nothing(tmp_path):
    db = tmp_path / "old.db"
    _first_release_db(db)
    first = Store.from_url(f"sqlite:///{db}")
    before = first.export_all()
    again = Store.from_url(f"sqlite:///{db}")  # the money conversion must not run a second time
    after = again.export_all()
    for key in ("statements", "stock_holdings", "mf_holdings", "mf_transactions"):
        assert after[key] == before[key]
    assert again.schema_version() == first.schema_version()
