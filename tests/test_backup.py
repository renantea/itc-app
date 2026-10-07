"""Phase 7: the backup script.

A backup is only worth what a restore proves, so these tests restore. They run
the script as a subprocess — that is how it actually runs, environment and all,
and it catches the failure that matters most: the script not running at all.
"""
import glob
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from itc.db import init_db, now

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "tools" / "backup.py"


def _run(db_path, backup_dir, keep=None):
    env = dict(os.environ, ITC_DATABASE=str(db_path), ITC_BACKUP_DIR=str(backup_dir))
    if keep is not None:
        env["ITC_BACKUP_KEEP"] = str(keep)
    return subprocess.run([sys.executable, str(SCRIPT)], env=env,
                          capture_output=True, text=True)


def _seed(db_path):
    init_db(str(db_path))
    conn = sqlite3.connect(db_path)
    stamp = now()
    conn.execute("""INSERT INTO events (slug,name,event_date,status,created_at,updated_at)
                    VALUES ('x','X','2027-01-01','open',?,?)""", (stamp, stamp))
    conn.commit(); conn.close()


def _restore(archive, into):
    with gzip.open(archive, "rb") as a, open(into, "wb") as b:
        shutil.copyfileobj(a, b)
    return sqlite3.connect(into)


def test_a_backup_restores_to_a_working_database(tmp_path):
    db = tmp_path / "itc.db"
    _seed(db)
    out = tmp_path / "backups"

    result = _run(db, out)
    assert result.returncode == 0, result.stderr

    archives = glob.glob(str(out / "itc-*.db.gz"))
    assert len(archives) == 1

    conn = _restore(archives[0], tmp_path / "restored.db")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_the_backup_is_not_world_readable(tmp_path):
    """Entry data is dates of birth, emergency contacts and medical notes."""
    db = tmp_path / "itc.db"
    _seed(db)
    out = tmp_path / "backups"
    _run(db, out)

    assert oct(out.stat().st_mode)[-3:] == "700"
    archive = glob.glob(str(out / "itc-*.db.gz"))[0]
    assert oct(os.stat(archive).st_mode)[-3:] == "600"


def test_a_database_missing_tables_is_refused(tmp_path):
    """Keeping a snapshot nobody opened is how you find out in the worst way."""
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
    conn.commit(); conn.close()
    out = tmp_path / "backups"

    result = _run(db, out)
    assert result.returncode == 1
    assert "rejected" in result.stderr
    assert glob.glob(str(out / "itc-*.db.gz")) == []


def test_a_missing_database_fails_loudly(tmp_path):
    result = _run(tmp_path / "nothing-here.db", tmp_path / "backups")
    assert result.returncode == 1
    assert "No database" in result.stderr


def test_old_backups_are_pruned_to_the_keep_count(tmp_path):
    db = tmp_path / "itc.db"
    _seed(db)
    out = tmp_path / "backups"
    out.mkdir(mode=0o700)
    # Older archives, named the way the script names them.
    for day in range(1, 6):
        (out / f"itc-2026010{day}-000000.db.gz").write_bytes(b"old")

    _run(db, out, keep=3)
    remaining = sorted(p.name for p in out.glob("itc-*.db.gz"))
    assert len(remaining) == 3
    # Newest kept, oldest dropped — the fresh one plus the two most recent.
    assert remaining[0] == "itc-20260104-000000.db.gz"
    assert remaining[-1].startswith("itc-20")
