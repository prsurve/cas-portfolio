"""Tax lots of mutual funds (first in, first out), from the transactions on a CAMS or KFintech statement.

Method. Every purchase, switch-in or reinvested dividend opens a lot: units, date and the amount paid. Every
redemption or switch-out uses the oldest lots first. What is left is the open lots; what was used gives the
realised gain of each sale, split by lot with the days each lot was held. This is a plain record of the
statement for you to check against your own tax working. It is not tax advice: whether a gain is short or long
term depends on the kind of fund and the date of purchase, so only the days held are shown, with a flag for
"more than a year" as a plain day count.

What is worked out, and what is left out (never estimated):
- Only funds the XIRR code also accepts: the transactions cover the whole holding and add up to the units held.
- Only transaction kinds whose effect on units and cost is clear. A fund with another kind that moves units or
  money (tax deducted at source, gifts, reversals, segregation, unknown) is skipped, with the reason.
- Stamp duty and STT rows move no units and are left out of the cost; dividend payouts are not lots.
- A sale of more units than the open lots hold means a purchase is missing: the fund is skipped.
- Proceeds are the redemption amount printed on the statement. Same-day purchases are matched before sales.

All arithmetic is `Decimal`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from portfolio.returns import UNITS_TOLERANCE, history_problem, transactions_by_fund, valuation_date
from portfolio.store import Store

ZERO = Decimal(0)
CENT = Decimal("0.000001")
OVER_A_YEAR_DAYS = 365

METHOD = (
    "Lots are matched first in, first out from the statement's own transactions. The days held are a plain count, "
    "and whether a gain is short or long term depends on the kind of fund and the purchase date, so check it "
    "against your own working. This is not tax advice."
)

OPENS_LOT = {"PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "SWITCH_IN_MERGER", "DIVIDEND_REINVEST"}
USES_LOTS = {"REDEMPTION", "SWITCH_OUT", "SWITCH_OUT_MERGER"}
IGNORED = {"STAMP_DUTY_TAX", "STT_TAX", "DIVIDEND_PAYOUT"}


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT)


def _label(kind: str) -> str:
    return kind.lower().replace("_", " ") or "transaction of unknown kind"


def match_lots(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """(open lots, sales, problem). Each lot is {date, units, cost}; each sale lists the lot pieces it used."""
    # Purchases before sales on the same day; otherwise the statement's own order.
    ordered = sorted(rows, key=lambda r: (r["date"], (r["type"] or "").upper() in USES_LOTS))
    lots: list[dict[str, Any]] = []
    sales: list[dict[str, Any]] = []
    for row in ordered:
        kind = (row["type"] or "").upper()
        units = Decimal(row["units"] or 0)
        amount = Decimal(row["amount"] or 0)
        if kind in IGNORED:
            continue
        if kind in OPENS_LOT:
            if units <= 0 or amount < 0:
                return [], [], f"A {_label(kind)} on {row['date']} has units or an amount of the unexpected sign."
            lots.append({"date": row["date"], "units": units, "cost": amount, "left": units})
        elif kind in USES_LOTS:
            if units >= 0 or amount > 0:
                return [], [], f"A {_label(kind)} on {row['date']} has units or an amount of the unexpected sign."
            need, proceeds = -units, -amount
            pieces: list[dict[str, Any]] = []
            for lot in lots:
                if need <= UNITS_TOLERANCE:
                    break
                if lot["left"] <= 0:
                    continue
                take = min(lot["left"], need)
                pieces.append({
                    "bought": lot["date"], "units": take, "cost": lot["cost"] * take / lot["units"],
                    "days_held": (row["date"] - lot["date"]).days,
                })
                lot["left"] -= take
                need -= take
            if need > UNITS_TOLERANCE:
                return [], [], (f"A {_label(kind)} on {row['date']} sells more units than the earlier purchases hold, "
                                "so a purchase is missing.")
            cost = sum((p["cost"] for p in pieces), ZERO)
            sold_units = sum((p["units"] for p in pieces), ZERO)
            for p in pieces:
                # The sale amount is shared by units, so the pieces add up to it exactly (up to rounding).
                p["proceeds"] = proceeds * p["units"] / sold_units if sold_units else ZERO
                p["gain"] = p["proceeds"] - p["cost"]
                p["over_a_year"] = p["days_held"] > OVER_A_YEAR_DAYS
            sales.append({"date": row["date"], "kind": kind, "units": sold_units, "proceeds": proceeds, "cost": cost,
                          "gain": proceeds - cost, "pieces": pieces})
        elif amount != 0 or units != 0:
            return [], [], (f"The statement has a {_label(kind)} on {row['date']}, "
                            "whose effect on units and cost this app does not work out.")
    open_lots = [l for l in lots if l["left"] > UNITS_TOLERANCE]
    return open_lots, sales, None


def fund_lots(fund: dict[str, Any], rows: list[dict[str, Any]], valuation: date) -> dict[str, Any]:
    """Open lots and realised sales of one fund of the dashboard view (`fund` as in compute_view)."""
    end = fund["nav_date"] or valuation
    result: dict[str, Any] = {
        "key": fund["key"], "scheme": fund["scheme"], "isin": fund["isin"], "amc": fund["amc"], "status": "skipped",
        "reason": None, "valuation_date": end, "value": fund["value"], "open_lots": [], "sales": [],
        "open_units": None, "open_cost": None, "unrealised_gain": None,
        "realised_proceeds": None, "realised_cost": None, "realised_gain": None,
        "realised_gain_held_over_a_year": None, "realised_gain_held_up_to_a_year": None,
    }
    result["reason"] = history_problem(fund, rows)
    if result["reason"]:
        return result
    open_lots, sales, problem = match_lots(rows)
    if problem:
        result["reason"] = problem
        return result

    units_left = sum((l["left"] for l in open_lots), ZERO)
    if abs(units_left - fund["units"]) > UNITS_TOLERANCE:
        result["reason"] = "The lots left after the sales do not match the units the statement says are held."
        return result
    per_unit = fund["value"] / fund["units"] if fund["units"] else ZERO
    shown_lots = []
    for lot in open_lots:
        cost = lot["cost"] * lot["left"] / lot["units"]
        value = lot["left"] * per_unit
        days = (end - lot["date"]).days
        shown_lots.append({
            "bought": lot["date"], "units": lot["left"], "cost": _q(cost), "value": _q(value), "gain": _q(value - cost),
            "days_held": days, "over_a_year": days > OVER_A_YEAR_DAYS,
        })
    shown_sales = [{
        "date": s["date"], "kind": s["kind"], "units": s["units"], "proceeds": _q(s["proceeds"]), "cost": _q(s["cost"]),
        "gain": _q(s["gain"]),
        "pieces": [{**p, "cost": _q(p["cost"]), "proceeds": _q(p["proceeds"]), "gain": _q(p["gain"])} for p in s["pieces"]],
    } for s in sales]
    pieces = [p for s in sales for p in s["pieces"]]
    open_cost = sum((l["cost"] for l in shown_lots), ZERO)
    result.update(
        status="ok", open_lots=shown_lots, sales=shown_sales, open_units=units_left, open_cost=open_cost,
        unrealised_gain=sum((l["gain"] for l in shown_lots), ZERO),
        realised_proceeds=sum((s["proceeds"] for s in shown_sales), ZERO),
        realised_cost=sum((s["cost"] for s in shown_sales), ZERO),
        realised_gain=sum((s["gain"] for s in shown_sales), ZERO),
        realised_gain_held_over_a_year=_q(sum((p["gain"] for p in pieces if p["over_a_year"]), ZERO)),
        realised_gain_held_up_to_a_year=_q(sum((p["gain"] for p in pieces if not p["over_a_year"]), ZERO)),
    )
    return result


def compute_lots(store: Store, view: dict[str, Any]) -> dict[str, Any]:
    """Lots for every fund in the view, with the totals over the funds that could be worked out."""
    valuation = valuation_date(view)
    rows_by_key = transactions_by_fund(store, view)
    funds = [fund_lots(f, rows_by_key.get(f["key"], []), valuation) for f in view["funds"]] if valuation else []
    done = [f for f in funds if f["status"] == "ok"]
    totals = {
        "funds_total": len(funds), "funds_included": len(done),
        "open_cost": sum((f["open_cost"] for f in done), ZERO),
        "unrealised_gain": sum((f["unrealised_gain"] for f in done), ZERO),
        "realised_proceeds": sum((f["realised_proceeds"] for f in done), ZERO),
        "realised_cost": sum((f["realised_cost"] for f in done), ZERO),
        "realised_gain": sum((f["realised_gain"] for f in done), ZERO),
    }
    return {"valuation_date": valuation, "funds": funds, "totals": totals, "method": METHOD}
