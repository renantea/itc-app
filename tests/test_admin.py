"""Phase 6: the organiser's side.

The guarantee this phase has to earn is that ITC can run an event without
opening the database — and that the two destructive things here (deleting, and
rewriting age bands) cannot quietly damage a start list.
"""
from datetime import date, timedelta

import pytest

from itc.admin import bands_to_text, parse_bands
from itc.db import connect
from test_entry import (_athlete, _bands, _confirm, _csrf, _event, _race,
                        _rows, _signin, _start)


def _admin(app, email="organiser@example.com"):
    """A signed-in organiser. Promotion happens out of band on purpose —
    no web form may mint one."""
    client = app.test_client()
    _signin(client, email, name="Liam Organiser")
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET role='admin' WHERE email=?", (email,))
    conn.commit(); conn.close()
    return client


def _post(client, path, **data):
    data["csrf_token"] = _csrf(client, "/account")
    return client.post(path, data=data, follow_redirects=True)


def _enter_one(app, rid, **over):
    """One real entry made through the public flow."""
    client = app.test_client()
    _signin(client, "parent@example.com")
    _start(client, rid, 1)
    _athlete(client, 1, **over)
    _confirm(client)
    return client


# ── Who may be in here ───────────────────────────────────

def test_the_organiser_area_needs_an_account(client):
    r = client.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_an_athlete_is_not_an_organiser(client, app):
    _signin(client)
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/events/new").status_code == 403


def test_an_organiser_sees_the_dashboard(app):
    admin = _admin(app)
    _event(app, name="ITC Spring Triathlon")
    body = admin.get("/admin").get_data(as_text=True)
    assert "ITC Spring Triathlon" in body
    assert "Organiser" in body


# ── Building an event ────────────────────────────────────

def test_an_event_can_be_created_and_gets_a_slug(app):
    admin = _admin(app)
    when = (date.today() + timedelta(days=40)).isoformat()
    _post(admin, "/admin/events/new", name="ITC Corniche 5K", event_date=when,
          venue="Corniche", status="open", summary="", description="",
          reg_opens_at="", reg_closes_at="")
    events = _rows(app, "SELECT * FROM events")
    assert len(events) == 1
    assert events[0]["slug"] == "itc-corniche-5k"
    assert events[0]["status"] == "open"


def test_two_events_with_the_same_name_get_different_slugs(app):
    admin = _admin(app)
    when = (date.today() + timedelta(days=40)).isoformat()
    for _ in range(2):
        _post(admin, "/admin/events/new", name="ITC Duathlon", event_date=when,
              status="draft", venue="", summary="", description="",
              reg_opens_at="", reg_closes_at="")
    slugs = sorted(e["slug"] for e in _rows(app, "SELECT slug FROM events"))
    assert slugs == ["itc-duathlon", "itc-duathlon-2"]


def test_an_event_without_a_date_is_refused(app):
    admin = _admin(app)
    r = admin.post("/admin/events/new", data={
        "csrf_token": _csrf(admin, "/account"), "name": "No Date",
        "event_date": "", "status": "draft"})
    assert r.status_code == 400
    assert "Give the race date" in r.get_data(as_text=True)
    assert _rows(app, "SELECT * FROM events") == []


def test_a_registration_window_that_makes_no_sense_is_refused(app):
    admin = _admin(app)
    race_day = (date.today() + timedelta(days=40)).isoformat()
    r = admin.post("/admin/events/new", data={
        "csrf_token": _csrf(admin, "/account"), "name": "Backwards",
        "event_date": race_day, "status": "draft",
        "reg_opens_at": (date.today() + timedelta(days=30)).isoformat(),
        "reg_closes_at": (date.today() + timedelta(days=10)).isoformat()})
    assert r.status_code == 400
    assert "close before it opened" in r.get_data(as_text=True)


def test_registration_cannot_close_after_the_race_is_run(app):
    admin = _admin(app)
    r = admin.post("/admin/events/new", data={
        "csrf_token": _csrf(admin, "/account"), "name": "Too Late",
        "event_date": (date.today() + timedelta(days=10)).isoformat(),
        "status": "draft", "reg_opens_at": "",
        "reg_closes_at": (date.today() + timedelta(days=20)).isoformat()})
    assert r.status_code == 400
    assert "after the race has been run" in r.get_data(as_text=True)


