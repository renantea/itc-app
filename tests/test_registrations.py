"""Phase 5: proof of entry, and getting back out of it.

Withdrawal is where this phase can do damage: it frees a place, so the tests
care about who may do it, what it leaves behind, and whether the place really
comes back.
"""
from datetime import date, timedelta

import pytest

from itc import mail
from itc.db import connect
# The Phase 4 helpers drive the entry flow; reusing them means these tests
# exercise real registrations rather than hand-built rows.
from test_entry import (_athlete, _bands, _confirm, _csrf, _event, _occupy,
                        _race, _rows, _signin, _start)


def _enter(client, app, rid, athletes=1, **first):
    """Run one account through the whole flow and return the reference."""
    _start(client, rid, athletes)
    for n in range(1, athletes + 1):
        over = dict(first) if n == 1 else {"first_name": f"Athlete{n}",
                                          "last_name": "Family"}
        _athlete(client, n, **over)
    _confirm(client)
    return _rows(app, "SELECT reference FROM registrations ORDER BY registration_id DESC")[0]["reference"]


def _entry_ids(app, reference):
    return [r["entry_id"] for r in _rows(
        app, """SELECT en.entry_id FROM entries en
                JOIN registrations g USING(registration_id)
                WHERE g.reference = ? ORDER BY en.entry_id""", (reference,))]


def _withdraw(client, reference, entry_id):
    """The token comes from /account, not from the withdraw page: that page
    deliberately redirects when there is nothing left to withdraw, and the
    header's sign-out form carries a token on every signed-in page anyway."""
    return client.post(
        f"/my-races/{reference}/withdraw/{entry_id}",
        data={"csrf_token": _csrf(client, "/account")}, follow_redirects=True)


# ── The list ─────────────────────────────────────────────

def test_my_races_needs_an_account(client):
    r = client.get("/my-races")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_an_empty_account_says_so_rather_than_showing_a_blank_page(client, app):
    _signin(client)
    body = client.get("/my-races").get_data(as_text=True)
    assert "Nothing entered yet" in body


def test_an_entry_appears_under_its_reference(client, app):
    rid = _race(app, _event(app, name="ITC Corniche 5K"))
    _signin(client)
    reference = _enter(client, app, rid, 2)
    body = client.get("/my-races").get_data(as_text=True)
    assert reference in body
    assert "ITC Corniche 5K" in body
    assert "Ada Lovelace" in body and "Athlete2 Family" in body


def test_past_and_upcoming_races_are_kept_apart(client, app):
    soon = _race(app, _event(app, days=20, slug="soon", name="Next One"))
    _signin(client)
    _enter(client, app, soon)
    # Move the event into the past; it must drop out of "upcoming".
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE events SET event_date = ?",
                 ((date.today() - timedelta(days=3)).isoformat(),))
    conn.commit(); conn.close()
    body = client.get("/my-races").get_data(as_text=True)
    assert "Been and done" in body


def test_one_account_cannot_read_anothers_entry(app):
    rid = _race(app, _event(app))
    owner = app.test_client()
    _signin(owner, "owner@example.com")
    reference = _enter(owner, app, rid, 1, medical_notes="Asthma")

    nosy = app.test_client()
    _signin(nosy, "nosy@example.com")
    r = nosy.get(f"/my-races/{reference}")
    # 404, not 403 — whether that reference exists is not their business.
    assert r.status_code == 404
    assert "Asthma" not in r.get_data(as_text=True)


# ── Withdrawing ──────────────────────────────────────────

def test_withdrawing_asks_before_it_acts(client, app):
    """A confirm() dialog would be an inline handler and the CSP blocks those,
    so the question has to be its own page."""
    rid = _race(app, _event(app))
    _signin(client)
    reference = _enter(client, app, rid)
    entry_id = _entry_ids(app, reference)[0]
    body = client.get(f"/my-races/{reference}/withdraw/{entry_id}").get_data(as_text=True)
    assert "Yes, withdraw Ada" in body
    assert "Keep the entry" in body
    # Asking must not have changed anything.
    assert _rows(app, "SELECT status FROM entries")[0]["status"] == "confirmed"


def test_withdrawing_frees_the_place(client, app):
    rid = _race(app, _event(app), capacity=10)
    _signin(client)
    reference = _enter(client, app, rid, 2)
    assert _rows(app, "SELECT COUNT(*) n FROM entries WHERE status='confirmed'")[0]["n"] == 2

    _withdraw(client, reference, _entry_ids(app, reference)[0])
    rows = _rows(app, "SELECT status, cancelled_at FROM entries ORDER BY entry_id")
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["cancelled_at"]
    # The other athlete in the same order is untouched.
    assert rows[1]["status"] == "confirmed"


def test_the_freed_place_is_really_back_on_the_event_page(client, app):
    eid = _event(app, slug="cap")
    rid = _race(app, eid, capacity=3)
    _signin(client)
    reference = _enter(client, app, rid, 2)
    assert "1 of 3 places left" in client.get("/events/cap").get_data(as_text=True)
    _withdraw(client, reference, _entry_ids(app, reference)[0])
    assert "2 of 3 places left" in client.get("/events/cap").get_data(as_text=True)


