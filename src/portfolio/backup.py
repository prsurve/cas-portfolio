"""Backups of the SQLite database: create, list, check and restore.

    PYTHONPATH=src .venv/bin/python -m portfolio.backup create     # consistent copy, even while the app runs
    PYTHONPATH=src .venv/bin/python -m portfolio.backup list
    PYTHONPATH=src .venv/bin/python -m portfolio.backup check      # integrity check of the live database
    PYTHONPATH=src .venv/bin/python -m portfolio.backup restore data/backups/portfolio-<stamp>.db[.gpg]

A backup holds everything in the database, so the backup folder is private (0700, files 0600)
and lives under data/, which is git-ignored. To keep copies off the device, set
PORTFOLIO_BACKUP_GPG_RECIPIENT to a gpg public key: backups are then encrypted to that key and
the plaintext copy is deleted. The Pi needs only the public key; decrypting needs the private
key, which should live somewhere else. If encryption fails, no plaintext copy is left behind.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_TABLES = ("statements", "stock_holdings", "mf_holdings", "mf_transactions")
PATTERN = re.compile(r"portfolio-\d{8}-\d{6}\.db(\.gpg)?")
GPG_TIMEOUT = 120


class BackupError(RuntimeError):
    """A user-facing problem with a backup or restore."""


def db_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise BackupError("Backups here cover SQLite only. For Postgres use pg_dump.")
    return Path(url.removeprefix("sqlite:///"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def check_database(path: Path) -> list[str]:
    """Problems found in a database file, as short messages (empty means fine)."""
    if not path.is_file():
        return [f"file not found: {path.name}"]
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            result = [r[0] for r in con.execute("PRAGMA integrity_check")]
            problems = [] if result == ["ok"] else result[:5]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return [f"not a database ({exc})"]
    return problems + [f"missing table {t}" for t in REQUIRED_TABLES if t not in tables]


def list_backups(dest: Path) -> list[Path]:
    """Backups in a folder, newest first. Other files there are ignored."""
    if not dest.is_dir():
        return []
    return sorted((p for p in dest.iterdir() if p.is_file() and PATTERN.fullmatch(p.name)),
                  key=lambda p: p.name, reverse=True)


def newest_age_days(dest: Path, now: datetime | None = None) -> int | None:
    found = list_backups(dest)
    if not found:
        return None
    written = datetime.fromtimestamp(found[0].stat().st_mtime, timezone.utc)
    return max(0, ((now or _now()) - written).days)


def safety_backup(path: Path, dest: Path, recipient: str | None = None) -> Path:
    """A copy taken just before something destructive (delete all, schema upgrade).
    Never pruned by retention, so it cannot push out other backups or be removed by this call."""
    return create_backup(path, dest, recipient=recipient, keep=None)


def _gpg(gpg: str, args: list[str], what: str) -> None:
    try:
        run = subprocess.run([gpg, "--batch", "--yes", *args], capture_output=True, text=True, timeout=GPG_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"{what} failed: could not run gpg ({type(exc).__name__}).") from exc
    if run.returncode != 0:
        first = next((line for line in run.stderr.splitlines() if line.strip()), "no details")
        raise BackupError(f"{what} failed (gpg exit {run.returncode}): {first[:200]}")


def create_backup(path: Path, dest: Path, *, recipient: str | None = None, keep: int | None = None,
                  now: datetime | None = None, gpg: str = "gpg") -> Path:
    """A verified copy of the database, encrypted to `recipient` if given. Returns its path."""
    if not path.is_file():
        raise BackupError(f"Database file not found: {path}")
    now = now or _now()
    name = f"portfolio-{now:%Y%m%d-%H%M%S}.db"
    final = dest / (name + (".gpg" if recipient else ""))
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    if final.exists() or (dest / name).exists():
        raise BackupError(f"{final.name} already exists.")
    plain, sealed = dest / f".partial-{name}", dest / f".partial-{name}.gpg"
    try:
        source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(plain)
        try:
            source.backup(target)  # consistent even if the app is writing
        finally:
            target.close()
            source.close()
        os.chmod(plain, 0o600)
        problems = check_database(plain)
        if problems:
            raise BackupError("The copy failed its integrity check: " + "; ".join(problems))
        if recipient:
            _gpg(gpg, ["--trust-model", "always", "--encrypt", "--recipient", recipient,
                       "--output", str(sealed), str(plain)], "Encryption")
            os.chmod(sealed, 0o600)
            os.replace(sealed, final)
        else:
            os.replace(plain, final)
    finally:
        for leftover in (plain, sealed):  # never leave a readable copy behind
            leftover.unlink(missing_ok=True)
    if keep:
        for old in list_backups(dest)[keep:]:
            old.unlink()
    return final


def restore_backup(backup: Path, path: Path, *, force: bool = False, now: datetime | None = None,
                   gpg: str = "gpg") -> Path | None:
    """Put a backup in place as the database. The backup is verified first; an existing database
    is only replaced with force, and is kept beside the new one. Returns where it went, if anywhere.
    Stop the app first."""
    if not backup.is_file():
        raise BackupError(f"Backup file not found: {backup}")
    if path.exists() and not force:
        raise BackupError(f"{path} already exists. Stop the app, then restore with --force. "
                          "The current file is kept beside the restored one.")
    now = now or _now()
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        os.chmod(path.parent, 0o700)
    tmp = path.parent / f".restore-{now:%Y%m%d-%H%M%S}.tmp"
    try:
        if backup.name.endswith(".gpg"):
            _gpg(gpg, ["--decrypt", "--output", str(tmp), str(backup)], "Decryption")
        else:
            shutil.copyfile(backup, tmp)
        os.chmod(tmp, 0o600)
        problems = check_database(tmp)
        if problems:
            raise BackupError(f"{backup.name} is not a usable backup: " + "; ".join(problems))
        aside = None
        if path.exists():
            aside = path.with_name(f"{path.name}.before-restore-{now:%Y%m%d-%H%M%S}")
            os.replace(path, aside)
            for suffix in ("-wal", "-shm", "-journal"):  # belong to the file just moved
                if (path.parent / (path.name + suffix)).exists():
                    os.replace(path.parent / (path.name + suffix), aside.parent / (aside.name + suffix))
        os.replace(tmp, path)
        return aside
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import portfolio.config as config

    parser = argparse.ArgumentParser(description="Back up, check and restore the portfolio database.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="write a verified copy")
    create.add_argument("--keep", type=int, help="keep only this many backups (default: the setting)")
    sub.add_parser("list", help="show existing backups")
    check = sub.add_parser("check", help="integrity check of a database file (default: the live one)")
    check.add_argument("file", nargs="?", type=Path)
    restore = sub.add_parser("restore", help="replace the database with a backup (stop the app first)")
    restore.add_argument("file", type=Path)
    restore.add_argument("--force", action="store_true", help="replace an existing database, keeping it aside")
    args = parser.parse_args(argv)

    settings = config.get_settings()
    dest = Path(settings.backup_dir)
    try:
        if args.command == "create":
            out = create_backup(db_path(settings.database_url), dest, recipient=settings.backup_gpg_recipient,
                                keep=args.keep or settings.backup_keep)
            print(f"Backup written: {out}" + (" (encrypted)" if out.name.endswith(".gpg") else " (not encrypted)"))
        elif args.command == "list":
            found = list_backups(dest)
            for p in found:
                print(f"{p.name}  {p.stat().st_size / 1024:.0f} KB")
            print("No backups yet." if not found else f"Newest is {newest_age_days(dest)} day(s) old.")
        elif args.command == "check":
            target = args.file or db_path(settings.database_url)
            problems = check_database(target)
            print("Database check: ok" if not problems else "Database check FAILED: " + "; ".join(problems))
            return 1 if problems else 0
        else:
            aside = restore_backup(args.file, db_path(settings.database_url), force=args.force)
            print("Restored." + (f" The previous database was kept as {aside.name}." if aside else "")
                  + " Start the app again.")
    except BackupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