def test_an_event_with_entries_cannot_be_deleted(app):
    """The cascade would take real entries with it."""
    eid = _event(app)
    rid = _race(app, eid)
    _enter_one(app, rid)
    admin = _admin(app)
    body = _post(admin, f"/admin/events/{eid}/delete").get_data(as_text=True)
    # Jinja escapes the apostrophe in "can't", so assert on copy without one.
    assert "Set it to closed instead" in body
    assert len(_rows(app, "SELECT * FROM events")) == 1
    assert len(_rows(app, "SELECT * FROM entries")) == 1


def test_an_empty_event_can_be_deleted(app):
    eid = _event(app)
    _race(app, eid)
    admin = _admin(app)
    _post(admin, f"/admin/events/{eid}/delete")
    assert _rows(app, "SELECT * FROM events") == []
    assert _rows(app, "SELECT * FROM races") == []


# ── Races ────────────────────────────────────────────────

def test_a_race_can_be_added_to_an_event(app):
    eid = _event(app)
    admin = _admin(app)
    _post(admin, f"/admin/events/{eid}/races/new", name="Sprint Triathlon",
          discipline="triathlon", swim_distance="750 m", bike_distance="20 km",
          run_distance="5 km", start_time="07:30", capacity="150",
          min_age="16", price_bhd="0", sort_order="1")
    races = _rows(app, "SELECT * FROM races")
    assert len(races) == 1 and races[0]["capacity"] == 150
    assert races[0]["min_age"] == 16


def test_a_capacity_of_zero_is_refused_because_it_means_nobody(app):
    eid = _event(app)
    admin = _admin(app)
    r = admin.post(f"/admin/events/{eid}/races/new", data={
        "csrf_token": _csrf(admin, "/account"), "name": "Nobody",
        "discipline": "running", "capacity": "0", "price_bhd": "0"})
    assert r.status_code == 400
    assert "nobody may enter" in r.get_data(as_text=True)


def test_a_discipline_itc_does_not_run_is_refused(app):
    eid = _event(app)
    admin = _admin(app)
    r = admin.post(f"/admin/events/{eid}/races/new", data={
        "csrf_token": _csrf(admin, "/account"), "name": "Curling",
        "discipline": "curling", "price_bhd": "0"})
    assert r.status_code == 400
    assert _rows(app, "SELECT * FROM races") == []


def test_a_race_with_entries_cannot_be_deleted(app):
    rid = _race(app, _event(app))
    _enter_one(app, rid)
    admin = _admin(app)
    body = _post(admin, f"/admin/races/{rid}/delete").get_data(as_text=True)
    assert "1 entries, so it" in body          # "can't" is escaped by Jinja
    assert len(_rows(app, "SELECT * FROM races")) == 1


# ── Age bands ────────────────────────────────────────────

def test_bands_parse_open_ends_and_default_gender():
    rows, errors = parse_bands("Junior 13-15 | 13 | 15\n60+ | 60 |\nOpen||")
    assert errors == []
    assert rows[0] == {"label": "Junior 13-15", "min_age": 13, "max_age": 15,
                       "gender": "any"}
    assert rows[1]["max_age"] is None
    assert rows[2] == {"label": "Open", "min_age": None, "max_age": None,
                       "gender": "any"}


def test_bands_report_what_is_wrong_and_on_which_line():
    rows, errors = parse_bands("M30-34 | thirty | 34 | male\n | 20 | 29\n"
                               "Backwards | 40 | 30\nX | 1 | 2 | martian")
    assert any("line 1" in e.lower() for e in errors)
    assert any("every band needs a label" in e for e in errors)
    assert any("starts after it ends" in e for e in errors)
    assert any("any, male or female" in e for e in errors)


def test_bands_ignore_blank_lines_and_comments():
    rows, errors = parse_bands("# the junior series\n\nTri-Star | 8 | 9\n\n")
    assert errors == [] and len(rows) == 1


def test_bands_round_trip_through_the_textarea():
    text = "M30-34 | 30 | 34 | male\n60+ | 60 |  | any"
    rows, errors = parse_bands(text)
    assert errors == []
    assert parse_bands(bands_to_text(rows))[0] == rows


def test_rewriting_the_bands_recomputes_the_entries(app):
    """The reason every entry carries a date of birth next to its category."""
    eid = _event(app, days=40)
    rid = _race(app, eid)
    year = int(_rows(app, "SELECT event_date FROM events")[0]["event_date"][:4])
    _bands(app, rid, [("Wide 20-60", 20, 60)])
    _enter_one(app, rid, date_of_birth=f"{year - 36}-06-01")
    assert _rows(app, """SELECT ag.label FROM entries en
                         JOIN age_groups ag USING(age_group_id)""")[0]["label"] \
        == "Wide 20-60"

    admin = _admin(app)
    _post(admin, f"/admin/races/{rid}", name="Sprint", discipline="triathlon",
          price_bhd="0", sort_order="0",
          bands="30-34 | 30 | 34\n35-39 | 35 | 39\n40+ | 40 |")
    label = _rows(app, """SELECT ag.label FROM entries en
                          JOIN age_groups ag USING(age_group_id)""")[0]["label"]
    assert label == "35-39"


