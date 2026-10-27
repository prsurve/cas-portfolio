"""One holding, explained.

`holding_detail()` takes a position out of the dashboard view and shows where each number comes from:
the statement and date it was read from, the rows that were added up, and the method. The inputs of
every explanation are the same values the view summed, so the text cannot disagree with the figures.

Rules kept from the dashboard: a holding is one ISIN (a fund held in a demat account and in a folio
shows both parts); nothing is summed across statements; latest prices sit beside statement values and
are never mixed in; anything that cannot be worked out is left out with a reason, never estimated.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from portfolio.portfolio_view import ZERO, _jsonable, _pct, compute_view
from portfolio.returns import fund_return, transactions_by_fund, valuation_date
from portfolio.store import Store

ASSET_CLASS = {"equity": "Stock", "fund": "ETF or demat fund", "bond": "Bond or SGB"}
SOURCE_LABEL = {"demat": "Demat statement", "folio": "Mutual fund statement"}


def _input(label: str, value: Any, unit: str) -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit}


def _calc(id_: str, label: str, value: Any, unit: str, method: str, *, inputs: list[dict[str, Any]] | None = None,
          source: str | None = None, as_of: date | None = None, limitation: str | None = None) -> dict[str, Any]:
    return {"id": id_, "label": label, "value": value, "unit": unit, "method": method, "inputs": inputs or [],
            "source": source, "as_of": as_of, "limitation": limitation}


def _find(view: dict[str, Any], key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    stock = next((h for h in view["stocks"] if h["isin"] == key), None)
    fund = next((f for f in view["funds"] if key in (f["isin"], f["key"])), None)
    return stock, fund


def _statement_label(info: dict[str, Any]) -> str:
    return f"{info['issuer']} statement dated {info['period_to']}"


def _latest_calc(kind: str, units: Decimal, statement_value: Decimal, latest: dict[str, Any] | None) -> dict[str, Any]:
    quantity_label = "Units" if kind == "folio" else "Quantity"
    if latest is None:
        reason = ("No exchange closing price dated on or after the statement was found for this ISIN. Use Refresh prices. "
                  "Unlisted or delisted securities have none, and a share is never valued at a fund NAV."
                  if kind == "demat" else
                  "No AMFI NAV dated on or after the statement's own NAV date was found for this ISIN. Use Refresh prices. "
                  "A fund AMFI does not list stays at its statement value.")
        return _calc("latest_value", "Value at latest price", None, "inr", "Not available.", limitation=reason + " Nothing is estimated.")
    note = f"This price is {latest['days_old']} days old." if latest["stale"] else None
    return _calc(
        "latest_value", "Value at latest price", latest["value"], "inr",
        f"{quantity_label.lower()} on the statement × latest {latest['source']} price. Shown beside the statement value, never added into totals.",
        inputs=[_input(quantity_label, units, "quantity"), _input(f"{latest['source']} price", latest["price"], "inr"),
                _input("Statement value", statement_value, "inr"), _input("Change from statement value", latest["change"], "inr")],
        source=latest["source"], as_of=latest["price_date"], limitation=note)


def _history(store: Store, key: str, stale_after_days: int) -> list[dict[str, Any]]:
    """The holding at each date a statement changed what the dashboard shows. Each point is the
    dashboard as it stood on that date, so the one-statement-per-source rules hold."""
    points: list[dict[str, Any]] = []
    previous: tuple | None = None
    for d in store.statement_dates():
        view = compute_view(store, stale_after_days, today=d, as_of=d)
        stock, fund = _find(view, key)
        parts = []
        if stock is not None:
            parts.append({"source": "demat", "issuer": view["statements"]["demat"]["issuer"],
                          "statement_date": view["statements"]["demat"]["period_to"], "quantity": stock["quantity"], "value": stock["value"]})
        if fund is not None:
            parts.append({"source": "folio", "issuer": view["statements"]["mf"]["issuer"],
                          "statement_date": view["statements"]["mf"]["period_to"], "quantity": fund["units"], "value": fund["value"]})
        if not parts:
            continue
        signature = tuple((p["source"], p["statement_date"]) for p in parts)
        if signature == previous:  # the same statements as the last point: nothing new to show
            continue
        previous = signature
        points.append({"date": d, "value": sum((p["value"] for p in parts), ZERO),
                       "quantity": sum((p["quantity"] for p in parts), ZERO), "parts": parts})
    return points


def holding_detail(store: Store, key: str, stale_after_days: int = 45, today: date | None = None) -> dict[str, Any] | None:
    """The explained holding for an ISIN (or a fund's name when it has no ISIN), or None if it is not held."""
    view = compute_view(store, stale_after_days, today)
    stock, fund = _find(view, key)
    if stock is None and fund is None:
        return None

    components: list[dict[str, Any]] = []
    calcs: list[dict[str, Any]] = []
    notes: list[str] = []
    total_value = ZERO

    if stock is not None:
        info = view["statements"]["demat"]
        total_value += stock["value"]
        components.append({
            "source": "demat", "statement": info, "quantity": stock["quantity"], "value": stock["value"],
            "accounts": stock["accounts"],
        })
        calcs.append(_calc(
            "statement_value_demat", "Statement value (demat)", stock["value"], "inr",
            f"Sum of the value printed for this ISIN in each demat account on the {_statement_label(info)}.",
            inputs=[_input(f"{a['label']} {a['client'] or ''}".strip(), a["value"], "inr") for a in stock["accounts"]],
            source=info["issuer"], as_of=date.fromisoformat(info["period_to"]),
            limitation=f"The average price shown ({stock['price']:.4f}) is this value divided by the quantity, not a separate printed figure."
            if stock["price"] is not None else None))
        calcs.append(_latest_calc("demat", stock["quantity"], stock["value"], stock["latest"]))
        notes.append("NSDL and CDSL statements do not include what you paid, so no cost, gain or loss is shown for demat holdings.")

    if fund is not None:
        info = view["statements"]["mf"]
        total_value += fund["value"]
        components.append({
            "source": "folio", "statement": info, "quantity": fund["units"], "value": fund["value"], "nav": fund["nav"],
            "nav_date": fund["nav_date"], "cost": fund["cost"], "gain": fund["gain"], "gain_pct": fund["gain_pct"],
            "history": fund["history"], "folios": fund["folios"],
        })
        as_of = date.fromisoformat(info["period_to"])
        calcs.append(_calc(
            "statement_value_folio", "Statement value (mutual fund folios)", fund["value"], "inr",
            f"Sum of the value printed for each folio on the {_statement_label(info)} (units × NAV on the NAV date).",
            inputs=[_input(f"Folio {f['folio'] or ''}".strip(), f["value"], "inr") for f in fund["folios"]],
            source=info["issuer"], as_of=as_of))
        if fund["cost"] is not None:
            calcs.append(_calc(
                "cost", "Amount invested", fund["cost"], "inr", "Sum of the amount invested printed for each folio on the same statement.",
                inputs=[_input(f"Folio {f['folio'] or ''}".strip(), f["cost"], "inr") for f in fund["folios"]],
                source=info["issuer"], as_of=as_of))
            calcs.append(_calc(
                "gain", "Gain on the statement date", fund["gain"], "inr",
                "Statement value minus amount invested. This is a gain as of the statement date, not a return over time.",
                inputs=[_input("Statement value", fund["value"], "inr"), _input("Amount invested", fund["cost"], "inr"),
                        _input("Gain as a percentage of cost", fund["gain_pct"], "percent")],
                source=info["issuer"], as_of=as_of))
        else:
            calcs.append(_calc(
                "cost", "Amount invested", None, "inr", "Not available.", source=info["issuer"], as_of=as_of,
                limitation="This statement prints no amount invested for the fund, so no gain is shown and the fund is left out of the gain totals."))
        ret = fund_return(fund, transactions_by_fund(store, view).get(fund["key"], []), valuation_date(view) or as_of)
        if ret["status"] == "ok":
            calcs.append(_calc(
                "xirr", "Return since the first transaction (XIRR)", ret["xirr"] * 100, "percent",
                "The yearly rate at which the money put in, the money taken out and the statement value balance. "
                "It is money-weighted and covers only the history on this statement.",
                inputs=[_input("Money put in", ret["invested"], "inr"), _input("Money taken out", ret["withdrawn"], "inr"),
                        _input("Statement value", ret["value"], "inr"), _input("Days from the first transaction", ret["days"], "quantity")],
                source=info["issuer"], as_of=ret["valuation_date"],
                limitation="Less than a year of history, so an annualised rate can mislead." if ret["short_history"] else None))
        else:
            calcs.append(_calc("xirr", "Return since the first transaction (XIRR)", None, "percent", "Not available.",
                               source=info["issuer"], as_of=as_of, limitation=ret["reason"] + " Nothing is estimated."))
        calcs.append(_latest_calc("folio", fund["units"], fund["value"], fund["latest"]))

    if stock is not None and fund is not None:
        notes.append("This ISIN is held both in a demat account and in a mutual fund folio. The statements list them separately, so both are counted.")

    portfolio_value = view["totals"]["value"]
    calcs.append(_calc(
        "weight", "Share of the portfolio", _pct(total_value, portfolio_value), "percent",
        "This holding's statement value divided by the dashboard's total statement value.",
        inputs=[_input("Holding", total_value, "inr"), _input("Portfolio total", portfolio_value, "inr")],
        limitation="The total can mix statement dates; see the range shown with it." if view["as_of"] and view["as_of"]["mixed"] else None))

    kind = ASSET_CLASS[stock["asset_type"]] if stock is not None else "Mutual fund"
    detail = {
        "key": key,
        "isin": (stock or fund)["isin"],
        "name": stock["name"] if stock is not None else fund["scheme"],
        "kind": kind,
        "symbol": stock["symbol"] if stock is not None else None,
        "amc": fund["amc"] if fund is not None else None,
        "value": total_value,
        "weight": _pct(total_value, portfolio_value),
        "as_of": view["as_of"],
        "components": components,
        "calculations": calcs,
        "notes": notes,
        "history": _history(store, key, stale_after_days),
        "history_note": "Value of what was held on each statement date. It rises when you buy as well as when prices rise, so it is not a return.",
    }
    return _jsonable(detail)
