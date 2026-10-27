"""Latest mutual fund NAVs from AMFI's public file: parsing, storage and valuation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from portfolio import prices
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.prices import PriceFetchError, parse_navall, refresh_nav
from portfolio.store import Store, prices as prices_table

TODAY = date(2026, 10, 27)
HEADER = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;"
          "Net Asset Value;Date")


def navall(*rows: str) -> str:
    """A made-up file in AMFI's layout: header, blank lines, headings and CRLF line endings."""
    body = ["Open Ended Schemes(Equity Scheme - Flexi Cap Fund)", "", "Some Mutual Fund", "", *rows]
    return "\r\n".join([HEADER, "", *body, ""])


SAMPLE = navall(
    "100001;INF879O01027;-;Flexi Cap Fund;Direct Plan;Growth Option;90.0000;26-Oct-2026",
    "100002;-;INF846K01WO1;Reinvest Only Fund;Direct Plan;IDCW Option;12.5000;26-Oct-2026",
    "100003;INF000000001 ;-;Trailing Space Fund;Direct Plan;Growth Option;7.0000;26-Oct-2026",
    "100004;INF111111111;Redeemed;Not Numeric Fund;Direct Plan;Growth Option;N.A.;26-Oct-2026",
    "100005;-;-;No Isin Fund;Direct Plan;Growth Option;5.0000;26-Oct-2026",
    "100006;INF222222222;-;Zero Nav Fund;Direct Plan;Growth Option;0.0000;26-Oct-2026",
    "100007;INF333333333;-;Bad Date Fund;Direct Plan;Growth Option;5.0000;31-Foo-2026",
    "short;line",
)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/prices.db")


# ---- parsing -----------------------------------------------------------------------


def test_parse_reads_either_isin_column_and_strips_spaces():
    t = parse_navall(SAMPLE)
    assert t.navs["INF879O01027"].price == D("90.0000") and t.navs["INF879O01027"].date == date(2026, 10, 26)
    assert t.navs["INF846K01WO1"].price == D("12.5")  # ISIN only in the reinvestment column
    assert "INF000000001" in t.navs                  # trailing space stripped


def test_parse_skips_what_it_cannot_trust_and_says_how_many():
    t = parse_navall(SAMPLE)
    for isin in ("INF111111111", "INF222222222", "INF333333333"):  # N.A. NAV, zero NAV, unreadable date
        assert isin not in t.navs
    assert t.skipped == 4 and t.rows == 7  # three bad rows + the short line; the no-ISIN row is not an error


def test_parse_nothing_to_read():
    assert parse_navall("").navs == {}
    with pytest.raises(PriceFetchError):
        parse_navall("<html>Service unavailable</html>", strict=True)  # not the NAV file at all


def test_repeated_isin_keeps_the_latest_date_and_drops_true_conflicts():
    t = parse_navall(navall(
        "1;INF204KB1XN0;-;Old Series;;;10.0000;10-May-2021",
        "2;INF204KB1XN0;-;Newer Series;;;13.5859;26-May-2022",   # later date wins
        "3;INF204KB1304;-;Twin A;;;10.0000;15-Jul-2019",
        "4;INF204KB1304;-;Twin B;;;10.0000;15-Jul-2019",         # identical: fine
        "5;INF204KA1UU2;-;Clash A;;;12.9698;28-Sep-2017",
        "6;INF204KA1UU2;-;Clash B;;;12.9193;28-Sep-2017",        # same date, different NAV: ambiguous
    ))
    assert t.navs["INF204KB1XN0"].price == D("13.5859")
    assert t.navs["INF204KB1304"].price == D(10)
    assert "INF204KA1UU2" not in t.navs and t.ambiguous == 1


# ---- fetching ----------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_asks_for_one_fixed_public_url_and_sends_nothing_else():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_method(), request.data, dict(request.header_items())))
        return FakeResponse(SAMPLE.encode())

    assert prices.fetch_navall(opener=opener) == SAMPLE
    [(url, method, data, headers)] = seen
    assert url == prices.AMFI_URL and url.startswith("https://") and "?" not in url
    assert method == "GET" and data is None
    assert set(headers) <= {"User-agent"}  # no cookies, tokens or custom headers


