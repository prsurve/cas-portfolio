from datetime import date

from fastapi.testclient import TestClient

from portfolio.app import create_app
from portfolio.config import Settings

H = {"X-Requested-With": "portfolio"}


def test_demo_serves_a_made_up_portfolio_and_leaves_the_real_database_alone(tmp_path):
    real = tmp_path / "real.db"
    settings = Settings(database_url=f"sqlite:///{real}", backup_dir=str(tmp_path / "bk"), demo=True, fetch_prices=True)
    client = TestClient(create_app(settings))
    assert client.get("/api/mode").json() == {"demo": True}
    view = client.get("/api/portfolio").json()
    assert view["totals"]["value"] > 0 and view["statements"]["demat"] and view["statements"]["mf"]
    assert not real.exists(), "the real database was created or touched"
    assert not (tmp_path / "bk").exists()
    # funds have a full SIP history, so a return can be worked out
    returns = client.get("/api/returns").json()
    assert returns["portfolio"]["status"] == "ok"
    # deleting everything in the demo cannot reach the real files either
    assert client.request("DELETE", "/api/data", json={"confirm": "delete all data"}, headers=H).status_code == 200
    assert not real.exists() and not (tmp_path / "bk").exists()


def test_demo_statements_carry_no_personal_details():
    from portfolio.demo import demo_statements
    text = repr(demo_statements(date(2027, 1, 10)))
    assert "demo@example.com" not in text and "DEMO USER" not in text
    assert "ABCDE1234F" not in text


def test_normal_mode_is_not_demo(tmp_path):
    client = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path}/n.db", backup_dir=str(tmp_path / "bk"))))
    assert client.get("/api/mode").json() == {"demo": False}
