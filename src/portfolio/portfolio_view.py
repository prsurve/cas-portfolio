"""Build the dashboard from the latest statement of each kind.

- Demat holdings (stocks, ETFs/demat fund units, bonds) come from the latest
  NSDL/CDSL statement, valued at its statement date. The same ISIN in several
  demat accounts appears once, with a line per account underneath. Purchase
  cost isn't on these statements.
- Mutual fund folios come from one statement only: the newest of the latest
  CAMS/KFintech statement and the latest NSDL/CDSL statement (both list
  them). On the same date CAMS/KFintech wins, since it also has transactions.
  Values are never summed across statements, so nothing is counted twice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from portfolio.importers.cas import TOLERANCE, broker_label
from portfolio.store import Store, as_utc, mf_holdings, statement_accounts, stock_holdings

ZERO = Decimal(0)
# The total adds stocks and funds from possibly different statements. Beyond this
# many days apart, the dashboard warns that the total mixes dates.
MIXED_DATES_AFTER_DAYS = 7
# A NAV older than this many days (counted from today) is flagged as stale.
PRICE_STALE_AFTER_DAYS = 7
ASSET_LABELS = {"equity": "Stocks", "fund": "ETFs & demat funds", "bond": "Bonds & SGBs"}


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _pct(part: Decimal, whole: Decimal) -> float | None:
    return _num(part / whole * 100) if whole else None


def _statement_info(st: dict[str, Any] | None, today: date, stale_after_days: int) -> dict[str, Any] | None:
    if st is None:
        return None
    age = (today - st["period_to"]).days
    return {
        "id": st["id"], "issuer": st["issuer"], "cas_type": st["cas_type"],
        "period_from": st["period_from"].isoformat() if st["period_from"] else None,
        "period_to": st["period_to"].isoformat(), "age_days": age, "stale": age > stale_after_days,
        "imported_at": as_utc(st["imported_at"]).isoformat(),
        "warnings": st["warnings"].split("\n") if st["warnings"] else [],
    }


def _as_of(*statements: dict[str, Any] | None) -> dict[str, Any] | None:
    """The date range the total covers: the statements it was built from."""
    dates = [s["period_to"] for s in statements if s]
    if not dates:
        return None
    spread = (max(dates) - min(dates)).days
    return {"from": min(dates), "to": max(dates), "spread_days": spread, "mixed": spread > MIXED_DATES_AFTER_DAYS}


def _stocks(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r["isin"]].append(r)
    out = []
    for isin, members in groups.items():
        qty = sum((Decimal(m["quantity"]) for m in members), ZERO)
        value = sum((Decimal(m["value"] or 0) for m in members), ZERO)
        out.append({
            "isin": isin,
            "asset_type": members[0]["asset_type"],
            "name": next((m["name"] for m in members if m["name"]), isin),
            "symbol": next((m["symbol"] for m in members if m.get("symbol")), None),
            "quantity": qty,
            "price": value / qty if qty else None,
            "value": value,
            "brokers": sorted({broker_label(m["dp_name"]) for m in members}),
            "accounts": [
                {"label": broker_label(m["dp_name"]), "dp_name": m["dp_name"], "client": m["client_id_masked"],
                 "depository": m["depository"], "quantity": Decimal(m["quantity"]), "value": Decimal(m["value"] or 0)}
                for m in sorted(members, key=lambda m: (broker_label(m["dp_name"]), m["client_id_masked"] or ""))
            ],
        })
    out.sort(key=lambda h: h["value"], reverse=True)
    return out, sum((h["value"] for h in out), ZERO)


def _attach_latest(store: Store, funds: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Value each fund at its latest public NAV, next to the statement value.

    A NAV counts only if it is at least as new as the statement's own valuation.
    A fund with no usable NAV gets `latest: None`; nothing is estimated. Returns
    totals over the priced funds only, with how many that is.
    """
    found = store.prices_for([f["isin"] for f in funds if f["isin"]], "AMFI")
    priced = []
    for f in funds:
        p = found.get(f["isin"]) if f["isin"] else None
        if p is None or (f["nav_date"] is not None and p["price_date"] < f["nav_date"]):
            f["latest"] = None
            continue
        value = Decimal(f["units"]) * Decimal(p["price"])
        age = (today - p["price_date"]).days
        f["latest"] = {
            "nav": Decimal(p["price"]), "nav_date": p["price_date"], "source": "AMFI", "value": value,
            "change": value - f["value"], "change_pct": _pct(value - f["value"], f["value"]),
            "days_old": age, "stale": age > PRICE_STALE_AFTER_DAYS,
        }
        priced.append(f)
    if not priced:
        return None
    value = sum((f["latest"]["value"] for f in priced), ZERO)
    was = sum((f["value"] for f in priced), ZERO)
    return {"value": value, "statement_value": was, "change": value - was, "change_pct": _pct(value - was, was),
            "priced": len(priced), "total": len(funds), "as_of": min(f["latest"]["nav_date"] for f in priced)}


