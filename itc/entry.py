"""Phase 4: the registration flow — the heart of the app.

    pick a race → how many are racing → one screen per athlete → review → confirm

Shape of it, and why:

**One athlete per screen.** Most entries are made on a phone. A single form
holding four athletes is forty fields on a 390px screen; four forms of ten are
four manageable screens with a visible "2 of 4".

**Progress lives in the database, not the cookie.** See `entry_drafts` — four
athletes with medical notes exceed a session cookie, and the browser discards
an oversized cookie silently, which looks exactly like the app eating your
typing.

**Nothing is written until Confirm.** A draft is form state; entries are a
commitment. Capacity, duplicates and eligibility are all re-checked at that
moment inside one write-locked transaction, because everything read while the
review page was on screen is already stale.

**Categories are derived, never offered.** See `itc.ages`.

Open question, built to be flipped: the waiver text in `templates/_waiver.html`
is a placeholder. ITC's own wording must replace it before a live event — the
timestamp this flow records is only worth what the text says.
"""
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)

from itc import ages
from itc.auth import login_required
from itc.db import (exclusive_transaction, get_setting, now, query_all,
                    query_one, race_taken, transaction)

bp = Blueprint("entry", __name__, url_prefix="/enter")

#: Most entries are one to four people. The ceiling is a guard against a
#: mistyped party size, not a club policy — raise it when a team needs it.
MAX_PARTY = 8

GENDERS = (("female", "Female"), ("male", "Male"), ("other", "Other"))
SHIRT_SIZES = ("XS", "S", "M", "L", "XL", "XXL")

#: Under this, an entry needs a named parent or guardian.
GUARDIAN_AGE = 18

#: Enough for "asthma — carries an inhaler", not a medical history. Race day
#: needs the one line a marshal can act on.
MAX_NOTE = 400

#: Abandoned drafts are swept after this. They hold entrant details, so they
#: are not kept indefinitely for the sake of it.
DRAFT_TTL_DAYS = 7

#: No I/1/O/0/S/5/B/8 — references get read down a phone on race morning.
_REF_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[\d+\s().-]{6,25}$")

_STEPS = ("Race", "Athletes", "Review", "Done")


class _Oversubscribed(Exception):
    """Someone took the places while this entry was being filled in."""

    def __init__(self, left):
        self.left = left


class _AlreadyEntered(Exception):
    def __init__(self, name):
        self.name = name


# ── Race, event and capacity ─────────────────────────────
def _race_for_entry(race_id: int):
    race = query_one(
        """SELECT r.*, e.event_id, e.slug, e.name AS event_name, e.event_date,
                  e.venue, e.status AS event_status, e.reg_opens_at, e.reg_closes_at
           FROM races r JOIN events e ON e.event_id = r.event_id
           WHERE r.race_id = ?""", (race_id,))
    if race is None:
        abort(404)
    return race


def _closed_reason(race):
    """Why this race cannot be entered right now, or None.

    Registration windows are compared as Bahrain calendar days and are
    inclusive at both ends: "closes 12 March" means entries are open all of
    the 12th. Comparing against `date.today()` rather than SQLite's UTC
    `date('now')` matters here — between midnight and 03:00 local they differ,
    and the wrong one shuts entries a day early.
    """
    today = date.today().isoformat()
    if race["event_status"] != "open":
        return f"Entries for {race['event_name']} are not open."
    if race["event_date"] < today:
        return f"{race['event_name']} has already taken place."
    opens = (race["reg_opens_at"] or "")[:10]
    closes = (race["reg_closes_at"] or "")[:10]
    if opens and today < opens:
        return f"Entries for {race['event_name']} open on {opens}."
    if closes and today > closes:
        return f"Entries for {race['event_name']} closed on {closes}."
    return None


def _places_left(race):
    """Indicative only — None means uncapped. The binding check is in confirm."""
    if race["capacity"] is None:
        return None
    taken = query_one(
        "SELECT COUNT(*) AS n FROM entries WHERE race_id = ? AND status = 'confirmed'",
        (race["race_id"],))["n"]
    return max(race["capacity"] - taken, 0)


def _groups(race_id: int):
    return query_all(
        """SELECT age_group_id, label, gender, min_age, max_age
           FROM age_groups WHERE race_id = ? ORDER BY sort_order, age_group_id""",
        (race_id,))