def test_an_entry_with_no_matching_band_is_left_without_one_not_wrongly_filed(app):
    eid = _event(app, days=40)
    rid = _race(app, eid)
    year = int(_rows(app, "SELECT event_date FROM events")[0]["event_date"][:4])
    _bands(app, rid, [("Wide 10-60", 10, 60)])
    _enter_one(app, rid, date_of_birth=f"{year - 11}-06-01",
               guardian_name="A Parent")

    admin = _admin(app)
    _post(admin, f"/admin/races/{rid}", name="Sprint", discipline="triathlon",
          price_bhd="0", sort_order="0", bands="Seniors | 20 | 60")
    entry = _rows(app, "SELECT age_group_id FROM entries")[0]
    assert entry["age_group_id"] is None


# ── Entrants, filters and the export ─────────────────────

def _event_with_two_races(app):
    eid = _event(app, days=40)
    tri = _race(app, eid, name="Sprint Triathlon")
    run = _race(app, eid, name="5K Run", discipline="running")
    _enter_one(app, tri, first_name="Ada", last_name="Lovelace", club="ITC")
    client = app.test_client()
    _signin(client, "second@example.com")
    _start(client, run, 1)
    _athlete(client, 1, first_name="Bea", last_name="Running")
    _confirm(client)
    return eid, tri, run


def test_entrants_lists_everyone_on_the_event(app):
    eid, _, _ = _event_with_two_races(app)
    body = _admin(app).get(f"/admin/events/{eid}/entrants").get_data(as_text=True)
    assert "Lovelace, Ada" in body and "Running, Bea" in body


def test_entrants_can_be_filtered_to_one_race(app):
    eid, tri, _ = _event_with_two_races(app)
    body = _admin(app).get(
        f"/admin/events/{eid}/entrants?race={tri}").get_data(as_text=True)
    assert "Lovelace" in body and "Running, Bea" not in body


def test_entrants_can_be_searched_by_name_club_or_reference(app):
    eid, _, _ = _event_with_two_races(app)
    admin = _admin(app)
    assert "Lovelace" in admin.get(
        f"/admin/events/{eid}/entrants?q=lovel").get_data(as_text=True)
    # Every reference begins "ITC-", so searching the club "ITC" must not drag
    # the whole event back.
    body = admin.get(f"/admin/events/{eid}/entrants?q=itc").get_data(as_text=True)
    assert "Lovelace" in body and "Running, Bea" not in body
    # Ask for *Ada's* reference by name. An unordered SELECT comes back in
    # whatever order SQLite finds cheapest — here the index on `reference`,
    # which is alphabetical, not insertion order.
    reference = _rows(app, """SELECT g.reference FROM registrations g
                              JOIN entries en USING(registration_id)
                              WHERE en.last_name = 'Lovelace'""")[0]["reference"]
    assert "Lovelace" in admin.get(
        f"/admin/events/{eid}/entrants?q={reference}").get_data(as_text=True)
    # ...and the bare code works too, for someone reading it off a phone.
    assert "Lovelace" in admin.get(
        f"/admin/events/{eid}/entrants?q={reference[4:]}").get_data(as_text=True)