def _history(members: list[dict[str, Any]]) -> str:
    """Whether a fund's transactions describe the whole holding (see mf_holdings.open_units)."""
    opening = [m.get("open_units") for m in members]
    if any(o is None for o in opening):
        return "unknown"
    return "incomplete" if any(o != 0 for o in opening) else "complete"


def _completeness(store: Store, demat: dict[str, Any] | None, stocks: list[dict[str, Any]],
                  funds: list[dict[str, Any]]) -> dict[str, Any]:
    """How much of what the statements printed was read, and what is missing."""
    out: dict[str, Any] = {"demat": None, "funds": None, "stocks": None}
    if demat:
        rows = store.rows_for(statement_accounts, demat["id"])
        if not rows:
            out["demat"] = {"available": False}  # imported before reconciliation was stored
        else:
            def line(r: dict[str, Any]) -> dict[str, Any]:
                gap = Decimal(r["statement_total"]) - Decimal(r["imported_value"])
                return {"label": broker_label(r["name"]), "client": r["client_id_masked"],
                        "depository": r["depository"], "statement_total": Decimal(r["statement_total"]),
                        "imported_value": Decimal(r["imported_value"]), "gap": gap, "ok": abs(gap) <= TOLERANCE}
            accounts = [line(r) for r in rows if r["section"] == "demat"]
            folios = next((line(r) for r in rows if r["section"] == "folios"), None)
            total = sum((a["statement_total"] for a in accounts), ZERO)
            read = sum((a["imported_value"] for a in accounts), ZERO)
            covered = sum((min(a["imported_value"], a["statement_total"]) for a in accounts), ZERO)
            out["demat"] = {
                "available": True, "accounts": accounts, "all_match": all(a["ok"] for a in accounts),
                "statement_total": total, "imported_value": read, "gap": total - read,
                "coverage_pct": _pct(covered, total),
                "folios": {k: folios[k] for k in ("statement_total", "imported_value", "gap", "ok")}
                if folios else None,
            }
        equities = [h for h in stocks if h["asset_type"] == "equity"]
        out["stocks"] = {"count": len(equities), "without_symbol": sum(1 for h in equities if not h["symbol"])}
    if funds:
        costed = sum(1 for f in funds if f["cost"] is not None)
        out["funds"] = {
            "count": len(funds), "with_cost": costed, "without_cost": len(funds) - costed,
            "unknown_amc": sum(1 for f in funds if not f["amc"]),
            "priced": sum(1 for f in funds if f["latest"]),
            "without_price": sum(1 for f in funds if not f["latest"]),
            "history": {k: sum(1 for f in funds if f["history"] == k) for k in ("complete", "incomplete", "unknown")},
        }
    return out


def _funds(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r["isin"] or r["scheme"]].append(r)
    out = []
    for key, members in groups.items():
        value = sum((Decimal(m["value"] or 0) for m in members), ZERO)
        has_cost = all(m["cost"] is not None for m in members)
        cost = sum((Decimal(m["cost"]) for m in members), ZERO) if has_cost else None
        out.append({
            "key": key,
            "scheme": members[0]["scheme"],
            "amc": members[0]["amc"],
            "isin": members[0]["isin"],
            "scheme_type": members[0]["scheme_type"],
            "history": _history(members),
            "units": sum((Decimal(m["units"]) for m in members), ZERO),
            "nav": members[0]["nav"],
            "nav_date": members[0]["nav_date"],
            "cost": cost,
            "value": value,
            "gain": value - cost if cost is not None else None,
            "gain_pct": _pct(value - cost, cost) if cost else None,
            "folios": [
                {"folio": m["folio_masked"], "units": Decimal(m["units"]), "cost": m["cost"],
                 "value": Decimal(m["value"] or 0)}
                for m in members
            ],
        })
    out.sort(key=lambda f: f["value"], reverse=True)
    costed = [f for f in out if f["cost"] is not None]
    totals = {
        "value": sum((f["value"] for f in out), ZERO),
        "cost": sum((f["cost"] for f in costed), ZERO),
        "costed_value": sum((f["value"] for f in costed), ZERO),
        "uncosted": Decimal(len(out) - len(costed)),
    }
    return out, totals


