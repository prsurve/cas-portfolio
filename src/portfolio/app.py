"""FastAPI app: statement upload and the portfolio dashboard.

Run (bind to localhost; expose only through Cloudflare Tunnel + Access):
    PYTHONPATH=src .venv/bin/uvicorn portfolio.app:create_app --factory \
        --host 127.0.0.1 --port 8000 --no-access-log
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from portfolio.config import Settings, get_settings
from portfolio.importers.cas import CasImportError, parse_pdf_bytes, save, sha256, summary
from portfolio.portfolio_view import build_view
from portfolio.store import Store, as_utc

STATIC_DIR = Path(__file__).parent / "static"
PASSWORD_HEADER = "X-PDF-Password"

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    if store is None:
        if settings.database_url.startswith("sqlite:///./"):
            Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        store = Store.from_url(settings.database_url)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    app = FastAPI(title="Portfolio", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # State-changing calls need a custom header, which a cross-site form
        # or image tag cannot send (CSRF protection).
        if request.method not in ("GET", "HEAD") and request.headers.get("X-Requested-With") != "portfolio":
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/statements")
    async def upload(request: Request) -> JSONResponse:
        """Body: the raw PDF. Password in the X-PDF-Password header. Nothing but parsed rows is kept."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
        pdf = await request.body()
        if len(pdf) > max_bytes:
            raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
        if not pdf.startswith(b"%PDF"):
            raise HTTPException(400, "This file is not a PDF")
        password = request.headers.get(PASSWORD_HEADER, "").strip()
        if not password:
            raise HTTPException(400, "Enter the PDF password")
        try:
            parsed = await run_in_threadpool(parse_pdf_bytes, pdf, password)
        except CasImportError as exc:
            store.audit("statement_rejected", str(exc)[:300])
            return JSONResponse({"detail": str(exc)}, status_code=422)
        statement_id, created = save(store, parsed, sha256(pdf))
        return JSONResponse({"id": statement_id, "created": created, **summary(parsed)},
                            status_code=201 if created else 200)

    @app.get("/api/statements")
    def statements() -> dict[str, Any]:
        return {"statements": [
            {
                "id": s["id"], "issuer": s["issuer"], "kind": s["kind"], "cas_type": s["cas_type"],
                "period_from": s["period_from"].isoformat() if s["period_from"] else None,
                "period_to": s["period_to"].isoformat(),
                "imported_at": as_utc(s["imported_at"]).isoformat(),
            }
            for s in store.list_statements()
        ]}

    @app.delete("/api/statements/{statement_id}")
    def delete_statement(statement_id: int) -> dict[str, str]:
        if not store.delete_statement(statement_id):
            raise HTTPException(404, "Statement not found")
        store.audit("statement_deleted", f"#{statement_id}")
        return {"status": "deleted"}

    @app.get("/api/portfolio")
    def portfolio() -> dict[str, Any]:
        return build_view(store, settings.stale_after_days)

    return app