def test_fetch_failures_become_one_clear_error():
    def refuses(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(PriceFetchError, match="Could not download"):
        prices.fetch_navall(opener=refuses)
    with pytest.raises(PriceFetchError, match="too large"):
        prices.fetch_navall(opener=lambda r, timeout: FakeResponse(b"x" * (prices.MAX_BYTES + 10)))


# ---- storing -----------------------------------------------------------------------


def _funds(store, mf_data):
    cas.save(store, cas.from_mf_data(mf_data), "cams")


def test_refresh_stores_only_the_isins_you_hold(store, mf_data):
    _funds(store, mf_data)  # holds INF879O01027 and INF179K01VQ6
    report = refresh_nav(store, fetch=lambda: SAMPLE)
    assert report["held"] == 2 and report["priced"] == 1 and report["unpriced"] == 1
    assert report["latest_date"] == "2026-10-26"
    with store.engine.connect() as conn:
        assert [r.isin for r in conn.execute(select(prices_table))] == ["INF879O01027"]  # not the whole file


def test_refresh_updates_in_place_and_a_failed_fetch_changes_nothing(store, mf_data):
    _funds(store, mf_data)
    refresh_nav(store, fetch=lambda: SAMPLE)
    newer = SAMPLE.replace("90.0000;26-Oct-2026", "91.5000;27-Oct-2026")
    refresh_nav(store, fetch=lambda: newer)
    with store.engine.connect() as conn:
        [row] = conn.execute(select(prices_table)).all()
    assert (row.price, row.price_date, row.source) == (D("91.5"), date(2026, 10, 27), "AMFI")

    def broken():
        raise PriceFetchError("offline")

    with pytest.raises(PriceFetchError):
        refresh_nav(store, fetch=broken)
    with store.engine.connect() as conn:
        assert conn.execute(select(prices_table.c.price)).scalar() == D("91.5")


def test_refresh_with_nothing_held_fetches_nothing(store):
    calls = []
    report = refresh_nav(store, fetch=lambda: calls.append(1) or SAMPLE)
    assert calls == [] and report["held"] == 0


# ---- valuation ---------------------------------------------------------------------


def _priced(store, mf_data, nav_row):
    _funds(store, mf_data)
    refresh_nav(store, fetch=lambda: navall(nav_row))
    return build_view(store, today=TODAY)


def test_latest_value_next_to_statement_value(store, mf_data):
    v = _priced(store, mf_data, "1;INF879O01027;-;Flexi Cap;;;90.0000;26-Oct-2026")
    flexi = next(f for f in v["funds"] if f["isin"] == "INF879O01027")
    assert flexi["value"] == 8840  # statement value unchanged
    assert flexi["latest"] == {"price": 90, "price_date": "2026-10-26", "source": "AMFI", "value": 9945,
                               "change": 1105, "change_pct": 12.5, "days_old": 1, "stale": False}
    liquid = next(f for f in v["funds"] if f["isin"] == "INF179K01VQ6")
    assert liquid["latest"] is None  # no NAV found: shown as missing, not estimated
    m = v["totals"]["mf_latest"]
    assert (m["value"], m["statement_value"], m["change"]) == (9945, 8840, 1105)
    assert (m["priced"], m["total"], m["as_of"]) == (1, 2, "2026-10-26")
    assert v["totals"]["value"] == 18840  # the statement-based total is never mixed with latest prices


def test_old_nav_is_flagged_stale(store, mf_data):
    v = _priced(store, mf_data, "1;INF879O01027;-;Flexi Cap;;;90.0000;15-Oct-2026")
    assert next(f for f in v["funds"] if f["isin"] == "INF879O01027")["latest"]["stale"] is True


def test_a_nav_older_than_the_statement_is_not_used(store, mf_data):
    v = _priced(store, mf_data, "1;INF879O01027;-;Flexi Cap;;;70.0000;01-Sep-2026")  # statement is 30 Sep
    assert all(f["latest"] is None for f in v["funds"]) and v["totals"]["mf_latest"] is None


def test_no_prices_at_all(store, mf_data):
    _funds(store, mf_data)
    v = build_view(store, today=TODAY)
    assert v["totals"]["mf_latest"] is None and all(f["latest"] is None for f in v["funds"])


def test_historical_views_are_never_repriced(store, mf_data):
    _funds(store, mf_data)
    refresh_nav(store, fetch=lambda: navall("1;INF879O01027;-;Flexi Cap;;;90.0000;26-Oct-2026"))
    v = build_view(store, today=TODAY, as_of=date(2026, 9, 30))
    assert v["totals"]["mf_latest"] is None and all(f["latest"] is None for f in v["funds"])


def test_completeness_counts_funds_without_a_nav(store, mf_data):
    v = _priced(store, mf_data, "1;INF879O01027;-;Flexi Cap;;;90.0000;26-Oct-2026")
    f = v["completeness"]["funds"]
    assert (f["priced"], f["without_price"]) == (1, 1)


# ---- the API -----------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, mf_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.db", _env_file=None)
    store = Store.from_url(settings.database_url)
    cas.save(store, cas.from_mf_data(mf_data), "cams")
    state = {"fetch": lambda: SAMPLE}
    import portfolio.app as app_module
    def no_exchange(exchange):
        raise AssertionError("these tests must never reach the exchanges")

    client = TestClient(app_module.create_app(settings, store, nav_fetcher=lambda: state["fetch"](),
                                              exchange_fetcher=no_exchange))
    return client, store, settings, state


CSRF = {"X-Requested-With": "portfolio"}


def test_refresh_endpoint(api):
    client, store, _, _ = api
    assert client.post("/api/prices/refresh").status_code == 403  # needs the CSRF header
    r = client.post("/api/prices/refresh", headers=CSRF)
    assert r.status_code == 200 and r.json()["nav"]["priced"] == 1 and r.json()["nav"]["latest_date"] == "2026-10-26"
    assert r.json()["errors"] == {}
    flexi = next(f for f in client.get("/api/portfolio").json()["funds"] if f["isin"] == "INF879O01027")
    assert flexi["latest"]["price"] == 90
    from portfolio.store import audit_log
    with store.engine.connect() as conn:
        events = [(e.event, e.detail) for e in conn.execute(select(audit_log))]
    assert ("prices_refreshed", "AMFI priced 1 of 2") in events


def test_refresh_failure_is_a_clear_502_and_keeps_old_prices(api):
    client, _, _, state = api
    client.post("/api/prices/refresh", headers=CSRF)

    def offline():
        raise PriceFetchError("Could not download the AMFI NAV file (OSError).")

    state["fetch"] = offline
    r = client.post("/api/prices/refresh", headers=CSRF)
    assert r.status_code == 502 and "AMFI" in r.json()["detail"]
    assert next(f for f in client.get("/api/portfolio").json()["funds"]
                if f["isin"] == "INF879O01027")["latest"]["price"] == 90


def test_refresh_can_be_switched_off(tmp_path, mf_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/off.db", fetch_prices=False, _env_file=None)
    import portfolio.app as app_module
    client = TestClient(app_module.create_app(settings, Store.from_url(settings.database_url),
                                              nav_fetcher=lambda: SAMPLE))
    assert client.post("/api/prices/refresh", headers=CSRF).status_code == 403
