"""Money-weighted return (XIRR) of mutual funds, from the transactions on a CAMS or KFintech statement.

Method. XIRR is the yearly rate r at which the money put in, the money taken out and today's value balance:
the sum of `cash flow / (1 + r) ** (days since the first flow / 365)` is zero. Money you put in counts as
negative, money you take out (redemptions, dividend payouts) as positive, and the statement value on its date
as a final positive flow. It is a money-weighted rate for the history the statement shows, not a rate you can
compare with a fund's published return, and it is not tax advice.

What is worked out, and what is left out (never estimated):
- Only funds whose transactions cover the whole holding (the statement opened at zero units), and whose
  transactions add up to the units held. Anything else is skipped, with the reason.
- Only transaction kinds whose cash effect is clear. A fund with a kind this module does not value (tax
  deducted at source, gifts, reversals, segregation, unknown) is skipped, as is one with an amount of the
  unexpected sign.
- If the flows allow more than one rate, or none, no rate is given.
- Less than a year of history is flagged: an annualised rate over a few months can mislead.

All arithmetic is `Decimal`. The root is found by bisection on a fixed grid of rates, so the same inputs always
give the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from portfolio.store import Store, mf_transactions

METHOD = (
    "XIRR is the yearly rate at which the money put in, the money taken out and the statement value balance. "
    "It is money-weighted and covers only the history the statement shows, so it is not comparable with a fund's published return."
)
ZERO = Decimal(0)
DAYS_IN_YEAR = Decimal(365)
UNITS_TOLERANCE = Decimal("0.001")
SHORT_HISTORY_DAYS = 365

# The search covers rates from -99% to +10,000% a year; the grid decides how many roots can be told apart.
GRID_POINTS = 48
MIN_GROWTH, MAX_GROWTH = Decimal("0.01"), Decimal("101")
RATE_PRECISION = Decimal("0.0000001")
MAX_BISECTIONS = 90

# How each transaction kind moves money. "out" and "in" are from the investor's side. casparser gives purchases
# and charges as positive amounts and redemptions as negative ones.
PUTS_IN = {"PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "SWITCH_IN_MERGER", "STAMP_DUTY_TAX", "STT_TAX"}
TAKES_OUT = {"REDEMPTION", "SWITCH_OUT", "SWITCH_OUT_MERGER"}
PAYS_OUT = {"DIVIDEND_PAYOUT"}
NO_CASH = {"DIVIDEND_REINVEST"}  # units are added, no money moves


@dataclass(frozen=True)
class Xirr:
    rate: Decimal | None  # a fraction per year: 0.1 is 10%
    reason: str | None


def _growth_points() -> list[Decimal]:
    """1 + r at each grid point, spaced evenly in logarithm so both heavy losses and huge gains are covered."""
    with localcontext() as ctx:
        ctx.prec = 30
        lo, hi = MIN_GROWTH.ln(), MAX_GROWTH.ln()
        step = (hi - lo) / (GRID_POINTS - 1)
        return [(lo + step * i).exp() for i in range(GRID_POINTS)]


def xirr(flows: list[tuple[date, Decimal]]) -> Xirr:
    """The yearly rate that makes the flows balance, or why there is none to give."""
    merged: dict[date, Decimal] = {}
    for day, amount in flows:
        merged[day] = merged.get(day, ZERO) + Decimal(amount)
    ordered = sorted((d, a) for d, a in merged.items() if a != 0)
    if not any(a < 0 for _, a in ordered) or not any(a > 0 for _, a in ordered):
        return Xirr(None, "The cash flows are all in one direction (nothing was both put in and got back), so no rate exists.")
    first = ordered[0][0]
    years = [Decimal((d - first).days) / DAYS_IN_YEAR for d, _ in ordered]
    amounts = [a for _, a in ordered]

    def npv(growth: Decimal) -> Decimal:
        log = growth.ln()
        return sum((a * (-t * log).exp() for a, t in zip(amounts, years)), ZERO)

    with localcontext() as ctx:
        ctx.prec = 30
        grid = _growth_points()
        values = [npv(g) for g in grid]
        brackets: list[tuple[Decimal, Decimal]] = []
        exact: list[Decimal] = []
        for i, v in enumerate(values):
            if v == 0:
                exact.append(grid[i])
            if i and values[i - 1] != 0 and v != 0 and (values[i - 1] < 0) != (v < 0):
                brackets.append((grid[i - 1], grid[i]))
        if len(exact) + len(brackets) > 1:
            return Xirr(None, "The cash flows fit more than one rate, so no single rate can be given.")
        if exact:
            return Xirr(exact[0] - 1, None)
        if not brackets:
            return Xirr(None, "No rate between -99% and +10,000% a year balances these cash flows.")
        lo, hi = brackets[0]
        f_lo = npv(lo)
        for _ in range(MAX_BISECTIONS):
            mid = (lo + hi) / 2
            f_mid = npv(mid)
            if f_mid == 0 or (hi - lo) < RATE_PRECISION:
                lo = hi = mid
                break
            if (f_mid < 0) == (f_lo < 0):
                lo, f_lo = mid, f_mid
            else:
                hi = mid
        else:
            return Xirr(None, "The calculation did not settle on a rate.")
        return Xirr(((lo + hi) / 2 - 1).quantize(RATE_PRECISION), None)


def cash_flows(rows: list[dict[str, Any]]) -> tuple[list[tuple[date, Decimal]], str | None]:
    """Investor cash flows from transaction rows (negative: money put in), or why they cannot be trusted."""
    flows: list[tuple[date, Decimal]] = []
    for row in rows:
        kind = (row["type"] or "").upper()
        amount = Decimal(row["amount"] or 0)
        units = Decimal(row["units"] or 0)
        if kind in NO_CASH:
            continue
        if kind in PUTS_IN:
            if amount < 0:
                return [], f"A {kind.lower().replace('_', ' ')} on {row['date']} has an amount of the unexpected sign."
            flows.append((row["date"], -amount))
        elif kind in TAKES_OUT:
            if amount > 0:
                return [], f"A {kind.lower().replace('_', ' ')} on {row['date']} has an amount of the unexpected sign."
            flows.append((row["date"], -amount))
        elif kind in PAYS_OUT:
            if amount < 0:
                return [], f"A dividend payout on {row['date']} has an amount of the unexpected sign."
            flows.append((row["date"], amount))
        elif amount != 0 or units != 0:
            return [], (f"The statement has a {kind.lower().replace('_', ' ') or 'transaction of unknown kind'} on {row['date']}, "
                        "whose effect on the money invested this app does not work out.")
    return flows, None


def fund_return(fund: dict[str, Any], rows: list[dict[str, Any]], valuation_date: date) -> dict[str, Any]:
    """The return of one fund of the dashboard view (`fund` as in compute_view), given its transaction rows."""
    result: dict[str, Any] = {
        "key": fund["key"], "scheme": fund["scheme"], "isin": fund["isin"], "amc": fund["amc"], "status": "skipped",
        "reason": None, "xirr": None, "since": None, "days": None, "short_history": False,
        "invested": None, "withdrawn": None, "value": fund["value"], "gain": None, "flows": 0,
        "valuation_date": fund["nav_date"] or valuation_date,
    }
    history = fund["history"]
    if not rows:
        result["reason"] = "No transactions were read for this fund. Only CAMS and KFintech statements list them."
    elif history == "unknown":
        result["reason"] = "This statement does not say whether its transactions cover the whole holding."
    elif history == "incomplete":
        result["reason"] = ("The transactions start after the fund was first bought, so they do not describe the whole holding. "
                            "Upload a CAMS or KFintech statement covering the full history.")
    if result["reason"]:
        return result

    held = sum((Decimal(r["units"] or 0) for r in rows), ZERO)
    if abs(held - fund["units"]) > UNITS_TOLERANCE:
        result["reason"] = "The transactions do not add up to the units the statement says are held, so some may be missing."
        return result
    flows, problem = cash_flows(rows)
    if problem:
        result["reason"] = problem
        return result
    end = result["valuation_date"]
    solved = xirr([*flows, (end, fund["value"])])
    put_in = sum((-a for _, a in flows if a < 0), ZERO)
    taken_out = sum((a for _, a in flows if a > 0), ZERO)
    first = min(d for d, _ in flows) if flows else None
    result.update(
        invested=put_in, withdrawn=taken_out, gain=fund["value"] + taken_out - put_in, flows=len(flows),
        since=first, days=(end - first).days if first else None,
    )
    result["short_history"] = bool(first and (end - first).days < SHORT_HISTORY_DAYS)
    if solved.rate is None:
        result["reason"] = solved.reason
        return result
    result.update(status="ok", xirr=solved.rate)
    return result


def transactions_by_fund(store: Store, view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The transaction rows of the statement the funds come from, grouped like the view groups funds."""
    mf = view["statements"]["mf"]
    out: dict[str, list[dict[str, Any]]] = {}
    if mf is not None:
        for r in store.rows_for(mf_transactions, mf["id"]):
            out.setdefault(r["isin"] or r["scheme"], []).append(r)
    return out


