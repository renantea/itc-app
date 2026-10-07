"""Phase 4: the registration flow.

The interesting tests are the refusals. An entry form that accepts a
nine-year-old into an adult race, lets two people take the last place, or
quietly loses the waiver, is worse than one that is hard to use — the mistake
only surfaces on race morning.
"""
import json
import re
from datetime import date, timedelta

import pytest

from itc import ages
from itc.db import connect, now

# ── Fixtures built straight into the database ────────────


def _event(app, *, days=30, status="open", slug="series", name="ITC Series"):
    conn = connect(app.config["DATABASE"])
    stamp = now()
    eid = conn.execute(
        """INSERT INTO events (slug,name,summary,venue,event_date,status,
                created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)""",
        (slug, name, "Summary", "Bahrain Bay",
         (date.today() + timedelta(days=days)).isoformat(), status, stamp, stamp)
    ).lastrowid
    conn.commit(); conn.close()
    return eid


def _race(app, event_id, *, name="Sprint", capacity=None, min_age=None,
          discipline="triathlon"):
    conn = connect(app.config["DATABASE"])
    stamp = now()
    rid = conn.execute(
        """INSERT INTO races (event_id,name,discipline,swim_distance,
                bike_distance,run_distance,capacity,min_age,created_at,updated_at)
           VALUES (?,?,?,'750 m','20 km','5 km',?,?,?,?)""",
        (event_id, name, discipline, capacity, min_age, stamp, stamp)).lastrowid
    conn.commit(); conn.close()
    return rid


def _bands(app, race_id, spans, gender="any"):
    """spans: [(label, min_age, max_age), ...]"""
    conn = connect(app.config["DATABASE"])
    for i, (label, low, high) in enumerate(spans):
        conn.execute(
            """INSERT INTO age_groups (race_id,label,gender,min_age,max_age,sort_order)
               VALUES (?,?,?,?,?,?)""", (race_id, label, gender, low, high, i))
    conn.commit(); conn.close()


def _occupy(app, race_id, n, *, status="confirmed", identity=None):
    """Entries made by somebody else — the state the flow has to cope with."""
    conn = connect(app.config["DATABASE"])
    stamp = now()
    uid = conn.execute(
        """INSERT INTO users (email,password_hash,full_name,created_at,updated_at)
           VALUES (?,'x','Someone Else',?,?)""",
        (f"filler{race_id}-{status}-{n}@example.com", stamp, stamp)).lastrowid
    event_id = conn.execute("SELECT event_id FROM races WHERE race_id = ?",
                            (race_id,)).fetchone()["event_id"]
    reg = conn.execute(
        """INSERT INTO registrations (reference,user_id,event_id,created_at)
           VALUES (?,?,?,?)""",
        (f"ITC-F{race_id}{status[:2]}{n}", uid, event_id, stamp)).lastrowid
    for i in range(n):
        first, last, dob = identity or (f"Filler{i}", "Person", "1990-01-01")
        conn.execute(
            """INSERT INTO entries (registration_id,race_id,first_name,last_name,
                    date_of_birth,gender,emergency_name,emergency_phone,status,
                    created_at,updated_at)
               VALUES (?,?,?,?,?,'female','ICE','123456',?,?,?)""",
            (reg, race_id, first, last, dob, status, stamp, stamp))
    conn.commit(); conn.close()


def _race_year(app, race_id) -> int:
    conn = connect(app.config["DATABASE"])
    row = conn.execute(
        """SELECT e.event_date FROM races r JOIN events e USING(event_id)
           WHERE r.race_id = ?""", (race_id,)).fetchone()
    conn.close()
    return int(row["event_date"][:4])


def _dob_for_age(app, race_id, age) -> str:
    """A date of birth that makes an athlete exactly `age` under the dec31 rule."""
    return f"{_race_year(app, race_id) - age}-06-01"


# ── Driving the flow ─────────────────────────────────────

def _csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _signin(client, email="parent@example.com", name="Mo Parent"):
    client.post("/register", data={
        "csrf_token": _csrf(client, "/register"), "full_name": name,
        "email": email, "password": "correct-horse-1", "phone": "",
    }, follow_redirects=True)


def _start(client, race_id, size=1):
    return client.post(f"/enter/{race_id}", data={
        "csrf_token": _csrf(client, f"/enter/{race_id}"), "party_size": size})


def _athlete(client, n=1, **over):
    data = {
        "first_name": "Ada", "last_name": "Lovelace",
        "date_of_birth": "1990-06-01", "gender": "female",
        "emergency_name": "Mo Parent", "emergency_phone": "+973 3312 3456",
        "email": "", "phone": "", "club": "", "shirt_size": "",
        "medical_notes": "", "guardian_name": "", "waiver": "on",
    }
    data.update(over)
    data = {k: v for k, v in data.items() if v is not None}
    data["csrf_token"] = _csrf(client, f"/enter/athlete/{n}")
    return client.post(f"/enter/athlete/{n}", data=data)