def test_a_withdrawn_athlete_is_still_shown_but_not_as_racing(client, app):
    """The entry is the record that someone accepted a waiver and then pulled
    out; deleting the row would throw that away."""
    rid = _race(app, _event(app))
    _signin(client)
    reference = _enter(client, app, rid)
    _withdraw(client, reference, _entry_ids(app, reference)[0])
    body = client.get(f"/my-races/{reference}").get_data(as_text=True)
    assert "Ada" in body
    assert "Withdrawn" in body
    assert "is-cancelled" in body


def test_withdrawing_twice_is_refused_without_a_crash(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    reference = _enter(client, app, rid)
    entry_id = _entry_ids(app, reference)[0]
    _withdraw(client, reference, entry_id)
    body = _withdraw(client, reference, entry_id).get_data(as_text=True)
    assert "already withdrawn" in body
    assert _rows(app, "SELECT COUNT(*) n FROM entries WHERE status='cancelled'")[0]["n"] == 1


def test_nobody_can_withdraw_somebody_elses_athlete(app):
    rid = _race(app, _event(app))
    owner = app.test_client()
    _signin(owner, "owner@example.com")
    reference = _enter(owner, app, rid)
    entry_id = _entry_ids(app, reference)[0]

    nosy = app.test_client()
    _signin(nosy, "nosy@example.com")
    assert _withdraw(nosy, reference, entry_id).status_code == 404
    assert _rows(app, "SELECT status FROM entries")[0]["status"] == "confirmed"


def test_a_race_that_has_been_run_cannot_be_withdrawn_from(client, app):
    rid = _race(app, _event(app, days=14))
    _signin(client)
    reference = _enter(client, app, rid)
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE events SET event_date = ?",
                 ((date.today() - timedelta(days=1)).isoformat(),))
    conn.commit(); conn.close()
    body = _withdraw(client, reference, _entry_ids(app, reference)[0]).get_data(as_text=True)
    assert "already taken place" in body
    assert _rows(app, "SELECT status FROM entries")[0]["status"] == "confirmed"


def test_withdrawing_needs_the_csrf_token(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    reference = _enter(client, app, rid)
    entry_id = _entry_ids(app, reference)[0]
    r = client.post(f"/my-races/{reference}/withdraw/{entry_id}", data={})
    assert r.status_code == 400
    assert _rows(app, "SELECT status FROM entries")[0]["status"] == "confirmed"


def test_the_account_page_counts_only_races_still_being_raced(client, app):
    rid = _race(app, _event(app))
    _signin(client)
    reference = _enter(client, app, rid, 2)
    assert "2 upcoming entries" in client.get("/account").get_data(as_text=True)
    _withdraw(client, reference, _entry_ids(app, reference)[0])
    assert "1 upcoming entry" in client.get("/account").get_data(as_text=True)


# ── The confirmation email ───────────────────────────────

def test_email_is_off_unless_it_is_switched_on(monkeypatch):
    """A half-built events app on a box with a live mail key must not be one
    import away from mailing real people."""
    monkeypatch.setattr(mail, "ENABLED", False)
    sent, info = mail.send("someone@example.com", "Someone", "Subject", "<p>hi</p>")
    assert sent is False
    assert "disabled" in info


def test_a_mail_failure_does_not_cost_anyone_their_place(client, app, monkeypatch):
    def explode(*a, **kw):
        raise RuntimeError("mail server on fire")
    monkeypatch.setattr(mail, "send", explode)

    rid = _race(app, _event(app))
    _signin(client)
    _start(client, rid, 1)
    _athlete(client, 1)
    body = _confirm(client).get_data(as_text=True)
    assert "You're in" in body
    assert _rows(app, "SELECT status FROM entries")[0]["status"] == "confirmed"


def test_the_confirmation_email_carries_the_reference_and_the_athletes(client, app):
    captured = {}

    rid = _race(app, _event(app, name="ITC Spring Triathlon"))
    _bands(app, rid, [("30-39", 30, 39)])
    _signin(client)

    import itc.mail as mail_module
    original = mail_module.send

    def capture(to_email, to_name, subject, html):
        captured.update(to=to_email, subject=subject, html=html)
        return True, "captured"

    mail_module.send = capture
    try:
        reference = _enter(client, app, rid, 1,
                           date_of_birth=f"{date.today().year + 0 - 35}-06-01")
    finally:
        mail_module.send = original

    assert captured["to"] == "parent@example.com"
    assert reference in captured["subject"]
    assert reference in captured["html"]
    assert "Ada Lovelace" in captured["html"]
    assert "ITC Spring Triathlon" in captured["html"]
    # Mail clients drop stylesheets, so the markup has to carry its own styling.
    assert "<link" not in captured["html"]
