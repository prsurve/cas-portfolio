"""Refresh every public price source. Each one can fail on its own."""

from __future__ import annotations

from typing import Any, Callable

from portfolio.bhavcopy import refresh_exchange
from portfolio.prices import PriceFetchError, fetch_navall, refresh_nav


def refresh_all(store: Any, fetch_nav: Callable[[], str] | None = None,
                fetch_exchange: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Fund NAVs (AMFI) and stock, ETF and bond closes (NSE, then BSE).

    A source that fails is reported under `errors` while the others still count. It is an
    error only if something failed and nothing at all was priced.
    """
    result: dict[str, Any] = {"nav": None, "exchange": None, "errors": {}}
    for key, run in (("nav", lambda: refresh_nav(store, fetch_nav or fetch_navall)),
                     ("exchange", lambda: refresh_exchange(store, fetch_exchange))):
        try:
            result[key] = run()
        except PriceFetchError as exc:
            result["errors"][key] = str(exc)
    priced = sum(r["priced"] for r in (result["nav"], result["exchange"]) if r)
    if result["errors"] and priced == 0:
        raise PriceFetchError(" ".join(result["errors"].values()))
    return result
