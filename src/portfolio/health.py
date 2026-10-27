"""How the installation is doing, and what it holds. For the Settings page.

Nothing here returns portfolio data: only counts, sizes, dates, file names inside the backup folder and
yes/no answers. The database location is shown for a local SQLite file and never for any other database,
whose address may contain a password.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from portfolio.backup import list_backups, newest_age_days
from portfolio.config import Settings
from portfolio.store import Store, as_utc, sqlite_file

LOW_DISK_BYTES = 500 * 1024 * 1024
BACKUP_STALE_DAYS = 30


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return path.name  # outside the working folder: the file name only


def system_health(store: Store, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    db_file = sqlite_file(store.engine)
    check = store.quick_check()
    backup_dir = Path(settings.backup_dir)
    backups = list_backups(backup_dir)
    newest = newest_age_days(backup_dir, now)
    refreshed = store.last_audit("prices_refreshed")
    disk = None
    if db_file is not None and db_file.exists():
        usage = shutil.disk_usage(db_file.parent)
        disk = {"free_bytes": usage.free, "total_bytes": usage.total}

    attention: list[str] = []
    if check != "ok":
        attention.append("The database failed its quick check. Restore a backup (see below) before importing anything.")
    if not backups:
        attention.append("There is no backup yet. Use Back up now.")
    elif newest is not None and newest > BACKUP_STALE_DAYS:
        attention.append(f"The newest backup is {newest} days old.")
    if disk and disk["free_bytes"] < LOW_DISK_BYTES:
        attention.append("Less than 500 MB of disk space is free.")

    return {
        "status": "ok" if not attention else "attention",
        "attention": attention,
        "database": {
            "ok": check == "ok",
            "engine": "SQLite" if db_file is not None else "external database",
            "location": _relative(db_file) if db_file is not None else None,
            "schema_version": store.schema_version(),
            "size_bytes": db_file.stat().st_size if db_file is not None and db_file.exists() else None,
        },
        "disk": disk,
        "backups": {
            "count": len(backups), "newest_days": newest, "encrypted": bool(settings.backup_gpg_recipient),
            "keep": settings.backup_keep, "folder": settings.backup_dir,
        },
        "prices": {
            "fetching": settings.fetch_prices,
            "last_refresh": as_utc(refreshed["ts"]).isoformat() if refreshed else None,
        },
        "stored": store.table_counts(),
    }


def backup_list(settings: Settings) -> dict[str, Any]:
    found = list_backups(Path(settings.backup_dir))
    return {
        "encrypted": bool(settings.backup_gpg_recipient),
        "backups": [{"name": p.name, "size_bytes": p.stat().st_size,
                     "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()} for p in found],
    }
