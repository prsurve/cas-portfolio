"""A dated list of what the stored statements record: each statement's end date and each mutual fund transaction.

Only what the statements say. Dividends and corporate actions on shares are not in these files reliably, so they
are not listed. Overlapping CAMS or KFintech statements print the same transactions twice, so a transaction is
listed as many times as the busiest single statement shows it: never summed across statements, and two identical
transactions on one day in one statement both stay.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select

from portfolio.store import Store, mf_transactions

TRANSACTION_LABELS = {
    "PURCHASE": "Purchase", "PURCHASE_SIP": "SIP purchase", "REDEMPTION": "Redemption", "SWITCH_IN": "Switch in",
    "SWITCH_IN_MERGER": "Switch in (merger)", "SWITCH_OUT": "Switch out", "SWITCH_OUT_MERGER": "Switch out (merger)",
    "DIVIDEND_PAYOUT": "Dividend paid out", "DIVIDEND_REINVEST": "Dividend reinvested", "STAMP_DUTY_TAX": "Stamp duty",
    "STT_TAX": "Securities transaction tax", "TDS_TAX": "Tax deducted at source",
}


def _transactions(store: Store) -> list[dict[str, Any]]:
    with store.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(select(mf_transactions).order_by(mf_transactions.c.id)).mappings()]
    by_statement: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_statement.setdefault(r["statement_id"], []).append(r)

    def key(r: dict[str, Any]) -> tuple:
        return (r["folio_masked"], r["isin"] or r["scheme"], r["date"], r["type"], r["amount"], r["units"])

    best: dict[tuple, tuple[int, int]] = {}  # key -> (most copies in one statement, that statement)
    for sid, items in by_statement.items():
        for k, n in Counter(key(r) for r in items).items():
            if k not in best or n > best[k][0]:
                best[k] = (n, sid)
    return [r for r in rows if best[key(r)][1] == r["statement_id"]]


def build_timeline(store: Store, limit: int = 500) -> dict[str, Any]:
    """Events newest first, at most `limit`, with the total before the cut so the page can say there are more."""
    events: list[dict[str, Any]] = []
    for s in store.list_statements():
        events.append({"date": s["period_to"], "kind": "statement", "title": "Statement imported",
                       "issuer": s["issuer"], "statement_kind": s["kind"], "period_from": s["period_from"],
                       "imported_at": s["imported_at"]})
    for r in _transactions(store):
        if r["date"] is None:
            continue
        kind = (r["type"] or "").upper()
        events.append({"date": r["date"], "kind": "transaction", "title": TRANSACTION_LABELS.get(kind, "Other transaction"),
                       "scheme": r["scheme"], "isin": r["isin"], "type": kind or None, "amount": r["amount"], "units": r["units"]})
    events.sort(key=lambda e: (e["date"], e["kind"] == "statement"), reverse=True)
    return {"total": len(events), "events": events[:limit]}