def _confirm(client, token=None):
    """`token` is for the cases where the review page no longer renders —
    a race that closed mid-entry still has to refuse a posted confirm."""
    return client.post("/enter/confirm", data={
        "csrf_token": token or _csrf(client, "/enter/review")},
        follow_redirects=True)


def _rows(app, sql, params=()):
    conn = connect(app.config["DATABASE"])
    out = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return out


# ── Age derivation (no HTTP involved) ────────────────────

def test_dec31_ignores_the_day_and_month():
    """World Triathlon: everyone born in a year races the same category."""
    assert ages.age_on("1990-12-31", "2026-03-01", "dec31") == 36
    assert ages.age_on("1990-01-01", "2026-03-01", "dec31") == 36


def test_race_day_counts_the_birthday():
    """Ironman: the day before you turn 40 you are still 39."""
    assert ages.age_on("1986-06-02", "2026-06-01", "race_day") == 39
    assert ages.age_on("1986-06-01", "2026-06-01", "race_day") == 40


def test_an_open_ended_band_stays_open():
    assert ages.covers({"min_age": 60, "max_age": None}, 81) is True
    assert ages.covers({"min_age": None, "max_age": 15}, 3) is True
    assert ages.covers({"min_age": 20, "max_age": 24}, 25) is False


def test_gender_breaks_the_tie_but_age_decides():
    bands = [
        {"age_group_id": 1, "label": "F30-34", "gender": "female", "min_age": 30, "max_age": 34},
        {"age_group_id": 2, "label": "M30-34", "gender": "male", "min_age": 30, "max_age": 34},
    ]
    assert ages.match_group(bands, 32, "male")["label"] == "M30-34"
    assert ages.match_group(bands, 32, "female")["label"] == "F30-34"
    # No band for the age at all is a real eligibility answer.
    assert ages.match_group(bands, 12, "female") is None


def test_an_unanticipated_gender_still_gets_a_band():
    """Refusing the entry would be the worse bug; an organiser can move them."""
    bands = [
        {"age_group_id": 1, "label": "F30-34", "gender": "female", "min_age": 30, "max_age": 34},
        {"age_group_id": 2, "label": "M30-34", "gender": "male", "min_age": 30, "max_age": 34},
    ]
    assert ages.match_group(bands, 32, "other") is not None


def test_an_any_gender_band_is_preferred_over_a_mismatch():
    bands = [
        {"age_group_id": 1, "label": "M30-34", "gender": "male", "min_age": 30, "max_age": 34},
        {"age_group_id": 2, "label": "Open 30-34", "gender": "any", "min_age": 30, "max_age": 34},
    ]
    assert ages.match_group(bands, 31, "other")["label"] == "Open 30-34"


# ── Getting in ───────────────────────────────────────────

def test_entry_needs_an_account_and_comes_back_to_the_race(client, app):
    rid = _race(app, _event(app))
    r = client.get(f"/enter/{rid}")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert f"enter%2F{rid}" in r.headers["Location"] or f"/enter/{rid}" in r.headers["Location"]


def test_a_closed_event_refuses_the_flow(client, app):
    rid = _race(app, _event(app, status="closed"))
    _signin(client)
    r = client.get(f"/enter/{rid}", follow_redirects=True)
    assert "are not open" in r.get_data(as_text=True)


def test_a_past_event_refuses_the_flow(client, app):
    rid = _race(app, _event(app, days=-5))
    _signin(client)
    body = client.get(f"/enter/{rid}", follow_redirects=True).get_data(as_text=True)
    assert "already taken place" in body


def test_a_full_race_refuses_before_anything_is_typed(client, app):
    rid = _race(app, _event(app), capacity=2)
    _occupy(app, rid, 2)
    _signin(client)
    body = client.get(f"/enter/{rid}", follow_redirects=True).get_data(as_text=True)
    assert "is full" in body


def test_the_party_cannot_exceed_the_places_left(client, app):
    rid = _race(app, _event(app), capacity=3)
    _occupy(app, rid, 1)
    _signin(client)
    # Two places remain, so the form offers two and refuses three.
    assert b"3 athletes" not in client.get(f"/enter/{rid}").data
    r = _start(client, rid, 3)
    assert r.status_code == 400
    assert "between 1 and 2" in r.get_data(as_text=True)


