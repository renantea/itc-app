"""Phase 2: what a visitor can see before they have an account.

The rules worth pinning down are about *not* showing things — a draft event
that leaks, or a finished race still inviting entries, are both worse than a
page that looks plain.
"""
from datetime import date, timedelta

import pytest

from itc.db import connect, now


def _event(app, slug, name, *, days, status="open", venue="Somewhere"):
    conn = connect(app.config["DATABASE"])
    stamp = now()
    eid = conn.execute(
        """INSERT INTO events (slug,name,summary,venue,event_date,status,
                created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)""",
        (slug, name, f"{name} summary", venue,
         (date.today() + timedelta(days=days)).isoformat(), status,
         stamp, stamp)).lastrowid
    conn.commit(); conn.close()
    return eid


def _race(app, event_id, name="Sprint", *, discipline="triathlon",
          capacity=None, swim="750 m", bike="20 km", run="5 km"):
    conn = connect(app.config["DATABASE"])
    stamp = now()
    rid = conn.execute(
        """INSERT INTO races (event_id,name,discipline,swim_distance,bike_distance,
                run_distance,capacity,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (event_id, name, discipline, swim, bike, run, capacity, stamp, stamp)).lastrowid
    conn.commit(); conn.close()
    return rid


def _fill(app, race_id, n, status="confirmed"):
    """Put n entries on a race, so capacity has something to count."""
    conn = connect(app.config["DATABASE"])
    stamp = now()
    conn.execute("INSERT OR IGNORE INTO users (user_id,email,password_hash,full_name,"
                 "created_at,updated_at) VALUES (1,'a@b.c','x','A',?,?)", (stamp, stamp))
    conn.execute("INSERT OR IGNORE INTO registrations (registration_id,reference,"
                 "user_id,event_id,created_at) VALUES (1,'ITC-1',1,1,?)", (stamp,))
    for i in range(n):
        conn.execute(
            """INSERT INTO entries (registration_id,race_id,first_name,last_name,
                    date_of_birth,gender,status,created_at,updated_at)
               VALUES (1,?,?,'T','1990-01-01','female',?,?,?)""",
            (race_id, f"E{i}", status, stamp, stamp))
    conn.commit(); conn.close()


# ── The listing ──────────────────────────────────────────

def test_an_open_event_appears_with_its_disciplines(client, app):
    eid = _event(app, "spring", "ITC Spring Triathlon", days=30)
    _race(app, eid, discipline="triathlon")
    _race(app, eid, "5K", discipline="running", swim=None, bike=None)
    body = client.get("/").get_data(as_text=True)
    assert "ITC Spring Triathlon" in body
    assert "Triathlon" in body and "Running" in body
    assert "2 races" in body


def test_a_draft_event_is_not_public(client, app):
    """Draft is ITC's workspace. Leaking one invites entries to a race that
    may never happen."""
    _event(app, "secret", "Unannounced Race", days=20, status="draft")
    assert "Unannounced Race" not in client.get("/").get_data(as_text=True)
    assert client.get("/events/secret").status_code == 404


def test_past_events_move_to_their_own_section(client, app):
    _event(app, "soon", "Coming Up Soon", days=10)
    _event(app, "gone", "Last Season", days=-20, status="completed")
    body = client.get("/").get_data(as_text=True)
    assert "Been and gone" in body
    # The finished one sits after the upcoming one, not mixed in with it.
    assert body.index("Coming Up Soon") < body.index("Been and gone") < body.index("Last Season")


def test_an_event_today_still_counts_as_upcoming(client, app):
    """Race day itself is not the past — someone checking the start time on the
    morning must still find the page where they expect it."""
    _event(app, "today", "Race Day Today", days=0)
    body = client.get("/").get_data(as_text=True)
    assert body.index("Race Day Today") < body.index("Been and gone") if "Been and gone" in body else True
    assert "Race Day Today" in body


# ── The detail page ──────────────────────────────────────

def test_the_detail_page_shows_only_the_legs_that_exist(client, app):
    """A 5K has no swim. Printing an empty Swim row would look like missing
    data rather than a run."""
    eid = _event(app, "run5k", "ITC Corniche 5K", days=12)
    _race(app, eid, "5K Run", discipline="running", swim=None, bike=None, run="5 km")
    body = client.get("/events/run5k").get_data(as_text=True)
    assert "5 km" in body
    assert ">Swim<" not in body and ">Bike<" not in body


def test_places_left_counts_confirmed_entries(client, app):
    eid = _event(app, "cap", "Capped Race", days=15)
    rid = _race(app, eid, capacity=10)
    _fill(app, rid, 4)
    _fill(app, rid, 2, status="cancelled")      # freed again
    body = client.get("/events/cap").get_data(as_text=True)
    assert "6 of 10 places left" in body


def test_a_full_race_says_so_and_offers_no_entry(client, app):
    eid = _event(app, "full", "Sold Out", days=15)
    rid = _race(app, eid, capacity=3)
    _fill(app, rid, 3)
    body = client.get("/events/full").get_data(as_text=True)
    assert "Full" in body and "Race full" in body
    assert f'href="/enter/{rid}"' not in body


def test_an_uncapped_race_does_not_invent_a_limit(client, app):
    eid = _event(app, "open", "Unlimited", days=15)
    _race(app, eid, capacity=None)
    body = client.get("/events/open").get_data(as_text=True)
    assert "No entry limit" in body
    assert "places left" not in body


def test_a_finished_event_does_not_invite_entries(client, app):
    eid = _event(app, "done", "Finished", days=-5, status="completed")
    rid = _race(app, eid)
    body = client.get("/events/done").get_data(as_text=True)
    assert "Entries closed" in body
    assert f'href="/enter/{rid}"' not in body


def test_an_open_race_links_into_the_entry_flow(client, app):
    """Phase 4 made this control real. A control that works must not be dressed
    as disabled, and the ones above must not look live."""
    eid = _event(app, "live", "Open Event", days=15)
    rid = _race(app, eid)
    body = client.get("/events/live").get_data(as_text=True)
    assert f'href="/enter/{rid}"' in body
    assert "Enter this race" in body
    assert 'aria-disabled="true"' not in body


def test_age_groups_are_listed_and_explained(client, app):
    eid = _event(app, "ages", "With Groups", days=15)
    rid = _race(app, eid)
    conn = connect(app.config["DATABASE"])
    conn.execute("INSERT INTO age_groups (race_id,label,gender,min_age,max_age,sort_order)"
                 " VALUES (?,'30-39','any',30,39,0)", (rid,))
    conn.commit(); conn.close()
    body = client.get("/events/ages").get_data(as_text=True)
    assert "30-39" in body
    # The page says the group is derived, which is the behaviour Phase 4 builds.
    assert "worked out from your date of birth" in body


def test_an_unknown_event_is_a_404(client):
    assert client.get("/events/no-such-race").status_code == 404
