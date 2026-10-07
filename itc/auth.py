"""Accounts: register, sign in, sign out.

The account holder is an **organiser**, not necessarily a competitor. They may
enter themselves, their children, or a club's worth of people — so nothing here
assumes the person signing in is going to race. Their own athlete details are
collected per entry (Phase 4), not at sign-up, which keeps this form short and
means one account can enter a different set of people each time.

Email is the username. Athletes remember an email; they do not remember a
username they invented once a year.
"""
import hmac
import re
import secrets
import time
from functools import wraps

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from itc.db import get_db, now, query_one, transaction

bp = Blueprint("auth", __name__)

#: Short enough that nobody reaches for a password manager to join a fun run,
#: long enough to be worth hashing. Length beats composition rules.
MIN_PASSWORD = 10

#: Failed sign-ins before the door shuts, and for how long.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

#: In-process, which is enough for one gunicorn box and costs nothing. If ITC
#: ever runs more than one worker host this needs to move into the database.
_failures: dict = {}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Throttling ───────────────────────────────────────────
def _throttle_key() -> str:
    return (request.remote_addr or "?").lower()


def _retry_after() -> int:
    """Seconds left on a lockout, or 0."""
    hits, first = _failures.get(_throttle_key(), (0, 0.0))
    if hits < MAX_ATTEMPTS:
        return 0
    left = int(LOCKOUT_SECONDS - (time.time() - first))
    if left <= 0:
        _failures.pop(_throttle_key(), None)
        return 0
    return left


def _record_failure() -> None:
    key = _throttle_key()
    hits, first = _failures.get(key, (0, time.time()))
    # The window starts at the first failure, so a slow trickle of guesses
    # cannot keep resetting it.
    if time.time() - first > LOCKOUT_SECONDS:
        hits, first = 0, time.time()
    _failures[key] = (hits + 1, first)


def _clear_failures() -> None:
    _failures.pop(_throttle_key(), None)


# ── Session ──────────────────────────────────────────────
def _session_version(user) -> str:
    """Changes when the password changes, so a password reset signs out every
    other device rather than leaving an old session alive."""
    return str(user["password_hash"])[-16:]


def sign_in(user) -> None:
    session.clear()
    session.permanent = True
    session["user_id"] = user["user_id"]
    session["v"] = _session_version(user)


@bp.before_app_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid is None:
        return
    user = query_one(
        "SELECT * FROM users WHERE user_id = ? AND is_active = 1", (uid,))
    if user is None or session.get("v") != _session_version(user):
        session.clear()
        return
    g.user = user


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.get("user") is None:
            # Remember where they were headed, so signing in doesn't dump them
            # on the home page half way through entering a race.
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if g.user["role"] != "admin":
            abort(403)
        return view(*a, **kw)
    return wrapped


# ── CSRF ─────────────────────────────────────────────────
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@bp.before_app_request
def csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    from flask import current_app
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
    expected = session.get("_csrf") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Your session expired. Reload the page and try again.")


# ── Views ────────────────────────────────────────────────
def _safe_next(raw: str) -> str:
    """Only ever bounce back inside this site."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return url_for("pages.home")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.get("user"):
        return redirect(url_for("pages.home"))
    form = {}
    if request.method == "POST":
        form = {
            "full_name": (request.form.get("full_name") or "").strip()[:120],
            "email": (request.form.get("email") or "").strip().lower()[:200],
            "phone": (request.form.get("phone") or "").strip()[:40],
        }
        password = request.form.get("password") or ""
        errors = []
        if len(form["full_name"]) < 2:
            errors.append("Please give your name.")
        if not _EMAIL_RE.match(form["email"]):
            errors.append("That email address doesn't look right.")
        if len(password) < MIN_PASSWORD:
            errors.append(f"Use at least {MIN_PASSWORD} characters for your password.")
        if not errors and query_one(
                "SELECT 1 FROM users WHERE email = ?", (form["email"],)):
            # Said plainly. Hiding it would send a club member round the reset
            # loop for an account they already know they have, and the email is
            # discoverable from the sign-in form anyway.
            errors.append("There's already an account with that email. Sign in instead.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html", form=form), 400

        stamp = now()
        with transaction() as conn:
            conn.execute(
                """INSERT INTO users (email, password_hash, full_name, phone,
                        role, created_at, updated_at)
                   VALUES (?,?,?,?, 'athlete', ?, ?)""",
                (form["email"], generate_password_hash(password),
                 form["full_name"], form["phone"] or None, stamp, stamp))
        user = query_one("SELECT * FROM users WHERE email = ?", (form["email"],))
        sign_in(user)
        flash(f"Welcome, {user['full_name'].split()[0]}. Your account is ready.", "ok")
        return redirect(_safe_next(request.args.get("next", "")))

    return render_template("register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("pages.home"))
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""

        wait = _retry_after()
        if wait:
            flash(f"Too many attempts. Try again in {wait // 60 + 1} minutes.", "error")
            return render_template("login.html", email=email), 429

        user = query_one(
            "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,))
        if user and check_password_hash(user["password_hash"], password):
            _clear_failures()
            sign_in(user)
            return redirect(_safe_next(request.args.get("next", "")))

        _record_failure()
        # One message for both "no such account" and "wrong password", so the
        # form cannot be used to find out who has an account here.
        flash("Email or password not recognised.", "error")
        return render_template("login.html", email=email), 401

    return render_template("login.html", email=email)


@bp.post("/logout")
def logout():
    session.clear()
    flash("Signed out.", "ok")
    return redirect(url_for("pages.home"))


@bp.get("/account")
@login_required
def account():
    from datetime import date

    from itc.db import scalar
    # Cancelled entries are excluded: the number is "races you are going to",
    # not "rows we hold about you".
    upcoming = scalar(
        """SELECT COUNT(*) FROM entries en
             JOIN registrations g ON g.registration_id = en.registration_id
             JOIN events ev       ON ev.event_id = g.event_id
            WHERE g.user_id = ? AND en.status <> 'cancelled'
              AND ev.event_date >= ?""",
        (g.user["user_id"], date.today().isoformat()))
    return render_template("account.html", upcoming=upcoming or 0)