def _age_rule() -> str:
    rule = get_setting("age_rule", "dec31")
    return rule if rule in ages.AGE_RULES else "dec31"


# ── Drafts ───────────────────────────────────────────────
def _new_draft(race_id: int, party_size: int) -> str:
    token = secrets.token_urlsafe(24)
    stamp = now()
    with transaction() as conn:
        # One live draft per race per account: starting again replaces the old
        # one rather than quietly leaving a second half-filled entry behind.
        conn.execute("DELETE FROM entry_drafts WHERE user_id = ? AND race_id = ?",
                     (g.user["user_id"], race_id))
        conn.execute(
            """INSERT INTO entry_drafts (token,user_id,race_id,party_size,payload,
                    created_at,updated_at) VALUES (?,?,?,?,'{}',?,?)""",
            (token, g.user["user_id"], race_id, party_size, stamp, stamp))
    return token


def _draft():
    """The caller's draft, with `payload` parsed into `data`.

    Ownership is checked against the signed-in account, not just the token:
    a session restored on a shared machine must not hand over someone else's
    entrant details.
    """
    token = session.get("draft")
    if not token:
        return None
    row = query_one(
        "SELECT * FROM entry_drafts WHERE token = ? AND user_id = ?",
        (token, g.user["user_id"]))
    if row is None:
        return None
    try:
        row["data"] = json.loads(row["payload"]) or {}
    except ValueError:
        row["data"] = {}
    return row


def _save_payload(draft, payload) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE entry_drafts SET payload = ?, updated_at = ? WHERE draft_id = ?",
            (json.dumps(payload), now(), draft["draft_id"]))


def _prune_drafts() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DRAFT_TTL_DAYS)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with transaction() as conn:
        conn.execute("DELETE FROM entry_drafts WHERE updated_at < ?", (cutoff,))


def _lost():
    flash("That entry had expired, so we've started you again.", "error")
    return redirect(url_for("pages.home"))


# ── Validation ───────────────────────────────────────────
def _me_prefill() -> dict:
    """Athlete 1 pre-filled from the account.

    A default, not an assumption: the account holder may be a parent who isn't
    racing, so every field stays editable and the form says where it came from.
    """
    parts = (g.user["full_name"] or "").split()
    return {
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "email": g.user["email"],
        "phone": g.user["phone"] or "",
    }


def _validate_athlete(form, race, groups, rule):
    """One athlete's details as clean data, plus everything wrong with them.

    Returns every error at once. Fixing one field, resubmitting and being told
    about the next is the worst version of a form on a phone.
    """
    data = {
        "first_name": (form.get("first_name") or "").strip()[:60],
        "last_name": (form.get("last_name") or "").strip()[:60],
        "date_of_birth": (form.get("date_of_birth") or "").strip()[:10],
        "gender": (form.get("gender") or "").strip(),
        "email": (form.get("email") or "").strip().lower()[:200],
        "phone": (form.get("phone") or "").strip()[:40],
        "club": (form.get("club") or "").strip()[:80],
        "shirt_size": (form.get("shirt_size") or "").strip(),
        "emergency_name": (form.get("emergency_name") or "").strip()[:120],
        "emergency_phone": (form.get("emergency_phone") or "").strip()[:40],
        "medical_notes": (form.get("medical_notes") or "").strip()[:MAX_NOTE],
        "guardian_name": (form.get("guardian_name") or "").strip()[:120],
    }
    errors = []

    if len(data["first_name"]) < 2:
        errors.append("Give the athlete's first name.")
    if len(data["last_name"]) < 2:
        errors.append("Give the athlete's last name.")
    if data["gender"] not in dict(GENDERS):
        errors.append("Choose the athlete's gender.")
    if data["shirt_size"] and data["shirt_size"] not in SHIRT_SIZES:
        errors.append("That isn't one of the shirt sizes.")
    if data["email"] and not _EMAIL_RE.match(data["email"]):
        errors.append("That athlete email address doesn't look right.")
    if data["phone"] and not _PHONE_RE.match(data["phone"]):
        errors.append("That athlete phone number doesn't look right.")
    if len(data["emergency_name"]) < 2:
        errors.append("An emergency contact name is required for every athlete.")
    if not _PHONE_RE.match(data["emergency_phone"] or ""):
        errors.append("An emergency contact number is required for every athlete.")

    dob = ages.parse_iso(data["date_of_birth"])
    age = None
    if dob is None:
        errors.append("Give the athlete's date of birth.")
    elif dob > date.today():
        errors.append("That date of birth is in the future.")
    elif dob.year < ages.EARLIEST_BIRTH_YEAR:
        errors.append(f"Check that year of birth — {dob.year} looks like a typo.")
    else:
        age = ages.age_on(dob, race["event_date"], rule)
        data["age"] = age
        if race["min_age"] is not None and age < race["min_age"]:
            errors.append(
                f"{race['name']} is for ages {race['min_age']} and over; "
                f"this athlete would be {age}.")
        elif groups:
            group = ages.match_group(groups, age, data["gender"])
            if group is None:
                errors.append(
                    f"There's no age group for a {age}-year-old in "
                    f"{race['name']}. It takes {ages.band_summary(groups)}.")
            else:
                data["age_group_id"] = group["age_group_id"]
                data["age_group"] = group["label"]
        if age is not None and age < GUARDIAN_AGE and len(data["guardian_name"]) < 2:
            errors.append(
                f"{data['first_name'] or 'This athlete'} is under {GUARDIAN_AGE}, "
                "so a parent or guardian must be named.")

    if not form.get("waiver"):
        errors.append("The waiver has to be accepted for each athlete.")
    else:
        # Stamped when they actually agreed, not when the entry was written —
        # after an incident the question is when this person accepted it.
        data["waiver_accepted_at"] = now()

    return data, errors


