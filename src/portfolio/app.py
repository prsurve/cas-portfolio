"""FastAPI app: statement upload and the portfolio dashboard.

Run (bind to localhost; expose only through Cloudflare Tunnel + Access):
    PYTHONPATH=src .venv/bin/uvicorn portfolio.app:create_app --factory \
        --host 127.0.0.1 --port 8000 --no-access-log
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from portfolio.config import Settings, get_settings
from portfolio.importers.cas import PARSER_VERSION, CasImportError, parse_pdf_bytes, save, sha256, summary
from portfolio.changes import compare
from portfolio.history import value_history
from portfolio.portfolio_view import build_view
from portfolio.prices import PriceFetchError, fetch_navall, refresh_nav
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


def create_app(settings: Settings | None = None, store: Store | None = None,
               nav_fetcher: Callable[[], str] | None = None) -> FastAPI:
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
            store.audit("statement_rejected", exc.code)
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
                "parser_version": s["parser_version"],
                # Imported by an older parser: uploading the same PDF again would read more from it.
                "outdated": s["parser_version"] < PARSER_VERSION,
                "warnings": s["warnings"].split("\n") if s["warnings"] else [],
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

    @app.get("/api/history")
    def history() -> dict[str, Any]:
        return {"points": value_history(store)}

    @app.post("/api/prices/refresh")
    async def refresh_prices() -> dict[str, Any]:
        """Download AMFI's public NAV file and store the latest NAV for the funds you hold."""
        if not settings.fetch_prices:
            raise HTTPException(403, "Price fetching is turned off (PORTFOLIO_FETCH_PRICES=false).")
        try:
            report = await run_in_threadpool(refresh_nav, store, nav_fetcher or fetch_navall)
        except PriceFetchError as exc:
            store.audit("prices_failed", "AMFI")
            raise HTTPException(502, str(exc)) from exc
        store.audit("prices_refreshed", f"AMFI priced {report['priced']} of {report['held']}")
        return report

    @app.get("/api/changes")
    def changes(from_date: date | None = None, to_date: date | None = None) -> dict[str, Any]:
        """Holdings that differ between two statement dates (default: the two latest)."""
        if (from_date is None) != (to_date is None):
            raise HTTPException(400, "Give both dates, or neither.")
        if from_date is None:
            dates = store.statement_dates()
            if len(dates) < 2:
                raise HTTPException(400, "Upload at least two statements to compare.")
            from_date, to_date = dates[-2], dates[-1]
        try:
            return compare(store, from_date, to_date)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return app
