"""Exact money and quantity storage.

SQLite has no decimal type: a NUMERIC column stores a float, which silently
changes values with many digits. On SQLite, Money is therefore stored as an
integer count of millionths (scale 6, the same as the declared precision).
On other databases it is a plain NUMERIC.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger, Numeric
from sqlalchemy.types import TypeDecorator

SCALE = 6
FACTOR = Decimal(10) ** SCALE
INT64_MAX = 2**63 - 1


def to_micros(value: Any) -> int:
    micros = int((Decimal(str(value)) * FACTOR).to_integral_value(ROUND_HALF_UP))
    if abs(micros) > INT64_MAX:
        raise ValueError(f"Amount {value} is too large to store")
    return micros


def from_micros(micros: int) -> Decimal:
    return Decimal(micros).scaleb(-SCALE)


class Money(TypeDecorator):
    impl = Numeric(24, SCALE, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(self.impl)

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name != "sqlite":
            return value
        return to_micros(value)

    def process_result_value(self, value, dialect):
        if value is None or dialect.name != "sqlite":
            return value
        return from_micros(int(value))
