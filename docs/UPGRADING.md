# Upgrading and rolling back

The app keeps everything in one SQLite file (`data/portfolio.db` by default). An upgrade can add columns or convert stored values, so take a copy first.

## Upgrade

1. **Back up.** Settings → Back up now, or `PYTHONPATH=src .venv/bin/python -m portfolio.backup create`. Check that the new file appears in `data/backups`.
2. **Stop the server.**
3. **Pull the new code** and reinstall if `requirements.lock` changed:
   `git pull && .venv/bin/pip install --index-url https://pypi.org/simple -r requirements.lock`
4. **Start the server** again. If the database needs changes, the app first writes a safety backup (never pruned) and only then changes the file. If that backup fails, the database is left untouched.
5. **Check.** Open `/healthz` (should say `ok`) and the Settings page (database check, row counts). Then reload the page: a server started before the pull answers 404 for new routes.

## Roll back

1. Stop the server and check out the earlier version of the code.
2. Restore the backup from step 1 of the upgrade (or the safety backup the upgrade made):
   `PYTHONPATH=src .venv/bin/python -m portfolio.backup restore data/backups/<file> --force`
   The restore verifies the copy first and keeps the database it replaces aside.
3. Start the server.

## A database from a newer version

If you go back to older code without restoring a backup, the app refuses to open a database whose schema version is higher than it understands. It says so at start-up and does not change the file. Update the code again, or restore a backup made by the older version.
