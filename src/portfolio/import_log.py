"""What was imported and when: one statement in detail, and the audit trail.

Only what the database already holds. Reconciliation is the stored comparison of the total each statement
printed with the total that was read (`statement_accounts`). The audit log records events and short codes,
never parser messages, because those can quote statement text.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from portfolio.importers.cas import PARSER_VERSION, TOLERANCE
from portfolio.portfolio_view import _jsonable
from portfolio.store import Store, as_utc, mf_holdings, mf_transactions, statement_accounts, stock_holdings


def statement_summary(s: dict[str, Any]) -> dict[str, Any]:
    """A stored statement as the list shows it."""
    return {
        "id": s["id"], "issuer": s["issuer"], "kind": s["kind"], "cas_type": s["cas_type"],
        "period_from": s["period_from"].isoformat() if s["period_from"] else None,
        "period_to": s["period_to"].isoformat(),
        "imported_at": as_utc(s["imported_at"]).isoformat(),
        "parser_version": s["parser_version"],
        # Imported by an older parser: uploading the same PDF again would read more from it.
        "outdated": s["parser_version"] < PARSER_VERSION,
        "warnings": s["warnings"].split("\n") if s["warnings"] else [],
    }


def statement_detail(store: Store, statement_id: int) -> dict[str, Any] | None:
    """One statement with what was read from it and how each printed total compares with the rows read."""
    found = next((s for s in store.list_statements() if s["id"] == statement_id), None)
    if found is None:
        return None
    checks = []
    for a in store.rows_for(statement_accounts, statement_id):
        printed, read = Decimal(a["statement_total"]), Decimal(a["imported_value"])
        gap = printed - read
        checks.append({
            "section": a["section"], "name": a["name"], "depository": a["depository"],
            "client": a["client_id_masked"], "statement_total": printed, "imported_value": read,
            "gap": gap, "ok": abs(gap) <= TOLERANCE,
        })
    detail = {
        **statement_summary(found),
        "counts": {"stocks": len(store.rows_for(stock_holdings, statement_id)),
                   "funds": len(store.rows_for(mf_holdings, statement_id)),
                   "transactions": len(store.rows_for(mf_transactions, statement_id))},
        "reconciliation": checks,
        # Statements imported before parser version 6 stored no reconciliation.
        "has_reconciliation": bool(checks),
    }
    return _jsonable(detail)


def audit_events(store: Store, limit: int = 50) -> list[dict[str, Any]]:
    return [{"ts": as_utc(e["ts"]).isoformat(), "event": e["event"], "detail": e["detail"]}
            for e in store.recent_audit(limit)]
