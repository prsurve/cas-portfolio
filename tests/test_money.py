"""Money and quantities must survive the database exactly (no float round trip)."""

import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import text

from portfolio.store import Store, mf_holdings, stock_holdings

BIG = Decimal("123456789012.123456")  # 18 significant digits: a float cannot hold this  # privacy-ok


def _stock(**kw):
    return {"depository": "CDSL", "dp_name": "X", "broker": None, "client_id_masked": "****1",
            "asset_type": "equity", "isin": "INE009A01021", "name": "T", "symbol": None,
            "quantity": Decimal(1), "price": None, "value": Decimal(0), **kw}


def _save(store, stocks, h="h"):
    from datetime import date
    return store.save_statement(file_sha256=h, issuer="CDSL", kind="demat", cas_type=None,
                                period_from=None, period_to=date(2026, 9, 30), warnings=[], stocks=stocks)[0]


def test_round_trip_is_exact():
    store = Store.from_url("sqlite://")
    sid = _save(store, [_stock(quantity=Decimal("0.000001"), price=Decimal("1450.5"), value=BIG)])
    [row] = store.rows_for(stock_holdings, sid)
    assert row["value"] == BIG and row["quantity"] == Decimal("0.000001") and row["price"] == Decimal("1450.5")
    assert all(isinstance(row[c], Decimal) for c in ("value", "quantity", "price"))


def test_null_stays_null():
    store = Store.from_url("sqlite://")
    sid = _save(store, [_stock(price=None)])
    assert store.rows_for(stock_holdings, sid)[0]["price"] is None


def test_sqlite_stores_integers_not_floats():
    store = Store.from_url("sqlite://")
    _save(store, [_stock(value=BIG)])
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT typeof(value) FROM stock_holdings")).scalar() == "integer"


def test_value_too_large_for_sqlite_is_rejected():
    store = Store.from_url("sqlite://")
    with pytest.raises(Exception, match="too large"):
        _save(store, [_stock(value=Decimal("99999999999999"))])  # > 9.2 trillion at 6 dp  # privacy-ok


def test_no_decimal_warning(recwarn):
    store = Store.from_url("sqlite://")
    _save(store, [_stock(value=BIG)])
    assert not [w for w in recwarn if "Decimal" in str(w.message)]


def _legacy_db(path):
    """A database written before exact storage: money in REAL/NUMERIC columns."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE statements (id INTEGER PRIMARY KEY, file_sha256 VARCHAR(64) NOT NULL UNIQUE,
            issuer VARCHAR(16) NOT NULL, kind VARCHAR(8) NOT NULL, cas_type VARCHAR(16), period_from DATE,
            period_to DATE NOT NULL, imported_at DATETIME NOT NULL, warnings TEXT,
            parser_version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL,
            depository VARCHAR(8) NOT NULL, dp_name VARCHAR(256), broker VARCHAR(32),
            client_id_masked VARCHAR(32), asset_type VARCHAR(8) NOT NULL DEFAULT 'equity',
            isin VARCHAR(12) NOT NULL, name VARCHAR(256), symbol VARCHAR(32),
            quantity NUMERIC(24, 6) NOT NULL, price NUMERIC(24, 6), value NUMERIC(24, 6));
        CREATE TABLE mf_holdings (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL, amc VARCHAR(256),
            folio_masked VARCHAR(32), scheme VARCHAR(512) NOT NULL, isin VARCHAR(12), amfi VARCHAR(16),
            rta VARCHAR(32), scheme_type VARCHAR(32), units NUMERIC(24, 6) NOT NULL, nav NUMERIC(24, 6),
            nav_date DATE, cost NUMERIC(24, 6), value NUMERIC(24, 6));
        INSERT INTO statements VALUES (1, 'h', 'CDSL', 'demat', NULL, NULL, '2026-09-30', '2026-10-27', NULL, 4);
        INSERT INTO stock_holdings VALUES (1, 1, 'CDSL', 'X', NULL, '****1', 'equity', 'INE009A01021', 'T', NULL,
            8, 1450.25, 11602.0);
        INSERT INTO mf_holdings VALUES (1, 1, NULL, '****1', 'S', NULL, NULL, NULL, NULL, 10.123, 55.5, NULL,
            NULL, 561.8265);
    """)
    con.commit()
    con.close()


def test_legacy_float_database_is_converted_once(tmp_path):
    db = tmp_path / "old.db"
    _legacy_db(db)
    url = f"sqlite:///{db}"
    for _ in range(2):  # opening it again must not rescale the values a second time
        store = Store.from_url(url)
        [s] = store.rows_for(stock_holdings, 1)
        [m] = store.rows_for(mf_holdings, 1)
        assert (s["quantity"], s["price"], s["value"]) == (Decimal(8), Decimal("1450.25"), Decimal(11602))
        assert (m["units"], m["nav"], m["cost"], m["value"]) == (
            Decimal("10.123"), Decimal("55.5"), None, Decimal("561.8265"))
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 1


def test_new_database_is_marked_current():
    store = Store.from_url("sqlite://")
    with store.engine.connect() as conn:
        assert conn.execute(text("PRAGMA user_version")).scalar() == 1
