"""End-of-day prices for stocks, ETFs and bonds from the exchanges' public bhavcopy files."""

from __future__ import annotations

import io
import urllib.error
import zipfile
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from portfolio import bhavcopy
from portfolio.bhavcopy import parse_bhavcopy, refresh_exchange
from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.prices import PriceFetchError, refresh_nav
from portfolio.refresh import refresh_all
from portfolio.store import Store, prices as prices_table

TODAY = date(2026, 10, 27)
HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
          "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric")


def bhav(*rows: tuple[str, str, str], day: str = "2026-10-26") -> str:
    """A made-up file in the exchanges' layout. Each row: (ISIN, symbol, close)."""
    lines = [HEADER]
    for isin, symbol, close in rows:
        lines.append(f"{day},{day},CM,NSE,STK,1,{isin},{symbol},EQ,,,,,{symbol} LTD,1,1,1,{close},{close},1,,{close}")
    return "\n".join(lines) + "\n"


NSE_FILE = bhav(("INE009A01021", "INFY", "1500.00"), ("INE467B01029", "TCS", "3500.00"),
                ("INF000000001", "LIQUIDETF", "110.00"))
BSE_FILE = bhav(("INE040A01034", "HDFCBANK", "1750.00"), ("INE002A01018", "RELIANCE", "3000.00"))


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/ex.db")


# ---- parsing -----------------------------------------------------------------------


def test_parse_reads_close_and_date():
    t = parse_bhavcopy(NSE_FILE)
    assert t.navs["INE009A01021"].price == D("1500.00") and t.navs["INE009A01021"].date == date(2026, 10, 26)
    assert len(t.navs) == 3 and t.rows == 3 and t.skipped == 0


def test_parse_skips_unusable_rows_and_handles_repeats():
    text = bhav(("INE1", "A", "10"), ("INE111111111", "ZERO", "0"), ("INE222222222", "EMPTY", ""),
                ("INE333333333", "DUP", "5"), ("INE333333333", "DUP", "5"),        # identical repeat: fine
                ("INE444444444", "CLASH", "5"), ("INE444444444", "CLASH", "6"))     # conflicting: dropped
    text += "2026-13-45,2026-10-26,CM,NSE,STK,1,INE555555555,BAD,EQ,,,,,B,1,1,1,9,9,1,,9\n"  # unreadable date
    t = parse_bhavcopy(text)
    assert set(t.navs) == {"INE1", "INE333333333"} and t.ambiguous == 1
    assert t.skipped == 3  # zero close, empty close, bad date


def test_a_page_that_is_not_the_file_is_rejected():
    with pytest.raises(PriceFetchError, match="expected format"):
        parse_bhavcopy("<html><body>Page not found</body></html>")


# ---- fetching ----------------------------------------------------------------------


def _zip(text: str, name: str = "file.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, text)
    return buf.getvalue()


class Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "msg", {}, None)


def test_request_is_a_plain_get_of_a_dated_public_url():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_method(), request.data, dict(request.header_items())))
        return Resp(_zip(NSE_FILE))

    day = bhavcopy.fetch_day("NSE", date(2026, 10, 26), opener=opener)
    [(url, method, data, headers)] = seen
    assert url == ("https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261026_F_0000.csv.zip")
    assert method == "GET" and data is None and set(headers) <= {"User-agent"} and "?" not in url
    assert day == NSE_FILE


def test_bse_url():
    seen = []
    bhavcopy.fetch_day("BSE", date(2026, 10, 26), opener=lambda r, timeout: seen.append(r.full_url) or Resp(BSE_FILE.encode()))
    assert seen == ["https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261026_F_0000.CSV"]


def test_a_day_without_a_file_is_not_an_error():
    def missing(request, timeout):
        raise http_error(404)

    assert bhavcopy.fetch_day("NSE", date(2026, 10, 25), opener=missing) is None
    # BSE answers 200 with a web page for a day it has no file for
    assert bhavcopy.fetch_day("BSE", date(2026, 10, 25), opener=lambda r, timeout: Resp(b"<html>Not found</html>")) is None


def test_a_refusal_is_reported_not_worked_around():
    calls = []

    def refused(request, timeout):
        calls.append(request)
        raise http_error(403)

    with pytest.raises(PriceFetchError, match="refused"):
        bhavcopy.fetch_day("NSE", date(2026, 10, 26), opener=refused)
    with pytest.raises(PriceFetchError, match="Could not download"):
        bhavcopy.fetch_day("NSE", date(2026, 10, 26), opener=lambda r, timeout: (_ for _ in ()).throw(OSError("down")))
    assert len(calls) == 1  # one plain request: no retry with other headers


