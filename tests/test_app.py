from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio.config import Settings
from portfolio.importers import cas
from portfolio.store import Store

HEADERS = {"X-Requested-With": "portfolio", "Content-Type": "application/pdf", "X-PDF-Password": "My-Secret-PW"}
PDF = b"%PDF-1.7 fake statement bytes"


@pytest.fixture
def env(tmp_path, monkeypatch, mf_data, demat_data):
    db = tmp_path / "app.db"
    settings = Settings(database_url=f"sqlite:///{db}", max_upload_mb=1, _env_file=None)
    store = Store.from_url(settings.database_url)
    current = {"data": mf_data}

    def fake_parse(pdf, password):
        if password != "My-Secret-PW":
            raise cas.CasImportError("Wrong password.")
        data = current["data"]
        return cas.from_mf_data(data) if data is mf_data else cas.from_demat_data(data)

    import portfolio.app as app_module
    monkeypatch.setattr(app_module, "parse_pdf_bytes", fake_parse)
    client = TestClient(app_module.create_app(settings, store))

    def use(kind):
        current["data"] = mf_data if kind == "mf" else demat_data

    return client, store, db, use


def test_upload_both_and_view(env):
    client, _, _, use = env
    r = client.post("/api/statements", content=PDF, headers=HEADERS)
    assert r.status_code == 201
    body = r.json()
    assert (body["issuer"], body["kind"], body["funds"], body["created"]) == ("CAMS", "mf", 3, True)

    use("demat")
    r = client.post("/api/statements", content=PDF + b"other", headers=HEADERS)
    assert r.status_code == 201 and r.json()["stocks"] == 5

    p = client.get("/api/portfolio").json()
    assert p["totals"]["funds_count"] == 2 and p["totals"]["stocks_count"] == 4
    assert [s["kind"] for s in client.get("/api/statements").json()["statements"]] == ["demat", "mf"]


def test_same_file_twice_is_not_duplicated(env):
    client, _, _, _ = env
    assert client.post("/api/statements", content=PDF, headers=HEADERS).status_code == 201
    r = client.post("/api/statements", content=PDF, headers=HEADERS)
    assert r.status_code == 200 and r.json()["created"] is False
    assert len(client.get("/api/statements").json()["statements"]) == 1


def test_password_and_pdf_never_stored(env):
    client, _, db, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    raw = Path(db).read_bytes()
    assert b"My-Secret-PW" not in raw and b"fake statement bytes" not in raw
    assert b"ABCDE1234F" not in raw  # PAN from the statement


@pytest.mark.parametrize("content,headers,status", [
    (b"hello", HEADERS, 400),                                         # not a PDF
    (PDF, {**HEADERS, "X-PDF-Password": ""}, 400),                    # no password
    (PDF, {**HEADERS, "X-PDF-Password": "wrong"}, 422),               # parser rejects
    (b"%PDF" + b"0" * (1024 * 1024 + 1), HEADERS, 413),               # over max_upload_mb
])
def test_upload_rejections(env, content, headers, status):
    client, _, _, _ = env
    r = client.post("/api/statements", content=content, headers=headers)
    assert r.status_code == status and r.json()["detail"]
    assert client.get("/api/statements").json()["statements"] == []


def test_state_changes_require_csrf_header(env):
    client, _, _, _ = env
    headers = {k: v for k, v in HEADERS.items() if k != "X-Requested-With"}
    assert client.post("/api/statements", content=PDF, headers=headers).status_code == 403
    assert client.delete("/api/statements/1").status_code == 403


def test_delete_statement(env):
    client, _, _, _ = env
    sid = client.post("/api/statements", content=PDF, headers=HEADERS).json()["id"]
    assert client.delete(f"/api/statements/{sid}", headers={"X-Requested-With": "portfolio"}).status_code == 200
    assert client.get("/api/portfolio").json()["funds"] == []
    assert client.delete(f"/api/statements/{sid}", headers={"X-Requested-With": "portfolio"}).status_code == 404


def test_page_and_security_headers(env):
    client, _, _, _ = env
    r = client.get("/")
    assert r.status_code == 200 and "Upload a statement" in r.text
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