def _allocation(items: dict[str, Decimal], total: Decimal) -> list[dict[str, Any]]:
    return [
        {"label": label, "value": _num(v), "weight": _pct(v, total)}
        for label, v in sorted(items.items(), key=lambda kv: kv[1], reverse=True) if v
    ]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _num(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def build_view(store: Store, stale_after_days: int = 45, today: date | None = None,
               as_of: date | None = None) -> dict[str, Any]:
    """The dashboard as JSON-ready data (money as floats)."""
    return _jsonable(compute_view(store, stale_after_days, today, as_of))


def compute_view(store: Store, stale_after_days: int = 45, today: date | None = None,
                 as_of: date | None = None) -> dict[str, Any]:
    """The dashboard with money still as Decimal, for further calculation.

    With as_of, the same rules applied to the statements that existed on that date."""
    today = today or datetime.now(timezone.utc).date()
    demat = store.latest_statement("demat", as_of)
    candidates = [s for s in (store.latest_statement("mf", as_of), demat) if s]
    fund_rows = {s["id"]: store.rows_for(mf_holdings, s["id"]) for s in candidates}
    # Funds come from one statement only: the newest that lists any; CAMS/KFintech wins a tie.
    mf = max((s for s in candidates if fund_rows[s["id"]]),
             key=lambda s: (s["period_to"], s["kind"] == "mf"), default=None)

    stocks, demat_value = _stocks(store.rows_for(stock_holdings, demat["id"]) if demat else [])
    funds, fund_totals = _funds(fund_rows[mf["id"]] if mf else [])
    # Latest prices apply to today's dashboard only, never to a past date.
    mf_latest = _attach_latest(store, funds, today) if as_of is None else None
    if as_of is not None:
        for f in funds:
            f["latest"] = None
    total = demat_value + fund_totals["value"]
    by_asset: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for h in stocks:
        by_asset[h["asset_type"]] += h["value"]

    by_broker: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for h in stocks:
        for a in h["accounts"]:
            by_broker[a["label"]] += a["value"]
    by_amc: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for f in funds:
        by_amc[f["amc"] or "Unknown"] += f["value"]
        by_type[(f["scheme_type"] or "Other").title()] += f["value"]

    mf_gain = fund_totals["costed_value"] - fund_totals["cost"]
    return {
        "totals": {
            "value": total,
            "demat_value": demat_value,
            "stocks_value": by_asset["equity"],
            "demat_funds_value": by_asset["fund"],
            "bonds_value": by_asset["bond"],
            "mf_value": fund_totals["value"],
            "mf_cost": fund_totals["cost"] if fund_totals["costed_value"] else None,
            "mf_gain": mf_gain if fund_totals["costed_value"] else None,
            "mf_gain_pct": _pct(mf_gain, fund_totals["cost"]),
            "mf_without_cost": int(fund_totals["uncosted"]),
            "stocks_count": sum(1 for h in stocks if h["asset_type"] == "equity"),
            "demat_count": len(stocks),
            "multi_broker_stocks": sum(1 for h in stocks if len(h["accounts"]) > 1),
            "funds_count": len(funds),
            "mf_latest": mf_latest,
        },
        "as_of": _as_of(demat, mf),
        "completeness": _completeness(store, demat, stocks, funds),
        "statements": {
            "demat": _statement_info(demat, today, stale_after_days),
            "mf": _statement_info(mf, today, stale_after_days),
        },
        "allocation": {
            "asset_class": _allocation(
                {**{ASSET_LABELS[k]: v for k, v in by_asset.items()}, "Mutual funds (folios)": fund_totals["value"]},
                total,
            ),
            "broker": _allocation(dict(by_broker), demat_value),
            "amc": _allocation(dict(by_amc), fund_totals["value"]),
            "fund_type": _allocation(dict(by_type), fund_totals["value"]),
        },
        "stocks": stocks,
        "funds": funds,
    }
