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
    settings = Settings(database_url=f"sqlite:///{db}", max_upload_mb=1, backup_dir=str(tmp_path / "backups"), _env_file=None)
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


def _audit_rows(store):
    from sqlalchemy import select
    from portfolio.store import audit_log
    with store.engine.connect() as conn:
        return [(r.event, r.detail) for r in conn.execute(select(audit_log))]


def test_rejected_upload_audit_holds_a_code_not_the_message(env, monkeypatch):
    client, store, _, _ = env
    import portfolio.app as app_module

    def boom(pdf, password):
        raise cas.CasImportError("Could not read this statement: line says SENTINEL-STATEMENT-TEXT",
                                 code="unreadable")

    monkeypatch.setattr(app_module, "parse_pdf_bytes", boom)
    r = client.post("/api/statements", content=PDF, headers=HEADERS)
    assert r.status_code == 422 and "SENTINEL-STATEMENT-TEXT" in r.json()["detail"]  # the user still sees it
    assert ("statement_rejected", "unreadable") in _audit_rows(store)
    assert "SENTINEL" not in repr(_audit_rows(store))


def test_import_errors_carry_codes(monkeypatch):
    import casparser
    from casparser.exceptions import IncorrectPasswordError, ParserException

    def raising(exc):
        def read(*a, **k):
            raise exc
        return read

    for exc, code in [(IncorrectPasswordError("x"), "wrong_password"), (ParserException("x"), "unreadable"),
                      (ValueError("x"), "not_pdf")]:
        monkeypatch.setattr(casparser, "read_cas_pdf", raising(exc))
        with pytest.raises(cas.CasImportError) as e:
            cas.parse_pdf_bytes(b"%PDF", "pw")
        assert e.value.code == code
    assert cas.CasImportError("m").code == "import_error"


def test_statement_list_flags_outdated_parser(env):
    client, store, _, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    [s] = client.get("/api/statements").json()["statements"]
    assert s["parser_version"] == cas.PARSER_VERSION and s["outdated"] is False and s["warnings"] == []
    from sqlalchemy import update
    from portfolio.store import statements
    with store.engine.begin() as conn:
        conn.execute(update(statements).values(parser_version=cas.PARSER_VERSION - 1, warnings="a\nb"))
    [s] = client.get("/api/statements").json()["statements"]
    assert s["outdated"] is True and s["warnings"] == ["a", "b"]


def test_export_has_everything_and_only_masked_ids(env):
    client, store, _, use = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    use("demat")
    client.post("/api/statements", content=PDF + b"other", headers=HEADERS)
    r = client.get("/api/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert len(data["statements"]) == 2 and data["stock_holdings"] and data["mf_holdings"] and data["mf_transactions"]
    assert all(isinstance(h["quantity"], str) for h in data["stock_holdings"])  # exact decimals, not floats
    text = r.text
    for private in ("ABCDE1234F", "TEST USER", "12345678", "1234567/89", "9999999999", "t@example.com"):
        assert private not in text


def test_delete_all_needs_the_phrase_and_keeps_the_audit_log(env):
    client, store, _, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    h = {"X-Requested-With": "portfolio"}
    assert client.delete("/api/data", headers=h).status_code == 400
    assert client.request("DELETE", "/api/data", headers=h, json={"confirm": "yes"}).status_code == 400
    assert client.request("DELETE", "/api/data", json={"confirm": "delete all data"}).status_code == 403  # no CSRF header
    assert len(client.get("/api/statements").json()["statements"]) == 1

    r = client.request("DELETE", "/api/data", headers=h, json={"confirm": "delete all data"})
    assert r.status_code == 200 and r.json()["removed"]["statements"] == 1
    data = client.get("/api/export").json()
    assert all(data[t] == [] for t in ("statements", "stock_holdings", "mf_holdings", "mf_transactions", "statement_accounts", "prices"))
    assert [a["event"] for a in data["audit_log"]][-1] == "data_deleted"
    assert client.get("/api/portfolio").status_code == 200


def test_delete_all_takes_a_verified_backup_first(env, tmp_path):
    client, store, _, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    h = {"X-Requested-With": "portfolio"}
    r = client.request("DELETE", "/api/data", headers=h, json={"confirm": "delete all data"})
    assert r.status_code == 200
    [saved] = list((tmp_path / "backups").glob("portfolio-*.db"))
    assert r.json()["backup"] == saved.name
    from portfolio.backup import check_database
    assert check_database(saved) == []
    assert Store.from_url(f"sqlite:///{saved}").list_statements() != []  # the copy still holds the data


def test_delete_all_is_refused_when_the_backup_fails(env, tmp_path, monkeypatch):
    client, store, _, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    import portfolio.app as app_module
    from portfolio.backup import BackupError

    def broken(*args, **kwargs):
        raise BackupError("disk full")
    monkeypatch.setattr(app_module, "safety_backup", broken)
    r = client.request("DELETE", "/api/data", headers={"X-Requested-With": "portfolio"}, json={"confirm": "delete all data"})
    assert r.status_code == 503 and "nothing was deleted" in r.json()["detail"]
    assert len(client.get("/api/statements").json()["statements"]) == 1


def test_preview_saves_nothing_and_reports_what_saving_would_do(env):
    client, store, _, _ = env
    before = client.get("/api/export").json()
    before.pop("exported_at")

    r = client.post("/api/statements/preview", content=PDF, headers=HEADERS)
    assert r.status_code == 200 and r.json()["would"] == "new" and r.json()["kind"] == "mf"
    after = client.get("/api/export").json()
    after.pop("exported_at")
    assert after == before  # no statement, holding or audit row was written

    assert client.post("/api/statements", content=PDF, headers=HEADERS).status_code == 201
    assert client.post("/api/statements/preview", content=PDF, headers=HEADERS).json()["would"] == "duplicate"
    assert client.post("/api/statements/preview", content=PDF, headers={"X-Requested-With": "portfolio"}).status_code == 400


def test_preview_errors_keep_the_upload_rules_and_write_no_audit_row(env):
    client, store, _, _ = env
    bad = {**HEADERS, "X-PDF-Password": "wrong"}
    r = client.post("/api/statements/preview", content=PDF, headers=bad)
    assert r.status_code == 422
    assert client.post("/api/statements/preview", content=b"not a pdf", headers=HEADERS).status_code == 400
    assert client.post("/api/statements/preview", content=PDF).status_code == 403  # no CSRF header
    assert all(a["event"] != "statement_rejected" for a in client.get("/api/export").json()["audit_log"])
    assert "wrong" not in r.text


def test_import_status_marks_older_parser_versions(env):
    client, store, _, _ = env
    client.post("/api/statements", content=PDF, headers=HEADERS)
    digest = cas.sha256(PDF)
    assert store.import_status(digest, 1) == "duplicate"
    assert store.import_status(digest, 999) == "replaces_older"
    assert store.import_status("0" * 64, 1) == "new"


def test_every_state_changing_route_rejects_a_request_without_the_csrf_header(env):
    """Sweeps the route table, so a route added later without the guard fails here."""
    client, _, _, _ = env
    routes = [(m, r.path) for r in client.app.routes if hasattr(r, "methods") for m in r.methods
              if m in {"POST", "PUT", "PATCH", "DELETE"}]
    assert len(routes) >= 9
    for method, path in routes:
        url = path.replace("{statement_id}", "1").replace("{snapshot_id}", "1")
        assert client.request(method, url).status_code == 403, f"{method} {path}"