def valuation_date(view: dict[str, Any]) -> date | None:
    mf = view["statements"]["mf"]
    if mf is None:
        return None
    return date.fromisoformat(mf["period_to"]) if isinstance(mf["period_to"], str) else mf["period_to"]


def compute_returns(store: Store, view: dict[str, Any]) -> dict[str, Any]:
    """Returns for every fund in the view, and for the funds that could be worked out taken together."""
    funds = view["funds"]
    rows_by_key = transactions_by_fund(store, view)
    valuation = valuation_date(view)
    per_fund = [fund_return(f, rows_by_key.get(f["key"], []), valuation) for f in funds] if valuation else []
    return {"valuation_date": valuation, "funds": per_fund, "portfolio": _portfolio(funds, per_fund, rows_by_key),
            "method": METHOD}


def _portfolio(funds: list[dict[str, Any]], per_fund: list[dict[str, Any]], rows_by_key: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    total_value = sum((f["value"] for f in funds), ZERO)
    done = [r for r in per_fund if r["status"] == "ok"]
    out: dict[str, Any] = {
        "status": "skipped", "reason": None, "xirr": None, "since": None, "days": None, "short_history": False,
        "funds_total": len(funds), "funds_included": len(done), "value_total": total_value,
        "value_included": sum((r["value"] for r in done), ZERO),
        "coverage_pct": None, "invested": None, "withdrawn": None, "gain": None,
    }
    if total_value:
        out["coverage_pct"] = out["value_included"] / total_value * 100
    if not done:
        out["reason"] = "No fund has a complete, consistent transaction history, so no portfolio rate can be given."
        return out
    flows: list[tuple[date, Decimal]] = []
    for r in done:
        f, _ = cash_flows(rows_by_key[r["key"]])
        flows += [*f, (r["valuation_date"], r["value"])]
    solved = xirr(flows)
    first = min(r["since"] for r in done)
    last = max(r["valuation_date"] for r in done)
    out.update(
        invested=sum((r["invested"] for r in done), ZERO), withdrawn=sum((r["withdrawn"] for r in done), ZERO),
        gain=sum((r["gain"] for r in done), ZERO), since=first, days=(last - first).days,
        short_history=(last - first).days < SHORT_HISTORY_DAYS,
    )
    if solved.rate is None:
        out["reason"] = solved.reason
        return out
    out.update(status="ok", xirr=solved.rate)
    return out
