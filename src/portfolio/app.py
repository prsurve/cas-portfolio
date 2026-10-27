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
from portfolio.backup import BackupError, create_backup, newest_age_days, safety_backup
from portfolio.changes import compare
from portfolio.confidence import data_confidence
from portfolio.confidence import data_confidence
from portfolio.health import backup_list, system_health
from portfolio.history import value_history
from portfolio.returns import compute_returns
from portfolio.import_log import audit_events, statement_detail, statement_summary
from portfolio.holdings import holding_detail
from portfolio.portfolio_view import _jsonable, build_view, compute_view
from portfolio.prices import PriceFetchError
from portfolio.refresh import refresh_all
from portfolio.store import Store, as_utc, sqlite_file

STATIC_DIR = Path(__file__).parent / "static"
PASSWORD_HEADER = "X-PDF-Password"
DELETE_PHRASE = "delete all data"

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def create_app(settings: Settings | None = None, store: Store | None = None,
               nav_fetcher: Callable[[], str] | None = None,
               exchange_fetcher: Callable[[str], Any] | None = None) -> FastAPI:
    settings = settings or get_settings()
    if store is None:
        if settings.database_url.startswith("sqlite:///./"):
            Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        store = Store.from_url(settings.database_url, before_upgrade=lambda path: safety_backup(
            path, Path(settings.backup_dir), settings.backup_gpg_recipient))
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

    async def read_upload(request: Request) -> tuple[bytes, str]:
        """The PDF bytes and password from an upload request, after the size, type and password checks."""
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
        return pdf, password

    @app.post("/api/statements")
    async def upload(request: Request) -> JSONResponse:
        """Body: the raw PDF. Password in the X-PDF-Password header. Nothing but parsed rows is kept."""
        pdf, password = await read_upload(request)
        try:
            parsed = await run_in_threadpool(parse_pdf_bytes, pdf, password)
        except CasImportError as exc:
            store.audit("statement_rejected", exc.code)
            return JSONResponse({"detail": str(exc)}, status_code=422)
        statement_id, created = save(store, parsed, sha256(pdf))
        return JSONResponse({"id": statement_id, "created": created, **summary(parsed)},
                            status_code=201 if created else 200)

    @app.post("/api/statements/preview")
    async def preview(request: Request) -> JSONResponse:
        """Same request as the upload, but nothing is saved, not even an audit row.
        Returns what would be read and what saving would do: new, duplicate or replaces_older."""
        pdf, password = await read_upload(request)
        try:
            parsed = await run_in_threadpool(parse_pdf_bytes, pdf, password)
        except CasImportError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return JSONResponse({"would": store.import_status(sha256(pdf), PARSER_VERSION), **summary(parsed)})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """For a monitor or a systemd watchdog. No portfolio data, and no database internals."""
        healthy = store.quick_check() == "ok"
        return JSONResponse({
            "status": "ok" if healthy else "degraded", "database": "ok" if healthy else "error",
            "schema_version": store.schema_version(),
            "last_backup_days": newest_age_days(Path(settings.backup_dir)),
        }, status_code=200 if healthy else 503)

    @app.get("/api/statements")
    def statements() -> dict[str, Any]:
        return {"statements": [statement_summary(s) for s in store.list_statements()]}

    @app.get("/api/statements/{statement_id}")
    def statement(statement_id: int) -> dict[str, Any]:
        """One statement: what was read from it, and each printed total against the rows read."""
        found = statement_detail(store, statement_id)
        if found is None:
            raise HTTPException(404, "Statement not found")
        return found

    @app.get("/api/audit")
    def audit(limit: int = 50) -> dict[str, Any]:
        """Recent events (import, delete, price refresh), newest first. Event names and short codes only."""
        return {"events": audit_events(store, max(1, min(limit, 200)))}

    @app.delete("/api/statements/{statement_id}")
    def delete_statement(statement_id: int) -> dict[str, str]:
        if not store.delete_statement(statement_id):
            raise HTTPException(404, "Statement not found")
        store.audit("statement_deleted", f"#{statement_id}")
        return {"status": "deleted"}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Database check, disk space, backups, last price refresh and row counts. No portfolio data."""
        return system_health(store, settings)

    @app.get("/api/backups")
    def backups() -> dict[str, Any]:
        return backup_list(settings)

    @app.post("/api/backups")
    def make_backup() -> JSONResponse:
        """A verified backup now, encrypted if a gpg recipient is set, pruned to the retention count."""
        db_file = sqlite_file(store.engine)
        if db_file is None:
            raise HTTPException(400, "Backups are for the local SQLite database only.")
        try:
            made = create_backup(db_file, Path(settings.backup_dir), recipient=settings.backup_gpg_recipient,
                                 keep=settings.backup_keep)
        except BackupError:  # the message can name paths or gpg output, so it stays out of the response
            raise HTTPException(503, "The backup could not be made. Check the backup folder and the gpg key.")
        store.audit("backup_created", made.name)
        return JSONResponse({"name": made.name}, status_code=201)

    @app.get("/api/export")
    def export_data() -> JSONResponse:
        """Everything stored, as one JSON file to download. Masked IDs only: that is all the database holds."""
        name = f"portfolio-export-{date.today().isoformat()}.json"
        return JSONResponse(store.export_all(), headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.delete("/api/data")
    async def delete_data(request: Request) -> dict[str, Any]:
        """Wipe all statements and prices. Needs the typed phrase, so a stray call cannot do it."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("confirm") != DELETE_PHRASE:
            raise HTTPException(400, f'Send {{"confirm": "{DELETE_PHRASE}"}} to delete everything')
        # A verified copy first. If it cannot be made, nothing is deleted.
        saved = None
        db_file = sqlite_file(store.engine)
        if db_file is not None:
            try:
                saved = safety_backup(db_file, Path(settings.backup_dir), settings.backup_gpg_recipient).name
            except BackupError:
                raise HTTPException(503, "Could not make a backup first, so nothing was deleted. "
                                         "Check the backup folder and gpg key, then try again.")
        return {"status": "deleted", "removed": store.delete_all(), "backup": saved}

    @app.get("/api/portfolio")
    def portfolio() -> dict[str, Any]:
        return build_view(store, settings.stale_after_days, concentration_alert_pct=settings.concentration_alert_pct)

    @app.get("/api/confidence")
    def confidence() -> dict[str, Any]:
        """What is wrong or missing, which figures it affects and what to do; how far each figure can be trusted;
        and how new each data source is. It describes: nothing here changes a figure or a holding."""
        return data_confidence(store, compute_view(store, settings.stale_after_days))

    @app.get("/api/returns")
    def returns() -> dict[str, Any]:
        """XIRR per fund and for the funds that can be worked out together, each with its reason when it cannot.
        Computed on request (it takes a moment for a long history), from statement values."""
        view = compute_view(store, settings.stale_after_days)
        return _jsonable(compute_returns(store, view))

    @app.get("/api/holdings/{key:path}")
    def holding(key: str) -> dict[str, Any]:
        """One holding (ISIN, or a fund's name when it has none) with where each number comes from."""
        found = holding_detail(store, key, settings.stale_after_days)
        if found is None:
            raise HTTPException(404, "No holding with that ISIN or name in the latest statements.")
        return found

    @app.get("/api/history")
    def history() -> dict[str, Any]:
        return {"points": value_history(store)}

    @app.post("/api/prices/refresh")
    async def refresh_prices() -> dict[str, Any]:
        """Download the public price files (AMFI fund NAVs; NSE then BSE closes) for what you hold."""
        if not settings.fetch_prices:
            raise HTTPException(403, "Price fetching is turned off (PORTFOLIO_FETCH_PRICES=false).")
        try:
            result = await run_in_threadpool(refresh_all, store, nav_fetcher, exchange_fetcher)
        except PriceFetchError as exc:
            store.audit("prices_failed", "all sources")
            raise HTTPException(502, str(exc)) from exc
        parts = [f"{label} priced {r['priced']} of {r['held']}"
                 for label, r in (("AMFI", result["nav"]), ("exchange", result["exchange"])) if r and r["held"]]
        store.audit("prices_refreshed", "; ".join(parts) or "nothing to price")
        if result["errors"]:
            store.audit("prices_failed", ",".join(sorted(result["errors"])))
        return result

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
