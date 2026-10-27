"""Database schema and data access (SQLAlchemy Core; SQLite or PostgreSQL).

Only parsed holdings are stored. Uploaded PDFs, passwords, PAN, names,
addresses and full folio/client numbers are never written.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, delete, inspect, insert, select, text,
)
from sqlalchemy.engine import Engine

from portfolio.money import Money

metadata = MetaData()

# SQLite schema version (PRAGMA user_version). 1 = money stored as exact integers.
SQLITE_SCHEMA_VERSION = 1
MONEY_COLUMNS = {
    "stock_holdings": ("quantity", "price", "value"),
    "mf_holdings": ("units", "nav", "cost", "value"),
    "mf_transactions": ("amount", "units", "nav", "balance"),
}

NUM = Money()

# kind: "mf" (CAMS/KFintech) or "demat" (NSDL/CDSL)
statements = Table(
    "statements", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("file_sha256", String(64), nullable=False, unique=True),
    Column("issuer", String(16), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("cas_type", String(16)),
    Column("period_from", Date),
    Column("period_to", Date, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    Column("warnings", Text),
    # Bumped when parsing improves, so re-uploading the same file re-imports it.
    Column("parser_version", Integer, nullable=False, default=1),
)

# Everything held in demat accounts. asset_type: "equity", "fund" (ETFs and
# demat mutual fund units; CDSL lists both under INF ISINs) or "bond".
stock_holdings = Table(
    "stock_holdings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("statement_id", Integer, ForeignKey("statements.id"), nullable=False, index=True),
    Column("depository", String(8), nullable=False),
    Column("dp_name", String(256)),
    Column("broker", String(32)),  # null when the DP name matches no known broker
    Column("client_id_masked", String(32)),
    Column("asset_type", String(8), nullable=False, default="equity"),
    Column("isin", String(12), nullable=False),
    Column("name", String(256)),
    Column("symbol", String(32)),  # NSE symbol, when known
    Column("quantity", NUM, nullable=False),
    Column("price", NUM),
    Column("value", NUM),
)

mf_holdings = Table(
    "mf_holdings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("statement_id", Integer, ForeignKey("statements.id"), nullable=False, index=True),
    Column("amc", String(256)),
    Column("folio_masked", String(32)),
    Column("scheme", String(512), nullable=False),
    Column("isin", String(12)),
    Column("amfi", String(16)),
    Column("rta", String(32)),
    Column("scheme_type", String(32)),
    Column("units", NUM, nullable=False),
    Column("nav", NUM),
    Column("nav_date", Date),
    Column("cost", NUM),
    Column("value", NUM),
    # Units held when the statement's transaction list starts, and that start date.
    # Null where the statement has no transactions (the folio section of an NSDL/CDSL CAS).
    # Transactions alone describe the whole holding only when open_units is zero.
    Column("open_units", NUM),
    Column("history_from", Date),
)

mf_transactions = Table(
    "mf_transactions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("statement_id", Integer, ForeignKey("statements.id"), nullable=False, index=True),
    Column("folio_masked", String(32)),
    Column("isin", String(12)),
    Column("scheme", String(512)),
    Column("date", Date),
    Column("description", String(512)),
    Column("type", String(32)),
    Column("amount", NUM),
    Column("units", NUM),
    Column("nav", NUM),
    Column("balance", NUM),
)

# What each statement printed against what was read: one row per demat account, plus one
# for the "Mutual Fund Folios" section. section is "demat" or "folios". Statements stored
# before parser version 6 have none; re-uploading the PDF fills them in.
statement_accounts = Table(
    "statement_accounts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("statement_id", Integer, ForeignKey("statements.id"), nullable=False, index=True),
    Column("section", String(8), nullable=False),
    Column("name", String(256)),
    Column("depository", String(8)),
    Column("client_id_masked", String(32)),
    Column("statement_total", NUM, nullable=False),
    Column("imported_value", NUM, nullable=False),
)

# The latest public price per held instrument and source (for now: AMFI NAV by fund ISIN).
# Only ISINs you hold are kept, and only the newest price: this is not a price history.
prices = Table(
    "prices", metadata,
    Column("isin", String(12), primary_key=True),
    Column("source", String(16), primary_key=True),
    Column("price", NUM, nullable=False),
    Column("price_date", Date, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("event", String(32), nullable=False),
    Column("detail", Text),
)

CHILD_TABLES = (stock_holdings, mf_holdings, mf_transactions, statement_accounts)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; values are always written as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> Store:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        store = cls(create_engine(url, **kwargs))
        metadata.create_all(store.engine)
        store._migrate()
        return store

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        money_ddl = "BIGINT" if self.engine.dialect.name == "sqlite" else "NUMERIC(24, 6)"
        added = [
            ("statements", "parser_version", "INTEGER NOT NULL DEFAULT 1"),
            ("stock_holdings", "asset_type", "VARCHAR(8) NOT NULL DEFAULT 'equity'"),
            ("stock_holdings", "symbol", "VARCHAR(32)"),
            ("mf_holdings", "open_units", money_ddl),
            ("mf_holdings", "history_from", "DATE"),
        ]
        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table, column, ddl in added:
                if column not in {c["name"] for c in inspector.get_columns(table)}:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            if self.engine.dialect.name == "sqlite":
                self._migrate_sqlite_money(conn)

    def _migrate_sqlite_money(self, conn: Any) -> None:
        """Databases from before exact storage hold floats. Convert them once to
        millionths (rounding recovers the original 6-decimal value), then mark
        the database so the conversion never runs twice."""
        if conn.execute(text("PRAGMA user_version")).scalar() >= SQLITE_SCHEMA_VERSION:
            return
        for table, columns in MONEY_COLUMNS.items():
            for column in columns:
                conn.execute(text(
                    f"UPDATE {table} SET {column} = CAST(ROUND({column} * 1000000) AS INTEGER) "
                    f"WHERE {column} IS NOT NULL"))
        conn.execute(text(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}"))

    def audit(self, event: str, detail: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(audit_log).values(ts=utcnow(), event=event, detail=detail))

    def save_statement(
        self,
        *,
        file_sha256: str,
        issuer: str,
        kind: str,
        cas_type: str | None,
        period_from: date | None,
        period_to: date,
        warnings: list[str],
        parser_version: int = 1,
        stocks: list[dict[str, Any]] = (),
        mfs: list[dict[str, Any]] = (),
        transactions: list[dict[str, Any]] = (),
        accounts: list[dict[str, Any]] = (),
    ) -> tuple[int, bool]:
        """Store a parsed statement atomically. Returns (id, created).

        The same file twice is a no-op, unless it was imported by an older
        parser version, in which case it is replaced with the new parse.
        """
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(statements.c.id, statements.c.parser_version).where(statements.c.file_sha256 == file_sha256)
            ).first()
            if existing is not None:
                if existing.parser_version >= parser_version:
                    return existing.id, False
                for table in CHILD_TABLES:
                    conn.execute(delete(table).where(table.c.statement_id == existing.id))
                conn.execute(delete(statements).where(statements.c.id == existing.id))
            statement_id = conn.execute(insert(statements).values(
                file_sha256=file_sha256, issuer=issuer, kind=kind, cas_type=cas_type,
                period_from=period_from, period_to=period_to, imported_at=utcnow(),
                warnings="\n".join(warnings) or None, parser_version=parser_version,
            )).inserted_primary_key[0]
            for table, rows in ((stock_holdings, stocks), (mf_holdings, mfs), (mf_transactions, transactions),
                                (statement_accounts, accounts)):
                if rows:
                    conn.execute(insert(table), [{**r, "statement_id": statement_id} for r in rows])
            return statement_id, True

    def list_statements(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(statements).order_by(statements.c.period_to.desc(), statements.c.id.desc()))
            return [dict(r) for r in rows.mappings()]

    def latest_statement(self, kind: str, on_or_before: date | None = None) -> dict[str, Any] | None:
        """The statement with the latest period end for this kind (ties: most recently imported).

        With on_or_before, the one that was the latest on that date.
        """
        query = select(statements).where(statements.c.kind == kind)
        if on_or_before is not None:
            query = query.where(statements.c.period_to <= on_or_before)
        with self.engine.connect() as conn:
            row = conn.execute(
                query.order_by(statements.c.period_to.desc(), statements.c.id.desc()).limit(1)
            ).mappings().first()
            return dict(row) if row else None

    def statement_dates(self) -> list[date]:
        """Every distinct statement end date, oldest first."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(statements.c.period_to).distinct().order_by(statements.c.period_to))
            return [r[0] for r in rows]

    def held_fund_isins(self) -> set[str]:
        """Every mutual fund ISIN in any stored statement."""
        with self.engine.connect() as conn:
            return {r[0] for r in conn.execute(select(mf_holdings.c.isin).distinct()) if r[0]}

    def save_prices(self, source: str, found: dict[str, tuple[Any, date]]) -> None:
        """Replace the stored price for each ISIN: {isin: (price, price_date)}."""
        now = utcnow()
        with self.engine.begin() as conn:
            if found:
                conn.execute(delete(prices).where(prices.c.source == source, prices.c.isin.in_(list(found))))
                conn.execute(insert(prices), [
                    {"isin": isin, "source": source, "price": price, "price_date": day, "fetched_at": now}
                    for isin, (price, day) in found.items()])

    def prices_for(self, isins: list[str], source: str) -> dict[str, dict[str, Any]]:
        if not isins:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(select(prices).where(prices.c.source == source, prices.c.isin.in_(isins))).mappings()
            return {r["isin"]: dict(r) for r in rows}

    def rows_for(self, table: Table, statement_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(table).where(table.c.statement_id == statement_id)).mappings()
            return [dict(r) for r in rows]

    def delete_statement(self, statement_id: int) -> bool:
        with self.engine.begin() as conn:
            for table in CHILD_TABLES:
                conn.execute(delete(table).where(table.c.statement_id == statement_id))
            return (conn.execute(delete(statements).where(statements.c.id == statement_id)).rowcount or 0) > 0