def test_zip_bombs_and_oversized_files_are_refused(monkeypatch):
    monkeypatch.setattr(bhavcopy, "MAX_BYTES", 1000)
    big = _zip("x" * 5000)
    with pytest.raises(PriceFetchError, match="too large"):
        bhavcopy.fetch_day("NSE", date(2026, 10, 26), opener=lambda r, timeout: Resp(big))
    with pytest.raises(PriceFetchError, match="too large"):
        bhavcopy.fetch_day("BSE", date(2026, 10, 26), opener=lambda r, timeout: Resp(b"y" * 2000))


def test_latest_file_walks_back_over_weekends_and_holidays():
    have = {date(2026, 10, 23)}  # nothing published for the 24th to 27th

    def opener(request, timeout):
        stamp = request.full_url.split("_")[-3]
        if date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:])) in have:
            return Resp(_zip(NSE_FILE))
        raise http_error(404)

    day, text = bhavcopy.fetch_latest("NSE", TODAY, opener=opener)
    assert day == date(2026, 10, 23) and text == NSE_FILE
    with pytest.raises(PriceFetchError, match="last 7 days"):
        bhavcopy.fetch_latest("NSE", date(2026, 11, 20), opener=opener)


# ---- storing -----------------------------------------------------------------------


def _demat(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "d")


def files(nse=NSE_FILE, bse=BSE_FILE, log=None):
    def fetch(exchange):
        if log is not None:
            log.append(exchange)
        text = {"NSE": nse, "BSE": bse}[exchange]
        if isinstance(text, Exception):
            raise text
        return date(2026, 10, 26), text
    return fetch


def test_nse_first_and_bse_only_for_what_nse_lacks(store, demat_data):
    _demat(store, demat_data)  # held: INFY, TCS, HDFC Bank, Reliance, the ETF
    log = []
    r = refresh_exchange(store, fetch=files(log=log))
    assert log == ["NSE", "BSE"]
    assert (r["held"], r["priced"], r["unpriced"]) == (5, 5, 0) and r["sources"] == {"NSE": 3, "BSE": 2}
    with store.engine.connect() as conn:
        rows = {(x.isin, x.source): x.price for x in conn.execute(select(prices_table))}
    assert rows[("INE009A01021", "NSE")] == D("1500.00") and rows[("INE040A01034", "BSE")] == D("1750.00")
    assert ("INE040A01034", "NSE") not in rows


def test_bse_is_not_downloaded_when_nse_has_everything(store, demat_data):
    _demat(store, demat_data)
    log = []
    refresh_exchange(store, fetch=files(nse=bhav(*[(i, "S", "10") for i in (
        "INE009A01021", "INE467B01029", "INE040A01034", "INE002A01018", "INF000000001")]), log=log))
    assert log == ["NSE"]


def test_bse_trouble_keeps_what_nse_gave(store, demat_data):
    _demat(store, demat_data)
    r = refresh_exchange(store, fetch=files(bse=PriceFetchError("BSE refused the request (HTTP 403).")))
    assert r["priced"] == 3 and r["sources"] == {"NSE": 3} and "BSE refused" in " ".join(r["warnings"])


def test_nse_failure_changes_nothing(store, demat_data):
    _demat(store, demat_data)
    refresh_exchange(store, fetch=files())
    with pytest.raises(PriceFetchError):
        refresh_exchange(store, fetch=files(nse=PriceFetchError("offline")))
    with store.engine.connect() as conn:
        assert len(conn.execute(select(prices_table)).all()) == 5


def test_nothing_held_downloads_nothing(store):
    log = []
    r = refresh_exchange(store, fetch=files(log=log))
    assert log == [] and r["held"] == 0


def test_demat_funds_are_also_priced_by_amfi_nav(store, demat_data):
    _demat(store, demat_data)  # the ETF and two folio funds are INF ISINs
    nav = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;"
           "Date\r\n1;INF000000001;-;Liquid;;;100.5;26-Oct-2026\r\n")
    r = refresh_nav(store, fetch=lambda: nav)
    assert r["held"] == 3 and r["priced"] == 1  # folios and the demat fund unit, not the equities


# ---- valuation ---------------------------------------------------------------------


def _priced_view(store, demat_data, nse=NSE_FILE, bse=BSE_FILE, nav=None):
    _demat(store, demat_data)
    refresh_exchange(store, fetch=files(nse=nse, bse=bse))
    if nav:
        refresh_nav(store, fetch=lambda: nav)
    return build_view(store, today=TODAY)


