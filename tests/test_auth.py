"""Phase 3: accounts.

Most of what matters here is refusal — a sign-in form that reveals who has an
account, a session that survives a password change, or a POST that any other
site can trigger, are all easier to get wrong than right.
"""
import re

import pytest

from itc.db import connect, now, query_one


def _csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def register(client, email="runner@example.com", password="correct-horse-1",
             name="Ada Runner", phone=""):
    return client.post("/register", data={
        "csrf_token": _csrf(client, "/register"), "full_name": name,
        "email": email, "password": password, "phone": phone,
    }, follow_redirects=True)


def login(client, email="runner@example.com", password="correct-horse-1"):
    return client.post("/login", data={
        "csrf_token": _csrf(client), "email": email, "password": password,
    }, follow_redirects=True)


# ── Registering ──────────────────────────────────────────

def test_an_account_can_be_created_and_signed_back_into(client):
    """Phase 3's definition of done."""
    assert register(client).status_code == 200
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    r = login(client)
    assert r.status_code == 200
    assert "Ada" in r.get_data(as_text=True)          # the nav greets them


def test_registering_signs_you_straight_in(client):
    """Making someone type the password they just chose, immediately, is the
    kind of friction that loses a race entry."""
    body = register(client).get_data(as_text=True)
    assert "Welcome, Ada" in body
    assert client.get("/account").status_code == 200


def test_a_short_password_is_refused_without_losing_the_form(client):
    r = client.post("/register", data={
        "csrf_token": _csrf(client, "/register"), "full_name": "Ada Runner",
        "email": "ada@example.com", "password": "short", "phone": "",
    })
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "at least 10 characters" in body
    # The name and email they already typed are still there.
    assert "Ada Runner" in body and "ada@example.com" in body


def test_a_malformed_email_is_refused(client):
    r = client.post("/register", data={
        "csrf_token": _csrf(client, "/register"), "full_name": "Ada",
        "email": "not-an-email", "password": "correct-horse-1", "phone": "",
    })
    assert r.status_code == 400
    # Jinja escapes the apostrophe, so assert on text that survives it.
    assert "look right" in r.get_data(as_text=True)


def test_the_same_email_cannot_register_twice(client):
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    r = client.post("/register", data={
        "csrf_token": _csrf(client, "/register"), "full_name": "Someone Else",
        "email": "runner@example.com", "password": "another-good-one", "phone": "",
    })
    assert r.status_code == 400
    assert "already an account" in r.get_data(as_text=True)


def test_email_is_stored_lowercase_and_matched_either_way(client, app):
    """People capitalise their email at random. Matching on case would hand
    them a second account and a mysterious 'wrong password'."""
    register(client, email="Ada.Runner@Example.COM")
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    r = login(client, email="ada.runner@example.com")
    assert r.status_code == 200 and "Ada" in r.get_data(as_text=True)


# ── Signing in ───────────────────────────────────────────

def test_a_wrong_password_says_nothing_about_who_has_an_account(client):
    """The same message for 'no such user' and 'wrong password', so the form
    cannot be used to find out who is a member."""
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    wrong = client.post("/login", data={
        "csrf_token": _csrf(client), "email": "runner@example.com",
        "password": "not-the-password"})
    missing = client.post("/login", data={
        "csrf_token": _csrf(client), "email": "nobody@example.com",
        "password": "not-the-password"})
    assert wrong.status_code == missing.status_code == 401
    assert "not recognised" in wrong.get_data(as_text=True)
    assert "not recognised" in missing.get_data(as_text=True)


def test_repeated_failures_shut_the_door(client):
    from itc.auth import MAX_ATTEMPTS, _failures
    _failures.clear()
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    for _ in range(MAX_ATTEMPTS):
        client.post("/login", data={"csrf_token": _csrf(client),
                                    "email": "runner@example.com", "password": "nope"})
    r = client.post("/login", data={"csrf_token": _csrf(client),
                                    "email": "runner@example.com",
                                    "password": "correct-horse-1"})
    assert r.status_code == 429
    assert "Too many attempts" in r.get_data(as_text=True)
    _failures.clear()


def test_a_deactivated_account_cannot_sign_in(client, app):
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET is_active = 0"); conn.commit(); conn.close()
    assert client.post("/login", data={
        "csrf_token": _csrf(client), "email": "runner@example.com",
        "password": "correct-horse-1"}).status_code == 401


# ── Sessions ─────────────────────────────────────────────

def test_changing_the_password_ends_other_sessions(client, app):
    """A password change is how someone responds to a device being lost; it
    has to actually sign that device out."""
    register(client)
    assert client.get("/account").status_code == 200
    from werkzeug.security import generate_password_hash
    conn = connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET password_hash = ?",
                 (generate_password_hash("a-brand-new-password"),))
    conn.commit(); conn.close()
    assert client.get("/account").status_code == 302      # bounced to sign-in


def test_signing_in_returns_you_to_where_you_were_going(client):
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    r = client.post("/login?next=/account", data={
        "csrf_token": _csrf(client), "email": "runner@example.com",
        "password": "correct-horse-1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/account")


def test_an_offsite_next_target_is_ignored(client):
    """Otherwise the sign-in form is an open redirect for phishing."""
    register(client)
    client.post("/logout", data={"csrf_token": _csrf(client, "/")})
    r = client.post("/login?next=https://evil.example.com/", data={
        "csrf_token": _csrf(client), "email": "runner@example.com",
        "password": "correct-horse-1"})
    assert "evil.example.com" not in r.headers.get("Location", "")


def test_signing_out_needs_a_token_so_nobody_else_can_do_it(client):
    register(client)
    assert client.post("/logout", data={}).status_code == 400   # no token
    assert client.get("/account").status_code == 200            # still signed in


# ── CSRF ─────────────────────────────────────────────────

def test_a_post_without_a_token_is_refused(client):
    r = client.post("/register", data={
        "full_name": "Ada", "email": "a@b.com", "password": "correct-horse-1"})
    assert r.status_code == 400
    assert query_one_safe(client) is None


def query_one_safe(client):
    """No user should exist after a rejected registration."""
    from flask import current_app
    with client.application.app_context():
        return query_one("SELECT 1 FROM users")


def test_a_forged_token_is_refused(client):
    r = client.post("/register", data={
        "csrf_token": "not-the-real-token", "full_name": "Ada",
        "email": "a@b.com", "password": "correct-horse-1"})
    assert r.status_code == 400


# ── Access ───────────────────────────────────────────────

def test_the_account_page_needs_an_account(client):
    r = client.get("/account")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_the_nav_offers_sign_in_when_signed_out(client):
    body = client.get("/").get_data(as_text=True)
    assert "Sign in" in body and "Sign out" not in body
