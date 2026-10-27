"""What changed between two statement dates.

Both sides are the dashboard as it stood on each date (compute_view with
as_of), so the same no-double-count rules apply. For each holding that
differs, the change in value is split in two:

- quantity effect: (units after - units before) at the earlier price. For a
  new holding that is its whole value, for a sold-out one minus its value.
- price effect: the rest, so the two always add up to the change in value.

Money stays Decimal until the end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from portfolio.portfolio_view import ZERO, _jsonable, compute_view


def _kind(q0: Decimal, q1: Decimal, had: bool, has: bool) -> str:
    if not had:
        return "new"
    if not has:
        return "exited"
    return "more" if q1 > q0 else "less" if q1 < q0 else "price"


def _diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
          value_before: Decimal, value_after: Decimal) -> dict[str, Any]:
    changes = []
    for key in before.keys() | after.keys():
        b, a = before.get(key), after.get(key)
        q0, v0 = (b["quantity"], b["value"]) if b else (ZERO, ZERO)
        q1, v1 = (a["quantity"], a["value"]) if a else (ZERO, ZERO)
        if q0 == q1 and v0 == v1:
            continue
        if b is None:
            qty_effect = v1
        elif a is None:
            qty_effect = -v0
        elif q0:
            qty_effect = (q1 - q0) * (v0 / q0)
        else:
            qty_effect = ZERO  # no earlier quantity to price (for example a rights entitlement)
        change = v1 - v0
        changes.append({
            "key": key, "isin": (a or b).get("isin"), "name": (a or b)["name"],
            "kind": _kind(q0, q1, b is not None, a is not None),
            "quantity_before": q0, "quantity_after": q1, "value_before": v0, "value_after": v1,
            "quantity_effect": qty_effect, "price_effect": change - qty_effect,
        })
    changes.sort(key=lambda c: abs(c["value_after"] - c["value_before"]), reverse=True)
    return {
        "changes": changes,
        "totals": {
            "value_before": value_before, "value_after": value_after, "change": value_after - value_before,
            "quantity_effect": sum((c["quantity_effect"] for c in changes), ZERO),
            "price_effect": sum((c["price_effect"] for c in changes), ZERO),
        },
    }


def _accounts(view: dict[str, Any]) -> set[tuple[str, str]]:
    return {(a["label"], a["client"] or "") for h in view["stocks"] for a in h["accounts"]}


def compare(store: Any, from_date: date, to_date: date) -> dict[str, Any]:
    if from_date >= to_date:
        raise ValueError("The first date must be earlier than the second.")
    v0 = compute_view(store, as_of=from_date, today=from_date)
    v1 = compute_view(store, as_of=to_date, today=to_date)
    warnings: list[str] = []

    for label, kind, key in (("demat", "stocks", "demat"), ("mutual fund", "funds", "mf")):
        for d, view in ((from_date, v0), (to_date, v1)):
            st = view["statements"][key]
            if st is None:
                warnings.append(f"No {label} statement on or before {d.isoformat()}: its {kind} count as not held.")
            elif st["period_to"] != d.isoformat():
                warnings.append(f"On {d.isoformat()} the {kind} come from the {label} statement dated {st['period_to']}.")

    if v0["statements"]["demat"] and v1["statements"]["demat"]:
        for name, client in sorted(_accounts(v0) - _accounts(v1)):
            warnings.append(
                f"Account {name} {client} is in the {from_date.isoformat()} statement but not in the "
                f"{to_date.isoformat()} one. If you still hold it, its holdings show as sold; upload a "
                "statement that includes it.")

    src0 = (v0["statements"]["mf"] or {}).get("issuer")
    src1 = (v1["statements"]["mf"] or {}).get("issuer")
    if src0 and src1 and src0 != src1:
        warnings.append(f"Funds are compared across different statements ({src0} before, {src1} after), "
                        "so folios and costs may differ for reasons other than trading.")

    stocks = lambda v: {h["isin"]: {"isin": h["isin"], "name": h["name"], "quantity": h["quantity"],  # noqa: E731
                                    "value": h["value"]} for h in v["stocks"]}
    funds = lambda v: {f["key"]: {"isin": f["isin"], "name": f["scheme"], "quantity": f["units"],  # noqa: E731
                                  "value": f["value"]} for f in v["funds"]}
    fund_diff = _diff(funds(v0), funds(v1), v0["totals"]["mf_value"], v1["totals"]["mf_value"])
    fund_diff.update(source_before=src0, source_after=src1)
    return _jsonable({
        "from": from_date, "to": to_date,
        "demat": _diff(stocks(v0), stocks(v1), v0["totals"]["demat_value"], v1["totals"]["demat_value"]),
        "funds": fund_diff,
        "warnings": warnings,
    })
