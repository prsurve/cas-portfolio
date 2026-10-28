"""Statement value over time.

One point per statement end date. Each point is the dashboard as it stood on
that date (build_view with as_of), so it follows the same rules: stocks from
one demat statement, funds from one statement, never summed across statements.

This is the value of what was held, not a return: money added to the
portfolio raises it too.
"""

from __future__ import annotations

from typing import Any

from portfolio.portfolio_view import build_view
from portfolio.store import Store


def value_history(store: Store) -> list[dict[str, Any]]:
    points = []
    for d in store.statement_dates():
        v = build_view(store, as_of=d, today=d)
        t, a, st = v["totals"], v["as_of"], v["statements"]
        points.append({
            "date": d.isoformat(),
            "value": t["value"],
            "demat_value": t["demat_value"],
            "mf_value": t["mf_value"],
            "by_class": [{"label": r["label"], "value": r["value"]} for r in v["allocation"]["asset_class"]],
            "mf_source": st["mf"]["issuer"] if st["mf"] else None,
            "spread_days": a["spread_days"],
            "mixed": a["mixed"],
        })
    return points