def test_a_nonsense_party_size_is_refused(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    assert _start(client, rid, 0).status_code == 400
    assert _start(client, rid, "many").status_code == 400


# ── One athlete at a time ────────────────────────────────

def test_the_athlete_form_names_everything_that_is_missing(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    r = _athlete(client, 1, first_name="", date_of_birth="", gender="",
                 emergency_phone="", waiver=None)
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "first name" in body
    assert "date of birth" in body
    assert "emergency contact number" in body
    assert "waiver" in body


def test_a_future_date_of_birth_is_refused(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    body = _athlete(client, 1, date_of_birth=tomorrow).get_data(as_text=True)
    assert "in the future" in body


def test_a_mistyped_birth_year_is_called_a_typo(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    body = _athlete(client, 1, date_of_birth="1066-06-01").get_data(as_text=True)
    assert "looks like a typo" in body


def test_a_race_minimum_age_is_enforced(client, app):
    rid = _race(app, _event(app), min_age=16)
    _signin(client)
    _start(client, rid, 1)
    body = _athlete(client, 1, date_of_birth=_dob_for_age(app, rid, 9)
                    ).get_data(as_text=True)
    assert "ages 16 and over" in body


def test_an_age_with_no_band_is_refused_and_the_bands_are_named(client, app):
    rid = _race(app, _event(app))
    _bands(app, rid, [("20-29", 20, 29), ("30+", 30, None)])
    _signin(client)
    _start(client, rid, 1)
    body = _athlete(client, 1, date_of_birth=_dob_for_age(app, rid, 9)
                    ).get_data(as_text=True)
    assert "no age group for a 9-year-old" in body
    assert "20–29, 30+" in body


def test_an_under_18_needs_a_named_guardian(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    young = {"date_of_birth": _dob_for_age(app, rid, 12), "first_name": "Sam"}
    body = _athlete(client, 1, **young).get_data(as_text=True)
    assert "parent or guardian must be named" in body
    assert _athlete(client, 1, guardian_name="Mo Parent",
                    **young).status_code == 302


def test_each_athlete_gets_their_own_screen_in_order(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 3)
    r = _athlete(client, 1)
    assert r.headers["Location"].endswith("/enter/athlete/2")
    r = _athlete(client, 2, first_name="Bee", last_name="Second")
    assert r.headers["Location"].endswith("/enter/athlete/3")
    r = _athlete(client, 3, first_name="Cee", last_name="Third")
    assert r.headers["Location"].endswith("/enter/review")


def test_a_saved_athlete_comes_back_filled_in(client, app):
    """A dropped connection on screen three must not cost screens one and two."""
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 2)
    _athlete(client, 1, club="Bahrain Road Runners")
    body = client.get("/enter/athlete/1").get_data(as_text=True)
    assert "Bahrain Road Runners" in body
    assert "Lovelace" in body


def test_review_sends_you_back_to_the_athlete_who_is_incomplete(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 2)
    _athlete(client, 1)
    r = client.get("/enter/review")
    assert r.headers["Location"].endswith("/enter/athlete/2")


def test_the_review_shows_the_derived_category_not_a_choice(client, app):
    rid = _race(app, _event(app))
    _bands(app, rid, [("30-34", 30, 34), ("35-39", 35, 39)])
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1, date_of_birth=_dob_for_age(app, rid, 36))
    body = client.get("/enter/review").get_data(as_text=True)
    assert "35-39" in body
    assert "Age 36" in body


# ── Confirming ───────────────────────────────────────────

def test_a_family_of_four_is_one_registration_and_four_entries(client, app):
    """Phase 4's definition of done."""
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 4)
    _athlete(client, 1, first_name="Mo", last_name="Parent")
    _athlete(client, 2, first_name="Ali", last_name="Parent",
             date_of_birth=_dob_for_age(app, rid, 14), guardian_name="Mo Parent")
    _athlete(client, 3, first_name="Noor", last_name="Parent",
             date_of_birth=_dob_for_age(app, rid, 11), guardian_name="Mo Parent")
    _athlete(client, 4, first_name="Zain", last_name="Parent")
    body = _confirm(client).get_data(as_text=True)

    assert len(_rows(app, "SELECT * FROM registrations")) == 1
    entries = _rows(app, "SELECT * FROM entries ORDER BY entry_id")
    assert len(entries) == 4
    assert {e["status"] for e in entries} == {"confirmed"}
    assert "Entry confirmed" in body or "You're in" in body
    # Guardians are stored per entry, and only where they are needed.
    assert [e["guardian_name"] for e in entries] == [None, "Mo Parent", "Mo Parent", None]


def test_confirming_stores_the_waiver_moment_per_entry(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    _confirm(client)
    entry = _rows(app, "SELECT * FROM entries")[0]
    assert entry["waiver_accepted_at"]
    assert entry["waiver_accepted_at"].endswith("Z")


def test_the_waiver_records_when_it_was_accepted_not_when_it_was_written(client, app):
    """The question after an incident is when *this person* agreed. Re-stamping
    at confirm would quietly answer a different one."""
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    accepted = json.loads(
        _rows(app, "SELECT payload FROM entry_drafts")[0]["payload"]
    )["1"]["waiver_accepted_at"]
    _confirm(client)
    entry = _rows(app, "SELECT * FROM entries")[0]
    assert entry["waiver_accepted_at"] == accepted


def test_the_reference_is_readable_and_unique(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    seen = set()
    for i in range(3):
        _start(client, rid, 1)
        _athlete(client, 1, first_name=f"Ada{i}")
        _confirm(client)
    for reg in _rows(app, "SELECT reference FROM registrations"):
        assert re.fullmatch(r"ITC-[ACDEFGHJKLMNPQRTUVWXY34679]{6}", reg["reference"])
        seen.add(reg["reference"])
    assert len(seen) == 3


def test_the_draft_is_gone_once_it_is_confirmed(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    _confirm(client)
    assert _rows(app, "SELECT * FROM entry_drafts") == []


def test_the_same_athlete_cannot_be_entered_twice_in_one_order(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 2)
    _athlete(client, 1)
    _athlete(client, 2)          # identical details — a mis-tapped Back button
    body = _confirm(client).get_data(as_text=True)
    assert "is listed twice" in body
    assert _rows(app, "SELECT * FROM entries") == []


def test_an_athlete_already_on_the_start_line_is_refused(client, app):
    rid = _race(app, _event(app))
    _occupy(app, rid, 1, identity=("Ada", "Lovelace", "1990-06-01"))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    body = _confirm(client).get_data(as_text=True)
    assert "already entered" in body
    assert len(_rows(app, "SELECT * FROM entries")) == 1


def test_a_cancelled_entry_does_not_block_entering_again(client, app):
    """A withdrawal frees the place and the name."""
    rid = _race(app, _event(app))
    _occupy(app, rid, 1, status="cancelled",
            identity=("Ada", "Lovelace", "1990-06-01"))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    _confirm(client)
    assert len(_rows(app, "SELECT * FROM entries WHERE status='confirmed'")) == 1


def test_places_taken_during_the_form_are_not_oversold(client, app):
    """The whole reason capacity is checked inside the write transaction."""
    rid = _race(app, _event(app), capacity=2)
    _signin(client)
    _start(client, rid, 2)
    _athlete(client, 1, first_name="Ada")
    _athlete(client, 2, first_name="Bee")
    # Somebody else enters while this review page is on screen.
    _occupy(app, rid, 1)
    body = _confirm(client).get_data(as_text=True)
    assert "dropped to 1 place" in body
    assert len(_rows(app, "SELECT * FROM entries")) == 1


def test_an_event_that_closes_mid_entry_is_not_written(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    token = _csrf(client, "/enter/review")      # taken while it still renders
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE events SET status = 'closed'")
    conn.commit(); conn.close()
    body = _confirm(client, token).get_data(as_text=True)
    assert "are not open" in body
    assert _rows(app, "SELECT * FROM entries") == []


# ── Whose data is it ─────────────────────────────────────

def test_a_draft_token_is_useless_to_another_account(app):
    rid = _race(app, _event(app))
    owner = app.test_client()
    _signin(owner, "owner@example.com")
    _start(owner, rid, 1)
    _athlete(owner, 1, medical_notes="Asthma")
    token = _rows(app, "SELECT token FROM entry_drafts")[0]["token"]

    thief = app.test_client()
    _signin(thief, "thief@example.com")
    with thief.session_transaction() as s:
        s["draft"] = token
    r = thief.get("/enter/athlete/1", follow_redirects=True)
    assert "Asthma" not in r.get_data(as_text=True)


def test_a_confirmation_is_only_visible_to_the_account_that_entered(app):
    rid = _race(app, _event(app))
    owner = app.test_client()
    _signin(owner, "owner@example.com")
    _start(owner, rid, 1)
    _athlete(owner, 1)
    _confirm(owner)
    reference = _rows(app, "SELECT reference FROM registrations")[0]["reference"]

    assert owner.get(f"/enter/done/{reference}").status_code == 200
    other = app.test_client()
    _signin(other, "nosy@example.com")
    assert other.get(f"/enter/done/{reference}").status_code == 403


def test_the_confirmation_shows_the_reference_and_the_athletes(app):
    rid = _race(app, _event(app))
    _bands(app, rid, [("30-34", 30, 34), ("35-39", 35, 39)])
    client = app.test_client()
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1, date_of_birth=_dob_for_age(app, rid, 33))
    body = _confirm(client).get_data(as_text=True)
    reference = _rows(app, "SELECT reference FROM registrations")[0]["reference"]
    assert reference in body
    assert "Ada Lovelace" in body
    assert "30-34" in body
