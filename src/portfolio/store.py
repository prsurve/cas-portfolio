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

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("event", String(32), nullable=False),
    Column("detail", Text),
)

CHILD_TABLES = (stock_holdings, mf_holdings, mf_transactions)


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
        added = [
            ("statements", "parser_version", "INTEGER NOT NULL DEFAULT 1"),
            ("stock_holdings", "asset_type", "VARCHAR(8) NOT NULL DEFAULT 'equity'"),
            ("stock_holdings", "symbol", "VARCHAR(32)"),
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
            for table, rows in ((stock_holdings, stocks), (mf_holdings, mfs), (mf_transactions, transactions)):
                if rows:
                    conn.execute(insert(table), [{**r, "statement_id": statement_id} for r in rows])
            return statement_id, True

    def list_statements(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(statements).order_by(statements.c.period_to.desc(), statements.c.id.desc()))
            return [dict(r) for r in rows.mappings()]

    def latest_statement(self, kind: str) -> dict[str, Any] | None:
        """The statement with the latest period end for this kind (ties: most recently imported)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                select(statements).where(statements.c.kind == kind)
                .order_by(statements.c.period_to.desc(), statements.c.id.desc()).limit(1)
            ).mappings().first()
            return dict(row) if row else None

    def rows_for(self, table: Table, statement_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(table).where(table.c.statement_id == statement_id)).mappings()
            return [dict(r) for r in rows]

    def delete_statement(self, statement_id: int) -> bool:
        with self.engine.begin() as conn:
            for table in CHILD_TABLES:
                conn.execute(delete(table).where(table.c.statement_id == statement_id))
            return (conn.execute(delete(statements).where(statements.c.id == statement_id)).rowcount or 0) > 0
