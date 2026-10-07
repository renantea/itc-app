"""Phase 1: the stack is wired end to end and the schema holds its shape.

These are the assertions worth having before any feature is built — a schema
that silently accepts nonsense, or a page that caches, costs far more to find
later than it does to pin down now.
"""
import sqlite3

import pytest

from itc.db import connect, race_taken, now


def test_the_home_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "International Triathlon Club" in body
    # The four disciplines ITC runs.
    for word in ("Running", "Duathlon", "Triathlon", "Aquathlon"):
        assert word in body


def test_an_empty_calendar_says_so_rather_than_showing_nothing(client):
    assert "No races open yet" in client.get("/").get_data(as_text=True)


def test_healthz_reports_the_database_is_reachable(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_pages_are_not_cached(client):
    """Pages will carry entrant details, and a cached document also means a
    deploy can land and stay invisible."""
    assert "no-store" in client.get("/").headers.get("Cache-Control", "")


def test_security_headers_are_present(client):
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]


def test_a_missing_page_is_a_styled_404(client):
    r = client.get("/no-such-race")
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert "404" in body and "Back to events" in body


def test_static_assets_are_stamped_so_they_cannot_go_stale(client):
    body = client.get("/").get_data(as_text=True)
    assert "app.css?v=" in body
    stamp = body.split("app.css?v=")[1].split('"')[0]
    assert stamp.isdigit() and int(stamp) > 0


# ── Schema ───────────────────────────────────────────────

def test_the_schema_has_every_table_the_plan_needs(app):
    with connect(app.config["DATABASE"]) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "events", "races", "age_groups",
            "registrations", "entries", "settings"} <= names


def test_a_race_must_be_a_discipline_itc_actually_runs(app):
    conn = connect(app.config["DATABASE"])
    conn.execute("INSERT INTO events (slug,name,event_date,created_at,updated_at)"
                 " VALUES ('x','X','2026-11-01',?,?)", (now(), now()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO races (event_id,name,discipline,created_at,updated_at)"
                     " VALUES (1,'Fun','curling',?,?)", (now(), now()))
    conn.close()


def test_deleting_an_event_takes_its_races_with_it(app):
    """Races have no meaning without their event."""
    conn = connect(app.config["DATABASE"])
    conn.execute("INSERT INTO events (slug,name,event_date,created_at,updated_at)"
                 " VALUES ('x','X','2026-11-01',?,?)", (now(), now()))
    conn.execute("INSERT INTO races (event_id,name,discipline,created_at,updated_at)"
                 " VALUES (1,'Sprint','triathlon',?,?)", (now(), now()))
    conn.commit()
    conn.execute("DELETE FROM events WHERE event_id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM races").fetchone()[0] == 0
    conn.close()


def test_capacity_counts_confirmed_entries_only(app):
    """A cancelled entry frees its place; a waitlisted one never had one."""
    conn = connect(app.config["DATABASE"])
    conn.execute("INSERT INTO events (slug,name,event_date,created_at,updated_at)"
                 " VALUES ('x','X','2026-11-01',?,?)", (now(), now()))
    conn.execute("INSERT INTO races (event_id,name,discipline,capacity,created_at,updated_at)"
                 " VALUES (1,'Sprint','triathlon',2,?,?)", (now(), now()))
    conn.execute("INSERT INTO users (email,password_hash,full_name,created_at,updated_at)"
                 " VALUES ('a@b.c','x','A',?,?)", (now(), now()))
    conn.execute("INSERT INTO registrations (reference,user_id,event_id,created_at)"
                 " VALUES ('ITC-1',1,1,?)", (now(),))
    for n, status in enumerate(("confirmed", "cancelled", "waitlisted")):
        conn.execute(
            "INSERT INTO entries (registration_id,race_id,first_name,last_name,"
            "date_of_birth,gender,status,created_at,updated_at)"
            " VALUES (1,1,?,'Tester','1990-01-01','female',?,?,?)",
            (f"E{n}", status, now(), now()))
    conn.commit()
    assert race_taken(conn, 1) == 1
    conn.close()


def test_the_age_rule_is_a_setting_not_a_hard_coded_assumption(app):
    """World Triathlon counts age at 31 December, Ironman counts it on race
    day, and ITC has not said which. Phase 4 reads this setting."""
    conn = connect(app.config["DATABASE"])
    row = conn.execute("SELECT value FROM settings WHERE key='age_rule'").fetchone()
    assert row["value"] in ("dec31", "race_day")
    conn.close()


def test_races_carry_a_price_so_payments_need_no_migration(app):
    conn = connect(app.config["DATABASE"])
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(races)")}
    assert "price_bhd" in cols
    conn.close()
