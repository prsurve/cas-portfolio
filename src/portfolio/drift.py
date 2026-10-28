"""How far the current asset mix is from the mix the user is aiming for.

Pure arithmetic on statement values: for each asset class, its share of the total now, the target share, the
difference in percentage points, and the amount that difference is worth. It states a gap, it does not say what
to buy or sell, and it ignores tax and costs. Classes are the dashboard's own (`ASSET_CLASS_LABELS`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from portfolio.portfolio_view import ASSET_CLASS_LABELS

ZERO = Decimal(0)
HUNDRED = Decimal(100)
SUM_TOLERANCE = Decimal("0.01")


class InvalidTargets(ValueError):
    """The target mix cannot be used. The message is safe to show."""


def clean_targets(raw: Any) -> dict[str, Decimal]:
    """Validate a {class: percent} mapping: known classes, each 0-100, adding up to 100. Empty means no targets."""
    if not isinstance(raw, dict):
        raise InvalidTargets("Send the targets as an object of asset class to percentage.")
    out: dict[str, Decimal] = {}
    for label, value in raw.items():
        if label not in ASSET_CLASS_LABELS:
            raise InvalidTargets(f"Unknown asset class: {str(label)[:40]}")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise InvalidTargets(f"The percentage for {label} is not a number.")
        try:
            pct = Decimal(str(value))
        except ArithmeticError:
            raise InvalidTargets(f"The percentage for {label} is not a number.") from None
        if not pct.is_finite() or pct < 0 or pct > 100:
            raise InvalidTargets(f"The percentage for {label} must be between 0 and 100.")
        out[label] = pct
    if out and abs(sum(out.values(), ZERO) - HUNDRED) > SUM_TOLERANCE:
        raise InvalidTargets(f"The targets add up to {sum(out.values(), ZERO).normalize():f}, not 100.")
    return out


def compute_drift(classes: list[dict[str, Any]], total: Decimal, targets: dict[str, Decimal]) -> dict[str, Any] | None:
    """Rows for every class that has a value or a target, or None when there are no targets or no value to compare."""
    if not targets or not total:
        return None
    now = {c["label"]: Decimal(str(c["value"])) for c in classes}
    rows = []
    for label in ASSET_CLASS_LABELS:
        value, target = now.get(label, ZERO), targets.get(label, ZERO)
        if not value and not target:
            continue
        weight = value / total * HUNDRED
        rows.append({"label": label, "value": value, "weight": weight, "target": target, "gap_pct": weight - target,
                     "gap_value": value - target / HUNDRED * total})
    return {"total": total, "rows": rows,
            "largest_gap_pct": max((abs(r["gap_pct"]) for r in rows), default=ZERO),
            "note": "Each gap is the class's share now minus its target. A positive gap is above target. This states a gap, not what to do about it."}
