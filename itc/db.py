"""Database: schema, connections, migrations and query helpers.

WHY IT IS SHAPED THIS WAY
-------------------------
The split that matters is **registrations vs entries**. One parent entering
three children is one registration and three entries. Everything operational —
capacity, start lists, the timing company's export — counts entries, never
registrations, because an entry is one human on one start line.

  WAL           a browser reading the event list never waits on an entry
                being written
  entries       one row per athlete, carrying their own date of birth, age
                group, emergency contact and waiver
  capacity      counted from entries at write time, inside the transaction
                (see `race_taken`), so two people racing for the last place
                cannot both get it

Money is in the schema from day one (`races.price_bhd`, default 0) even though
every event is free today. Adding a column later is easy; backfilling one
across live registrations is not.
"""
import os
import sqlite3
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ITC_DATABASE") or os.path.join(BASE_DIR, "itc.db")

#: Disciplines ITC runs. Mirrored by the CHECK on races.discipline.
DISCIPLINES = ("running", "duathlon", "triathlon", "aquathlon")

#: Event lifecycle. Only 'open' accepts entries.
EVENT_STATUSES = ("draft", "open", "closed", "completed")

#: An entry's own state, independent of the event's.
ENTRY_STATUSES = ("confirmed", "waitlisted", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    phone         TEXT,
    -- 'athlete' registers themselves and others; 'admin' runs events.
    role          TEXT NOT NULL DEFAULT 'athlete'
                  CHECK (role IN ('athlete','admin')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    summary       TEXT,
    description   TEXT,
    venue         TEXT,
    -- Local race day. Dates are stored as plain YYYY-MM-DD: a race day is a
    -- calendar day in Bahrain, not an instant, and timezone maths on it only
    -- ever introduces off-by-one bugs.
    event_date    TEXT NOT NULL,
    reg_opens_at  TEXT,
    reg_closes_at TEXT,
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','open','closed','completed')),
    hero_image    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_listing ON events(status, event_date);

CREATE TABLE IF NOT EXISTS races (
    race_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    discipline    TEXT NOT NULL
                  CHECK (discipline IN ('running','duathlon','triathlon','aquathlon')),
    -- Free text per leg ("750 m", "20 km") rather than numbers: ITC states
    -- distances in whatever unit reads best for the race, and nothing computes
    -- with them.
    swim_distance TEXT,
    bike_distance TEXT,
    run_distance  TEXT,
    start_time    TEXT,
    -- NULL means uncapped. 0 would mean "nobody may enter", which is different.
    capacity      INTEGER,
    -- Every event is free today. The column exists so adding payment later is
    -- a feature, not a migration of live registrations.
    price_bhd     REAL NOT NULL DEFAULT 0,
    min_age       INTEGER,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_races_event ON races(event_id, sort_order);

-- Age bands per race. Held as rows rather than hard-coded so ITC can run a
-- junior series and a masters series off the same code.
CREATE TABLE IF NOT EXISTS age_groups (
    age_group_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id       INTEGER NOT NULL REFERENCES races(race_id) ON DELETE CASCADE,
    label         TEXT NOT NULL,
    gender        TEXT NOT NULL DEFAULT 'any'
                  CHECK (gender IN ('male','female','any')),
    min_age       INTEGER,
    max_age       INTEGER,
    sort_order    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_age_groups_race ON age_groups(race_id, sort_order);

-- One order. The person who filled the form in, not necessarily a competitor.
CREATE TABLE IF NOT EXISTS registrations (
    registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference       TEXT NOT NULL UNIQUE,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    event_id        INTEGER NOT NULL REFERENCES events(event_id),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_registrations_user ON registrations(user_id, created_at DESC);

-- One athlete on one start line. This is the table that matters.
CREATE TABLE IF NOT EXISTS entries (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_id INTEGER NOT NULL REFERENCES registrations(registration_id) ON DELETE CASCADE,
    race_id         INTEGER NOT NULL REFERENCES races(race_id),
    age_group_id    INTEGER REFERENCES age_groups(age_group_id),
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    -- Kept alongside age_group_id on purpose: the group is derived from this,
    -- and if ITC ever changes the banding rule the source data is still here
    -- to recompute from.
    date_of_birth   TEXT NOT NULL,
    gender          TEXT NOT NULL CHECK (gender IN ('male','female','other')),
    email           TEXT,
    phone           TEXT,
    club            TEXT,
    shirt_size      TEXT,
    emergency_name  TEXT,
    emergency_phone TEXT,
    medical_notes   TEXT,
    -- Required for under-18s, empty for adults. Held on the entry, not the
    -- account: the account holder is not always the guardian.
    guardian_name   TEXT,
    -- Null until accepted. A timestamp rather than a flag, because "when did
    -- they agree" is the question that gets asked after an incident.
    waiver_accepted_at TEXT,
    bib_number      TEXT,
    status          TEXT NOT NULL DEFAULT 'confirmed'
                    CHECK (status IN ('confirmed','waitlisted','cancelled')),
    -- When the place was given back. Kept rather than deleting the row: the
    -- entry is evidence that someone accepted a waiver and then withdrew.
    cancelled_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entries_race ON entries(race_id, status);
CREATE INDEX IF NOT EXISTS ix_entries_registration ON entries(registration_id);

-- A half-finished entry form. Entering a family of four is several screens,
-- and a phone on race-day wifi will drop one of them.
--
-- This is a table rather than the session cookie on purpose: four athletes
-- with medical notes is comfortably past the 4 KB a cookie holds, and the
-- failure mode there is silent — the browser drops the cookie and the entrant
-- watches their typing disappear. Only an opaque token lives in the session.
CREATE TABLE IF NOT EXISTS entry_drafts (
    draft_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    race_id    INTEGER NOT NULL REFERENCES races(race_id) ON DELETE CASCADE,
    party_size INTEGER NOT NULL DEFAULT 1,
    -- JSON, keyed by the athlete's position in the party. Deliberately not
    -- columns: this is throwaway form state, and its shape follows the form.
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entry_drafts_user ON entry_drafts(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: Defaults written once, on first boot. `age_rule` is deliberately a setting:
#: World Triathlon takes age as of 31 December of the race year, Ironman takes
#: age on race day, and ITC has not confirmed which it follows. Phase 4 reads
#: this rather than baking one convention into the code.
DEFAULT_SETTINGS = {
    "club_name": "International Triathlon Club",
    "age_rule": "dec31",            # dec31 | race_day — confirm with ITC
    "currency": "BHD",
}


def connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: reading the event list never blocks on an entry being written.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(path: str = None) -> None:
    """Create or upgrade. Safe to run on every boot."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                         (key, value))
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Bring a database made by an earlier version up to date.

    New *tables* need nothing here — `CREATE TABLE IF NOT EXISTS` in SCHEMA
    runs on every boot. New *columns* on an existing table do, because SQLite
    will not rewrite one. Adding a column is the one safe ALTER; changing a
    CHECK or a type needs the full rebuild dance, so prefer additive changes.
    """
    _add_column(conn, "entries", "guardian_name", "TEXT")
    _add_column(conn, "entries", "cancelled_at", "TEXT")


def _add_column(conn, table: str, column: str, decl: str) -> None:
    present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in present:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def now() -> str:
    """UTC, with the Z that `to_local` looks for."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Flask glue ───────────────────────────────────────────
def get_db():
    from flask import g
    if "db" not in g:
        from flask import current_app
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    from flask import g
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query_all(sql: str, params: tuple = ()) -> list:
    return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()):
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


def scalar(sql: str, params: tuple = ()):
    row = get_db().execute(sql, params).fetchone()
    return row[0] if row else None


def get_setting(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


class transaction:
    """`with transaction() as conn:` — commits, or rolls back on error."""

    def __enter__(self):
        self.conn = get_db()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


class exclusive_transaction(transaction):
    """A transaction that takes the write lock *before* it reads.

    `transaction` is enough for ordinary writes. This one exists for capacity:
    counting places and then inserting an entry is only safe if nobody else
    can insert in between, and a plain deferred transaction does not promise
    that — the count happens outside any lock, so two entrants can both read
    "1 place left" and both take it. BEGIN IMMEDIATE grabs the write lock on
    entry, which serialises the count and the insert as one step.
    """

    def __enter__(self):
        self.conn = get_db()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn


def race_taken(conn, race_id: int) -> int:
    """Places already gone on a race.

    Cancelled entries free their place; waitlisted ones were never given one.
    Call this *inside* the transaction that writes the entry — checking it when
    the page is rendered lets two people take the same last place.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM entries WHERE race_id = ? AND status = 'confirmed'",
        (race_id,)).fetchone()
    return row["n"] if row else 0
