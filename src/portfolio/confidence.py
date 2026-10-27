"""How far each figure can be trusted, and what to do about it.

`assess()` turns what the dashboard already knows (reconciliation, prices, costs, transaction history, ages) into:
- **issues**: each says what is wrong, which figures it affects, and what to do. "attention" changes a figure;
  "note" changes none. Checks that pass are listed separately.
- **figures**: for every main figure, whether it is fully backed ("ok"), only partly ("partial") or cannot be
  worked out ("unavailable"), with the reasons.
- **sources**: where the data came from and how new it is.

Nothing here changes a figure or modifies a holding. It describes. Wording separates "not verified" from "wrong":
a gap means rows on the statement were not recognised, not that the statement is wrong.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from portfolio.importers.cas import PARSER_VERSION
from portfolio.portfolio_view import PRICE_STALE_AFTER_DAYS, _jsonable
from portfolio.store import Store, as_utc

FIGURES = {
    "total": "Total value",
    "allocation": "Allocation and concentration",
    "latest": "Value at latest price",
    "fund_gain": "Mutual fund gain",
    "stock_gain": "Stock gain or loss",
    "xirr": "Mutual fund return (XIRR)",
    "changes": "Change since the previous statement",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _issue(id_: str, severity: str, title: str, affects: list[str], action: str | None, detail: str | None = None,
           count: int | None = None) -> dict[str, Any]:
    return {"id": id_, "severity": severity, "title": title, "detail": detail, "affects": [FIGURES[a] for a in affects],
            "affect_ids": affects, "action": action, "count": count}


def _passed(id_: str, title: str) -> dict[str, Any]:
    return {"id": id_, "severity": "ok", "title": title, "detail": None, "affects": [], "affect_ids": [], "action": None, "count": None}


def _issues(view: dict[str, Any], extras: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []
    c = view["completeness"]
    stale_prices = sum(1 for h in (*view["stocks"], *view["funds"]) if h["latest"] and h["latest"]["stale"])

    d = c["demat"]
    if d is not None:
        if not d["available"]:
            issues.append(_issue(
                "demat_unverified", "attention", "This demat statement was not checked against its printed totals.",
                ["total", "allocation"], "Upload the same PDF again on the Imports page. It will be read with the current version and each account checked.",
                "It was imported before the check was stored. The figures may be right; they are not verified."))
        elif d["all_match"]:
            passed.append(_passed("demat_match", f"All {_plural(len(d['accounts']), 'demat account')} add up to the totals printed on the statement."))
        else:
            bad = [a for a in d["accounts"] if not a["ok"]]
            issues.append(_issue(
                "demat_gap", "attention", f"{len(bad)} of {_plural(len(d['accounts']), 'demat account')} do not add up to the printed total.",
                ["total", "allocation", "changes"],
                "Open the statement's details on the Imports page to see which account. Some rows were not recognised, so values may be too low. "
                "Run the command-line importer with --diagnose on the PDF to list them.",
                "; ".join(f"{a['label']} {a['client'] or ''}: printed {a['statement_total']}, read {a['imported_value']}".replace("  ", " ")
                          for a in bad), count=len(bad)))
        f = d.get("folios") if d["available"] else None
        if f is not None:
            if f["ok"]:
                passed.append(_passed("folios_match", "Mutual fund folios add up to the printed total."))
            else:
                issues.append(_issue(
                    "folios_gap", "attention", "The mutual fund folios section does not add up to its printed total.",
                    ["total", "allocation", "fund_gain", "changes"],
                    "Some fund rows were not recognised. Open the statement's details on the Imports page, and re-upload or run --diagnose.",
                    f"printed {f['statement_total']}, read {f['imported_value']}"))

    s = c["stocks"]
    if s and s["without_symbol"]:
        issues.append(_issue("no_symbol", "note", f"{_plural(s['without_symbol'], 'stock')} of {s['count']} "
                             f"{'has' if s['without_symbol'] == 1 else 'have'} no NSE symbol.", [],
                             None, "They are shown by name. Latest prices are matched by ISIN, so a missing symbol changes no figure."))

    h = c["holdings"]
    if h:
        if h["without_price"] == 0:
            passed.append(_passed("demat_prices", "Every demat holding has a latest price."))
        else:
            issues.append(_issue(
                "demat_no_price", "attention",
                "No latest prices for demat holdings yet." if h["priced"] == 0 else f"{h['without_price']} of {h['count']} demat holdings have no price newer than the statement.",
                ["latest"], "Use Refresh prices on the Overview. Unlisted or delisted securities have none: they show a dash and are never estimated.",
                "Their statement values are unchanged.", count=h["without_price"]))

    fd = c["funds"]
    if fd:
        n = fd["count"]
        if fd["without_cost"] == 0:
            passed.append(_passed("fund_cost", f"All {_plural(n, 'fund')} have a purchase cost."))
        else:
            issues.append(_issue(
                "fund_no_cost", "attention", f"{fd['without_cost']} of {_plural(n, 'fund')} {'has' if fd['without_cost'] == 1 else 'have'} no purchase cost.",
                ["fund_gain"], "Upload a detailed CAMS or KFintech statement; it prints the amount invested.",
                "No gain is shown for these funds and they are left out of the gain total.", count=fd["without_cost"]))
        if fd["without_price"] == 0:
            passed.append(_passed("fund_prices", "Every fund has a latest NAV."))
        else:
            issues.append(_issue(
                "fund_no_price", "attention",
                "No latest NAVs yet." if fd["priced"] == 0 else f"{fd['without_price']} of {_plural(n, 'fund')} {'has' if fd['without_price'] == 1 else 'have'} no NAV newer than the statement.",
                ["latest"], "Use Refresh prices on the Overview. A fund AMFI does not list stays at its statement value and shows a dash.",
                count=fd["without_price"]))
        if fd["unknown_amc"]:
            issues.append(_issue("unknown_amc", "attention", f"{_plural(fd['unknown_amc'], 'fund')} {'has' if fd['unknown_amc'] == 1 else 'have'} no fund house name.",
                                 ["allocation"], "The fund house comes from the ISIN. If this stays, the allocation by fund house shows them as Unknown.",
                                 count=fd["unknown_amc"]))
        hist = fd["history"]
        if hist["unknown"]:
            issues.append(_issue("history_unknown", "attention", f"No transaction history for {hist['unknown']} of {_plural(n, 'fund')}.",
                                 ["xirr"], "Upload a detailed CAS from CAMS or KFintech. Only those list transactions.", count=hist["unknown"]))
        if hist["incomplete"]:
            issues.append(_issue("history_incomplete", "attention",
                                 f"{_plural(hist['incomplete'], 'fund')} {'was' if hist['incomplete'] == 1 else 'were'} held before the first statement began, so the history is partial.",
                                 ["xirr"], "Request a CAMS or KFintech statement covering the full period.", count=hist["incomplete"]))
        if not hist["unknown"] and not hist["incomplete"]:
            passed.append(_passed("history", "Transaction history covers the whole holding for every fund."))

    for kind, st in (("demat", view["statements"]["demat"]), ("mf", view["statements"]["mf"])):
        if st and st["stale"]:
            label = "Stocks and ETFs" if kind == "demat" else "Mutual funds"
            issues.append(_issue(f"stale_{kind}", "attention", f"{label}: the statement is {_plural(st['age_days'], 'day')} old.",
                                 ["total", "allocation"], "Upload a newer statement. The values are as of the statement date, not today.",
                                 f"Dated {st['period_to']}."))
    a = view["as_of"]
    if a and a["mixed"]:
        issues.append(_issue("mixed_dates", "attention", f"The total adds statements {a['spread_days']} days apart.",
                             ["total", "allocation"], "Upload a newer statement for the older part, so both are from about the same day.",
                             f"{_d(a['from'])} and {_d(a['to'])}."))
    if stale_prices:
        issues.append(_issue("old_prices", "attention", f"{_plural(stale_prices, 'latest price')} {'is' if stale_prices == 1 else 'are'} more than {PRICE_STALE_AFTER_DAYS} days old.",
                             ["latest"], "Use Refresh prices. Exchanges publish end-of-day files on trading days only.", count=stale_prices))
    if extras["outdated"]:
        issues.append(_issue("outdated_parser", "attention", f"{_plural(extras['outdated'], 'statement')} {'was' if extras['outdated'] == 1 else 'were'} read by an older version of this app.",
                             ["total", "allocation"], "Upload the same PDF again on the Imports page; a newer version may read more from it.", count=extras["outdated"]))
    return issues, passed


def _d(value: Any) -> str:
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _figures(view: dict[str, Any], issues: list[dict[str, Any]], extras: dict[str, Any]) -> list[dict[str, Any]]:
    c = view["completeness"]
    attention = [i for i in issues if i["severity"] == "attention"]

    def hit(figure: str) -> list[str]:
        return [i["title"] for i in attention if figure in i["affect_ids"]]

    def fig(id_: str, status: str, basis: str, caveats: list[str]) -> dict[str, Any]:
        return {"id": id_, "figure": FIGURES[id_], "status": status, "basis": basis, "caveats": caveats}

    out: list[dict[str, Any]] = []
    a = view["as_of"]
    if a is None:
        return [fig(i, "unavailable", "No statement has been imported.", []) for i in ("total", "allocation")]
    when = _d(a["to"]) if a["from"] == a["to"] else f"{_d(a['from'])} to {_d(a['to'])}"
    for id_ in ("total", "allocation"):
        caveats = hit(id_)
        out.append(fig(id_, "partial" if caveats else "ok", f"Statement values as of {when}.", caveats))

    latest_caveats = hit("latest")
    counted = [x for x in (c["holdings"], c["funds"]) if x]
    priced = sum(x["priced"] for x in counted)
    total = sum(x["count"] for x in counted)
    if total and priced == 0:
        out.append(fig("latest", "unavailable", "No latest prices have been fetched.", latest_caveats))
    else:
        out.append(fig("latest", "partial" if latest_caveats else "ok",
                       f"AMFI NAV and NSE or BSE closing prices for {priced} of {total} holdings, shown beside statement values.", latest_caveats))

    fd = c["funds"]
    if fd:
        if fd["with_cost"] == 0:
            out.append(fig("fund_gain", "unavailable", "No fund has a purchase cost on its statement.", hit("fund_gain")))
        else:
            out.append(fig("fund_gain", "partial" if fd["without_cost"] else "ok",
                           f"Statement value minus amount invested, for {fd['with_cost']} of {_plural(fd['count'], 'fund')}.", hit("fund_gain")))
        done = fd["history"]["complete"]
        out.append(fig("xirr", "unavailable" if done == 0 else "partial" if done < fd["count"] else "ok",
                       f"Needs a complete transaction history. {done} of {_plural(fd['count'], 'fund')} have one; the working-out has further conditions "
                       "(units must add up, every transaction kind must be understood).", hit("xirr")))
    if c["stocks"] is not None:
        out.append(fig("stock_gain", "unavailable", "NSDL and CDSL statements do not print what you paid for a holding.",
                       ["A gain needs purchase cost, which these statements do not carry."]))
    if extras["statement_dates"] < 2:
        out.append(fig("changes", "unavailable", "Needs statements from at least two dates.", []))
    else:
        caveats = hit("changes")
        out.append(fig("changes", "partial" if caveats else "ok",
                       f"Compares two statement dates, from {extras['statement_dates']} available.", caveats))
    return out


def _sources(view: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    prices = []
    for p in extras["price_sources"]:
        prices.append({"source": p["source"], "count": p["count"], "newest": p["newest"], "oldest": p["oldest"],
                       "fetched_at": as_utc(p["fetched_at"]).isoformat() if p["fetched_at"] else None})
    return {"statements": view["statements"], "prices": prices, "last_refresh": extras["last_refresh"],
            "last_refresh_failed": extras["last_failure"]}


def assess(view: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    """The pure part: `view` is `compute_view` output, `extras` holds what the view does not carry
    (outdated, statement_dates, price_sources, last_refresh, last_failure)."""
    if view["as_of"] is None:
        return {"empty": True, "issues": [], "passed": [], "figures": _figures(view, [], extras), "sources": _sources(view, extras)}
    issues, passed = _issues(view, extras)
    order = {"attention": 0, "note": 1}
    issues.sort(key=lambda i: order[i["severity"]])
    return {"empty": False, "issues": issues, "passed": passed, "figures": _figures(view, issues, extras), "sources": _sources(view, extras)}


def data_confidence(store: Store, view: dict[str, Any]) -> dict[str, Any]:
    refreshed, failed = store.last_audit("prices_refreshed"), store.last_audit("prices_failed")
    extras = {
        "outdated": sum(1 for s in store.list_statements() if s["parser_version"] < PARSER_VERSION),
        "statement_dates": len(store.statement_dates()),
        "price_sources": store.price_sources(),
        "last_refresh": as_utc(refreshed["ts"]).isoformat() if refreshed else None,
        "last_failure": as_utc(failed["ts"]).isoformat() if failed else None,
    }
    return _jsonable(assess(view, extras))
