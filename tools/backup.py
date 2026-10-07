#!/usr/bin/env python3
"""Nightly backup of the ITC Events database.

    .venv/bin/python tools/backup.py
    ITC_BACKUP_DIR=/srv/backups .venv/bin/python tools/backup.py

Uses SQLite's own online-backup API, not a file copy. The app runs in WAL mode,
so copying the file while a request is mid-write gives a torn database plus a
`-wal` you did not copy. The backup API takes a consistent snapshot of a live
database, which is the whole reason to use it.

Python's stdlib rather than the `sqlite3` CLI, because the CLI is not installed
on this host and a backup script that depends on a binary nobody checked for is
a backup script that silently never runs.

Entry data is personal — dates of birth, emergency contacts, medical notes.
Backups inherit that: 0600 files in a 0700 directory.
"""
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("ITC_DATABASE") or os.path.join(BASE, "itc.db")
DIR = os.environ.get("ITC_BACKUP_DIR") or os.path.join(BASE, "backups")
KEEP = int(os.environ.get("ITC_BACKUP_KEEP", "14"))

#: Every table the app needs. Fewer than this and the snapshot is not a backup.
EXPECTED = {"users", "events", "races", "age_groups", "registrations",
            "entries", "settings", "entry_drafts"}


def main() -> int:
    if not os.path.exists(DB):
        print(f"No database at {DB}", file=sys.stderr)
        return 1

    os.makedirs(DIR, mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    plain = os.path.join(DIR, f"itc-{stamp}.db")

    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dst = sqlite3.connect(plain)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.chmod(plain, 0o600)

    # A backup nobody opened is a guess. Prove it before keeping it.
    check = sqlite3.connect(plain)
    try:
        tables = {r[0] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = EXPECTED - tables
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        # The table check comes before any row count. On a database missing
        # `entries`, counting raises — and the script would die with a traceback
        # having already left a half-written file on disk.
        if missing or integrity != "ok":
            reason = f"missing {sorted(missing)}, integrity={integrity}"
            entries = events = None
        else:
            reason = None
            entries = check.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            events = check.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        check.close()

    if reason:
        os.unlink(plain)
        print(f"Backup rejected — {reason}", file=sys.stderr)
        return 1

    with open(plain, "rb") as f_in, gzip.open(plain + ".gz", "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.unlink(plain)
    os.chmod(plain + ".gz", 0o600)
    print(f"{plain}.gz  ({events} events, {entries} entries, integrity ok)")

    prune()
    return 0


def prune() -> None:
    kept = sorted((f for f in os.listdir(DIR)
                   if f.startswith("itc-") and f.endswith(".db.gz")), reverse=True)
    for old in kept[KEEP:]:
        os.unlink(os.path.join(DIR, old))
        print(f"removed {old}")


if __name__ == "__main__":
    sys.exit(main())