def _party(draft, race, groups, rule):
    """Every athlete in the draft, re-validated and re-derived.

    Nothing trusts what the draft stored: the age group is recomputed from the
    date of birth every time it is shown or written, so a change to the rule,
    the bands or the race date can never leave a stale category in place.
    """
    rows, problems = [], []
    for n in range(1, draft["party_size"] + 1):
        saved = draft["data"].get(str(n))
        if not saved:
            problems.append((n, None))
            continue
        data, errors = _validate_athlete(_Saved(saved), race, groups, rule)
        # The waiver was accepted on the athlete's own screen; keep that moment
        # rather than re-stamping it on every page view.
        if saved.get("waiver_accepted_at"):
            data["waiver_accepted_at"] = saved["waiver_accepted_at"]
        if errors:
            problems.append((n, errors))
        rows.append((n, data))
    return rows, problems


class _Saved(dict):
    """Lets saved draft data go back through the same validator as a form.

    A dict is nearly a `request.form` already; the waiver is the exception,
    because a stored timestamp means the box was ticked.
    """

    def get(self, key, default=None):
        if key == "waiver":
            return "on" if dict.get(self, "waiver_accepted_at") else ""
        return dict.get(self, key, default)


def _within_party_duplicate(rows):
    """Two of the same person in one order — usually a mis-tapped Back button."""
    seen = {}
    for n, data in rows:
        key = (data["first_name"].lower(), data["last_name"].lower(),
               data["date_of_birth"])
        if key in seen:
            return f"{data['first_name']} {data['last_name']} is listed twice " \
                   f"(athletes {seen[key]} and {n})."
        seen[key] = n
    return None


# ── Steps ────────────────────────────────────────────────
@bp.route("/<int:race_id>", methods=["GET", "POST"])
@login_required
def start(race_id):
    """Step 1 — how many people are racing."""
    race = _race_for_entry(race_id)
    reason = _closed_reason(race)
    if reason:
        flash(reason, "error")
        return redirect(url_for("pages.event", slug=race["slug"]))

    left = _places_left(race)
    ceiling = MAX_PARTY if left is None else min(MAX_PARTY, left)
    if ceiling < 1:
        flash(f"{race['name']} is full.", "error")
        return redirect(url_for("pages.event", slug=race["slug"]))

    bad_size = False
    if request.method == "POST":
        try:
            size = int(request.form.get("party_size") or 0)
        except ValueError:
            size = 0
        if size < 1 or size > ceiling:
            flash(f"Choose between 1 and {ceiling} athletes.", "error")
            bad_size = True
        else:
            _prune_drafts()
            session["draft"] = _new_draft(race_id, size)
            return redirect(url_for("entry.athlete", n=1))

    existing = _draft()
    page = render_template(
        "enter_start.html", race=race, places_left=left, ceiling=ceiling,
        groups=_groups(race_id), steps=_STEPS, step=1,
        resume=existing if existing and existing["race_id"] == race_id else None)
    return (page, 400) if bad_size else page


