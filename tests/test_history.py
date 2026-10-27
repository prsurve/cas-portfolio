"""Value over time: one point per statement date, each built like the live dashboard."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portfolio.config import Settings
from portfolio.history import value_history
from portfolio.importers import cas
from portfolio.portfolio_view import build_view
from portfolio.store import Store

TODAY = date(2026, 10, 27)


@pytest.fixture
def store(tmp_path):
    return Store.from_url(f"sqlite:///{tmp_path}/hist.db")


def _end(data, end):
    data.statement_period = data.statement_period.model_copy(update={"to": end})
    return data


def test_latest_statement_on_or_before(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "sep")
    cas.save(store, cas.from_demat_data(_end(demat_data, "31-Oct-2026")), "oct")
    assert store.latest_statement("demat")["period_to"] == date(2026, 10, 31)
    assert store.latest_statement("demat", on_or_before=date(2026, 10, 15))["period_to"] == date(2026, 9, 30)
    assert store.latest_statement("demat", on_or_before=date(2026, 9, 1)) is None


def test_build_view_as_of_uses_the_statements_then_in_force(store, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "sep")
    sep_value = build_view(store, today=TODAY)["totals"]["demat_value"]
    demat_data.accounts = demat_data.accounts[:1]
    cas.save(store, cas.from_demat_data(_end(demat_data, "31-Oct-2026")), "oct")
    assert build_view(store, today=TODAY)["totals"]["demat_value"] != sep_value
    old = build_view(store, today=TODAY, as_of=date(2026, 10, 15))
    assert old["totals"]["demat_value"] == sep_value and old["statements"]["demat"]["period_to"] == "2026-09-30"


def test_empty_history(store):
    assert value_history(store) == []


def test_history_has_one_point_per_statement_date_and_never_double_counts(store, mf_data, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat-sep")   # stocks + folios worth 5000, to 30 Sep
    cas.save(store, cas.from_mf_data(mf_data), "cams-sep")          # funds worth 18840, to 30 Sep
    sep = build_view(store, today=TODAY)["totals"]
    demat_data.accounts = [demat_data.accounts[0], demat_data.accounts[-1]]  # one demat account + the folios
    cas.save(store, cas.from_demat_data(_end(demat_data, "31-Oct-2026")), "demat-oct")

    points = value_history(store)
    assert [p["date"] for p in points] == ["2026-09-30", "2026-10-31"]
    first, second = points
    # 30 Sep: stocks from the demat statement; funds from CAMS (wins the tie), never CAMS + folios.
    assert first["value"] == sep["demat_value"] + 18840 and first["mf_value"] == 18840
    assert first["mf_source"] == "CAMS" and first["mixed"] is False
    # 31 Oct: the newer demat statement supplies its own folios (5000); CAMS is not added.
    assert second["mf_source"] == "CDSL" and second["mf_value"] == 5000
    assert second["value"] == second["demat_value"] + 5000


def test_history_flags_points_that_mix_dates(store, mf_data, demat_data):
    cas.save(store, cas.from_demat_data(demat_data), "demat-sep")
    cas.save(store, cas.from_mf_data(_end(mf_data, "25-Oct-2026")), "cams-oct")
    sep, octo = value_history(store)
    assert sep["mixed"] is False and octo["mixed"] is True and octo["spread_days"] == 25


def test_history_endpoint(tmp_path, mf_data):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.db", _env_file=None)
    store = Store.from_url(settings.database_url)
    import portfolio.app as app_module
    client = TestClient(app_module.create_app(settings, store))
    assert client.get("/api/history").json() == {"points": []}
    cas.save(store, cas.from_mf_data(mf_data), "m")
    [p] = client.get("/api/history").json()["points"]
    assert p["date"] == "2026-09-30" and p["value"] == 18840


def test_older_fund_source_makes_the_point_mixed(store, mf_data, demat_data):
    """A newer demat statement without a folio section keeps CAMS (older) as the fund source: flagged, not summed."""
    cas.save(store, cas.from_mf_data(mf_data), "cams-sep")
    demat_data.accounts = demat_data.accounts[:1]  # no folio section
    cas.save(store, cas.from_demat_data(_end(demat_data, "31-Oct-2026")), "demat-oct")
    last = value_history(store)[-1]
    assert last["mf_source"] == "CAMS" and last["mf_value"] == 18840 and last["mixed"] is True