def test_stocks_at_the_latest_close(store, demat_data):
    v = _priced_view(store, demat_data)
    infy = next(s for s in v["stocks"] if s["isin"] == "INE009A01021")
    assert infy["value"] == 29000 and infy["quantity"] == 20  # statement value untouched, merged across accounts
    assert infy["latest"] == {"price": 1500, "price_date": "2026-10-26", "source": "NSE", "value": 30000,
                              "change": 1000, "change_pct": pytest.approx(3.4483, abs=1e-3),
                              "days_old": 1, "stale": False}
    hdfc = next(s for s in v["stocks"] if s["isin"] == "INE040A01034")
    assert hdfc["latest"]["source"] == "BSE" and hdfc["latest"]["value"] == 35000
    t = v["totals"]["demat_latest"]
    assert (t["priced"], t["total"], t["as_of"]) == (5, 5, "2026-10-26")
    assert t["value"] - t["statement_value"] == t["change"]
    assert v["totals"]["value"] == 94700  # the statement-based total is never mixed with latest prices


def test_missing_and_old_prices_show_nothing(store, demat_data):
    old = bhav(("INE009A01021", "INFY", "1500"), day="2026-09-15")  # older than the 30 Sep statement
    v = _priced_view(store, demat_data, nse=old, bse=bhav(("INE467B01029", "TCS", "3500")))
    by = {s["isin"]: s for s in v["stocks"]}
    assert by["INE009A01021"]["latest"] is None          # older than the statement: not used
    assert by["INE002A01018"]["latest"] is None          # nobody lists it: shown as missing, not estimated
    assert by["INE467B01029"]["latest"]["source"] == "BSE"
    assert v["totals"]["demat_latest"]["priced"] == 1 and v["completeness"]["holdings"] == {
        "count": 5, "priced": 1, "without_price": 4}


def test_etf_prefers_the_exchange_and_demat_fund_units_fall_back_to_nav(store, demat_data):
    nav = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;"
           "Date\r\n1;INF000000001;-;Liquid;;;99.0;26-Oct-2026\r\n")
    etf = lambda v: next(s for s in v["stocks"] if s["isin"] == "INF000000001")["latest"]  # noqa: E731
    both = _priced_view(store, demat_data, nav=nav)
    assert etf(both)["source"] == "NSE" and etf(both)["price"] == 110  # an ETF trades at its market price
    store2 = Store.from_url("sqlite://")
    only_nav = _priced_view(store2, demat_data, nse=bhav(("INE009A01021", "INFY", "1500")), bse=bhav(), nav=nav)
    assert etf(only_nav)["source"] == "AMFI" and etf(only_nav)["value"] == 990  # 10 units at NAV 99


def test_a_nav_is_never_used_for_a_share(store, demat_data):
    store.save_prices("AMFI", {"INE009A01021": (D(1), date(2026, 10, 26))})
    _demat(store, demat_data)
    infy = next(s for s in build_view(store, today=TODAY)["stocks"] if s["isin"] == "INE009A01021")
    assert infy["latest"] is None


def test_history_is_never_repriced(store, demat_data):
    _priced_view(store, demat_data)
    v = build_view(store, today=TODAY, as_of=date(2026, 9, 30))
    assert v["totals"]["demat_latest"] is None and all(s["latest"] is None for s in v["stocks"])


# ---- refreshing everything ---------------------------------------------------------


def test_each_source_can_fail_on_its_own(store, demat_data):
    _demat(store, demat_data)
    nav_down = lambda: (_ for _ in ()).throw(PriceFetchError("AMFI is down."))  # noqa: E731
    r = refresh_all(store, fetch_nav=nav_down, fetch_exchange=files())
    assert r["nav"] is None and r["exchange"]["priced"] == 5 and r["errors"] == {"nav": "AMFI is down."}


def test_all_sources_failing_is_an_error(store, demat_data):
    _demat(store, demat_data)
    down = lambda *a: (_ for _ in ()).throw(PriceFetchError("down"))  # noqa: E731
    with pytest.raises(PriceFetchError):
        refresh_all(store, fetch_nav=down, fetch_exchange=down)


# ---- the API -----------------------------------------------------------------------


def test_refresh_endpoint_runs_both_sources(tmp_path, demat_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.db", _env_file=None)
    store = Store.from_url(settings.database_url)
    cas.save(store, cas.from_demat_data(demat_data), "d")
    import portfolio.app as app_module
    client = TestClient(app_module.create_app(settings, store, nav_fetcher=lambda: "x", exchange_fetcher=files()))
    r = client.post("/api/prices/refresh", headers={"X-Requested-With": "portfolio"})
    assert r.status_code == 200 and r.json()["exchange"]["priced"] == 5
    assert r.json()["errors"].keys() == {"nav"}  # the stub NAV file is not a real one
    infy = next(s for s in client.get("/api/portfolio").json()["stocks"] if s["isin"] == "INE009A01021")
    assert infy["latest"]["value"] == 30000