@bp.route("/athlete/<int:n>", methods=["GET", "POST"])
@login_required
def athlete(n):
    """Step 2 — one screen per athlete, `n` of the party."""
    draft = _draft()
    if draft is None:
        return _lost()
    if n < 1:
        abort(404)
    if n > draft["party_size"]:
        return redirect(url_for("entry.review"))

    race = _race_for_entry(draft["race_id"])
    reason = _closed_reason(race)
    if reason:
        flash(reason, "error")
        return redirect(url_for("pages.event", slug=race["slug"]))

    groups = _groups(race["race_id"])
    rule = _age_rule()
    payload = draft["data"]

    if request.method == "POST":
        data, errors = _validate_athlete(request.form, race, groups, rule)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("enter_athlete.html", race=race, n=n,
                                   draft=draft, form=request.form,
                                   genders=GENDERS, shirts=SHIRT_SIZES,
                                   groups=groups, max_note=MAX_NOTE,
                                   guardian_age=GUARDIAN_AGE,
                                   steps=_STEPS, step=2, today=date.today()), 400
        payload[str(n)] = data
        _save_payload(draft, payload)
        if n < draft["party_size"]:
            return redirect(url_for("entry.athlete", n=n + 1))
        return redirect(url_for("entry.review"))

    form = payload.get(str(n)) or (_me_prefill() if n == 1 else {})
    return render_template("enter_athlete.html", race=race, n=n, draft=draft,
                           form=_Saved(form), genders=GENDERS,
                           shirts=SHIRT_SIZES, groups=groups,
                           max_note=MAX_NOTE, guardian_age=GUARDIAN_AGE,
                           prefilled=(n == 1 and not payload.get("1")),
                           steps=_STEPS, step=2, today=date.today())


@bp.get("/review")
@login_required
def review():
    """Step 3 — everything, once, before anything is written."""
    draft = _draft()
    if draft is None:
        return _lost()
    race = _race_for_entry(draft["race_id"])
    reason = _closed_reason(race)
    if reason:
        flash(reason, "error")
        return redirect(url_for("pages.event", slug=race["slug"]))

    rows, problems = _party(draft, race, _groups(race["race_id"]), _age_rule())
    if problems:
        first, errors = problems[0]
        for e in (errors or ["That athlete's details are still missing."]):
            flash(e, "error")
        return redirect(url_for("entry.athlete", n=first))

    return render_template("enter_review.html", race=race, rows=rows,
                           draft=draft, rule=_age_rule(),
                           total=race["price_bhd"] * len(rows),
                           steps=_STEPS, step=3)


@bp.post("/confirm")
@login_required
def confirm():
    """The only step that writes anything."""
    draft = _draft()
    if draft is None:
        return _lost()
    race = _race_for_entry(draft["race_id"])
    reason = _closed_reason(race)
    if reason:
        flash(reason, "error")
        return redirect(url_for("pages.event", slug=race["slug"]))

    rows, problems = _party(draft, race, _groups(race["race_id"]), _age_rule())
    if problems:
        first, errors = problems[0]
        for e in (errors or ["That athlete's details are still missing."]):
            flash(e, "error")
        return redirect(url_for("entry.athlete", n=first))

    clash = _within_party_duplicate(rows)
    if clash:
        flash(clash, "error")
        return redirect(url_for("entry.review"))

    try:
        reference = _write_entries(draft, race, rows)
    except _Oversubscribed as exc:
        # Honest arithmetic beats a partial order: entering three children and
        # being given two places silently is worse than being told to come back
        # with a smaller party. Waitlisting is Phase 5's decision to make.
        flash(f"While you were filling that in, {race['name']} dropped to "
              f"{exc.left} place{'' if exc.left == 1 else 's'}. "
              "Go back and reduce the party, or pick another race.", "error")
        return redirect(url_for("entry.review"))
    except _AlreadyEntered as exc:
        flash(f"{exc.name} is already entered in {race['name']}.", "error")
        return redirect(url_for("entry.review"))

    session.pop("draft", None)
    flash("Entry confirmed. See you on the start line.", "ok")

    # The entry is already written and committed. A mail failure is reported
    # here and nowhere else — it must never undo a place on a start line.
    from itc.registrations import send_confirmation
    sent, info = send_confirmation(reference)
    if sent:
        flash(f"A copy is on its way to {g.user['email']}.", "ok")
    else:
        current_app.logger.info("No confirmation email sent (%s)", info)
    return redirect(url_for("entry.done", reference=reference))