def test_the_csv_carries_what_a_timing_company_needs(app):
    eid, _, _ = _event_with_two_races(app)
    r = _admin(app).get(f"/admin/events/{eid}/entrants.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    assert "attachment" in r.headers["Content-Disposition"]
    assert ".csv" in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    # Excel mangles accented names without the BOM, and someone retypes the
    # whole start list by hand.
    assert body.startswith("﻿")
    header = body.splitlines()[0]
    for column in ("bib", "race", "first_name", "last_name", "date_of_birth",
                   "age", "age_group", "emergency_phone", "status", "reference"):
        assert column in header
    assert "Lovelace" in body and "Bea" in body


def test_the_csv_respects_the_filters(app):
    eid, tri, _ = _event_with_two_races(app)
    body = _admin(app).get(
        f"/admin/events/{eid}/entrants.csv?race={tri}").get_data(as_text=True)
    assert "Lovelace" in body and "Bea" not in body


# ── Bibs ─────────────────────────────────────────────────

def test_bibs_are_assigned_in_surname_order_with_a_prefix(app):
    eid = _event(app, days=40)
    rid = _race(app, eid)
    _enter_one(app, rid, first_name="Zoe", last_name="Zephyr")
    client = app.test_client()
    _signin(client, "other@example.com")
    _start(client, rid, 1)
    _athlete(client, 1, first_name="Amy", last_name="Able")
    _confirm(client)

    admin = _admin(app)
    _post(admin, f"/admin/races/{rid}/bibs", prefix="J", start="100")
    bibs = {r["last_name"]: r["bib_number"]
            for r in _rows(app, "SELECT last_name, bib_number FROM entries")}
    assert bibs == {"Able": "J100", "Zephyr": "J101"}


def test_bibs_skip_withdrawn_entries(app):
    """A withdrawn athlete holding bib 14 leaves a gap nobody can explain."""
    eid = _event(app, days=40)
    rid = _race(app, eid)
    _enter_one(app, rid, first_name="Amy", last_name="Able")
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE entries SET status='cancelled'")
    conn.commit(); conn.close()

    admin = _admin(app)
    _post(admin, f"/admin/races/{rid}/bibs", start="1")
    assert _rows(app, "SELECT bib_number FROM entries")[0]["bib_number"] is None


def test_existing_bibs_are_kept_unless_a_renumber_is_asked_for(app):
    """Bibs get printed; a silent renumber invalidates the print run."""
    eid = _event(app, days=40)
    rid = _race(app, eid)
    _enter_one(app, rid, first_name="Amy", last_name="Able")
    admin = _admin(app)
    _post(admin, f"/admin/races/{rid}/bibs", start="7")
    assert _rows(app, "SELECT bib_number FROM entries")[0]["bib_number"] == "7"

    _post(admin, f"/admin/races/{rid}/bibs", start="50")
    assert _rows(app, "SELECT bib_number FROM entries")[0]["bib_number"] == "7"

    _post(admin, f"/admin/races/{rid}/bibs", start="50", overwrite="1")
    assert _rows(app, "SELECT bib_number FROM entries")[0]["bib_number"] == "50"


# ── Fixing one entry ─────────────────────────────────────

def test_an_organiser_can_set_a_bib_and_a_status(app):
    rid = _race(app, _event(app, days=40))
    _enter_one(app, rid)
    entry_id = _rows(app, "SELECT entry_id FROM entries")[0]["entry_id"]
    admin = _admin(app)
    _post(admin, f"/admin/entries/{entry_id}", bib_number="A12",
          status="waitlisted")
    row = _rows(app, "SELECT * FROM entries")[0]
    assert row["bib_number"] == "A12" and row["status"] == "waitlisted"


def test_confirming_into_a_full_race_is_refused(app):
    """Otherwise clearing a waitlist quietly oversubscribes the race."""
    eid = _event(app, days=40)
    rid = _race(app, eid, capacity=1)
    _enter_one(app, rid, first_name="Amy", last_name="Able")
    conn = connect(app.config["DATABASE"])
    # A second athlete, waiting.
    conn.execute("""INSERT INTO entries (registration_id,race_id,first_name,
                        last_name,date_of_birth,gender,emergency_name,
                        emergency_phone,status,created_at,updated_at)
                    SELECT registration_id,race_id,'Bea','Waiting','1990-01-01',
                        'female','ICE','123456','waitlisted',created_at,updated_at
                      FROM entries LIMIT 1""")
    conn.commit(); conn.close()
    waiting = [r for r in _rows(app, "SELECT * FROM entries")
               if r["status"] == "waitlisted"][0]

    admin = _admin(app)
    body = _post(admin, f"/admin/entries/{waiting['entry_id']}",
                 status="confirmed", bib_number="").get_data(as_text=True)
    assert "is full" in body
    assert [r for r in _rows(app, "SELECT * FROM entries")
            if r["entry_id"] == waiting["entry_id"]][0]["status"] == "waitlisted"


def test_cancelling_from_the_organiser_side_stamps_when(app):
    rid = _race(app, _event(app, days=40))
    _enter_one(app, rid)
    entry_id = _rows(app, "SELECT entry_id FROM entries")[0]["entry_id"]
    _post(_admin(app), f"/admin/entries/{entry_id}", status="cancelled",
          bib_number="")
    row = _rows(app, "SELECT * FROM entries")[0]
    assert row["status"] == "cancelled" and row["cancelled_at"]