def _write_entries(draft, race, rows) -> str:
    """Registration + one entry per athlete, or nothing at all.

    Capacity and the duplicate check happen in here, under the write lock, for
    the reason in `exclusive_transaction`: anything checked while the review
    page was on screen was only ever a guess.
    """
    stamp = now()
    with exclusive_transaction() as conn:
        if race["capacity"] is not None:
            left = max(race["capacity"] - race_taken(conn, race["race_id"]), 0)
            if left < len(rows):
                raise _Oversubscribed(left)

        for _, data in rows:
            # Cancelled entries freed their place, so they must not block a
            # re-entry. Waitlisted ones are still in the queue, so they do.
            if conn.execute(
                    """SELECT 1 FROM entries
                       WHERE race_id = ? AND status <> 'cancelled'
                         AND lower(first_name) = ? AND lower(last_name) = ?
                         AND date_of_birth = ?""",
                    (race["race_id"], data["first_name"].lower(),
                     data["last_name"].lower(), data["date_of_birth"])).fetchone():
                raise _AlreadyEntered(f"{data['first_name']} {data['last_name']}")

        reference = _reserve_reference(conn)
        registration_id = conn.execute(
            """INSERT INTO registrations (reference,user_id,event_id,created_at)
               VALUES (?,?,?,?)""",
            (reference, g.user["user_id"], race["event_id"], stamp)).lastrowid

        for _, d in rows:
            conn.execute(
                """INSERT INTO entries (registration_id,race_id,age_group_id,
                        first_name,last_name,date_of_birth,gender,email,phone,
                        club,shirt_size,emergency_name,emergency_phone,
                        medical_notes,guardian_name,waiver_accepted_at,status,
                        created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'confirmed',?,?)""",
                (registration_id, race["race_id"], d.get("age_group_id"),
                 d["first_name"], d["last_name"], d["date_of_birth"],
                 d["gender"], d["email"] or None, d["phone"] or None,
                 d["club"] or None, d["shirt_size"] or None,
                 d["emergency_name"], d["emergency_phone"],
                 d["medical_notes"] or None, d["guardian_name"] or None,
                 d.get("waiver_accepted_at"), stamp, stamp))

        conn.execute("DELETE FROM entry_drafts WHERE draft_id = ?",
                     (draft["draft_id"],))
    return reference


def _reserve_reference(conn) -> str:
    for _ in range(16):
        reference = "ITC-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        if not conn.execute("SELECT 1 FROM registrations WHERE reference = ?",
                            (reference,)).fetchone():
            return reference
    # 26^6 of them; exhausting sixteen tries means something else is wrong.
    raise RuntimeError("Could not allocate a registration reference")


@bp.get("/done/<reference>")
@login_required
def done(reference):
    """Step 4 — proof. Phase 5 adds the email and the withdrawal link."""
    reg = query_one(
        """SELECT r.*, e.name AS event_name, e.event_date, e.venue, e.slug
           FROM registrations r JOIN events e ON e.event_id = r.event_id
           WHERE r.reference = ?""", (reference,))
    if reg is None:
        abort(404)
    # Entrant details, including dates of birth and medical notes. Only the
    # account that entered them may see them.
    if reg["user_id"] != g.user["user_id"]:
        abort(403)

    entries = query_all(
        """SELECT en.*, ra.name AS race_name, ra.discipline, ra.start_time,
                  ag.label AS age_group
           FROM entries en
           JOIN races ra ON ra.race_id = en.race_id
           LEFT JOIN age_groups ag ON ag.age_group_id = en.age_group_id
           WHERE en.registration_id = ?
           ORDER BY en.entry_id""", (reg["registration_id"],))
    return render_template("enter_done.html", reg=reg, entries=entries,
                           steps=_STEPS, step=4)
