"""Phase 6: the organiser's side.

The test of this phase is whether ITC can run an event start to finish without
anyone opening the database. That means: build the event, publish it, watch it
fill, fix the entries that need fixing, assign bibs, and hand the timing
company a file.

Three things shape the code:

**Editing bands recomputes categories.** Age groups are not labels on an entry,
they are derived from date of birth (`itc.ages`). So when an organiser rewrites
the bands, every affected entry is recomputed from the date of birth it has
been carrying since Phase 4. Anything else leaves a start list quietly
disagreeing with the bands printed next to it.

**Deleting is refused once anyone has entered.** A cascade would take real
entries with it. Close the event instead; that is what `closed` is for.

**The export is the deliverable.** A timing company gets one CSV with
everything they need and nothing they have to ask for twice.
"""
import csv
import io
import re
from datetime import date

from flask import (Blueprint, Response, abort, flash, g, redirect,
                   render_template, request, url_for)

from itc import ages
from itc.db import (DISCIPLINES, EVENT_STATUSES, get_setting, now, query_all,
                    query_one, set_setting, transaction)
from itc.locale import CURRENCIES, app_timezone

bp = Blueprint("admin", __name__, url_prefix="/admin")

ENTRY_STATUSES = ("confirmed", "waitlisted", "cancelled")
GENDERS = ("any", "male", "female")
AGE_RULES = ("dec31", "race_day")

#: Zones offered in the Settings dropdown. Any IANA name is accepted on submit;
#: these are just the common ones so the two launch regions are one click.
COMMON_TIMEZONES = (
    "Asia/Bahrain", "Asia/Manila", "Asia/Dubai", "Asia/Riyadh",
    "Asia/Singapore", "Asia/Hong_Kong", "Europe/London", "UTC",
)


@bp.before_request
def only_organisers():
    """One guard for the whole area, rather than a decorator per view that can
    be forgotten on the thirteenth one."""
    if g.get("user") is None:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if g.user["role"] != "admin":
        abort(403)


# ── Helpers ──────────────────────────────────────────────
def _slugify(text: str, fallback: str = "event") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:80] or fallback


def _unique_slug(base: str, event_id=None) -> str:
    slug, n = base, 2
    while True:
        clash = query_one(
            "SELECT event_id FROM events WHERE slug = ? AND event_id IS NOT ?",
            (slug, event_id))
        if not clash:
            return slug
        slug, n = f"{base}-{n}", n + 1


def _get_event(event_id):
    event = query_one("SELECT * FROM events WHERE event_id = ?", (event_id,))
    if event is None:
        abort(404)
    return event


def _get_race(race_id):
    race = query_one(
        """SELECT r.*, e.name AS event_name, e.event_date, e.slug
           FROM races r JOIN events e ON e.event_id = r.event_id
           WHERE r.race_id = ?""", (race_id,))
    if race is None:
        abort(404)
    return race


def _int_or_none(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# ── Dashboard ────────────────────────────────────────────
@bp.get("")
def dashboard():
    """Every event, with how full it is. The number that matters is confirmed
    entries, because that is who turns up."""
    events = query_all(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM races r WHERE r.event_id = e.event_id)
                      AS race_count,
                  (SELECT COUNT(*) FROM entries en
                     JOIN races r ON r.race_id = en.race_id
                    WHERE r.event_id = e.event_id AND en.status = 'confirmed')
                      AS entered,
                  (SELECT COUNT(*) FROM entries en
                     JOIN races r ON r.race_id = en.race_id
                    WHERE r.event_id = e.event_id AND en.status = 'waitlisted')
                      AS waiting,
                  (SELECT SUM(COALESCE(r.capacity, 0)) FROM races r
                    WHERE r.event_id = e.event_id) AS capacity,
                  (SELECT COUNT(*) FROM races r
                    WHERE r.event_id = e.event_id AND r.capacity IS NULL)
                      AS uncapped
           FROM events e
           ORDER BY e.event_date DESC""")
    today = date.today().isoformat()
    for e in events:
        e["is_past"] = e["event_date"] < today
        cap = e["capacity"] or 0
        e["pct"] = int(min(e["entered"] / cap * 100, 100)) if cap else 0
    return render_template("admin/dashboard.html", events=events,
                           today=today, age_rule=get_setting("age_rule", "dec31"))


# ── Settings ─────────────────────────────────────────────
def _current_settings():
    return {
        "club_name": get_setting("club_name", "International Triathlon Club"),
        "currency": get_setting("currency", "BHD"),
        "app_timezone": get_setting("app_timezone", "Asia/Bahrain"),
        "age_rule": get_setting("age_rule", "dec31"),
    }


def _settings_choices():
    return {
        "currencies": sorted(CURRENCIES),
        "timezones": COMMON_TIMEZONES,
        "age_rules": AGE_RULES,
    }


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    """Region and club basics, so switching the app from Bahrain to a
    Philippine series is a form, not a database edit. Validated before save:
    a bad currency or zone here would quietly mislabel every price or shift
    every race day."""
    if request.method == "POST":
        club_name = (request.form.get("club_name") or "").strip()
        currency = (request.form.get("currency") or "").strip().upper()
        tz = (request.form.get("app_timezone") or "").strip()
        age_rule = (request.form.get("age_rule") or "").strip()

        errors = []
        if not club_name:
            errors.append("Club name cannot be empty.")
        if currency not in CURRENCIES:
            errors.append(f"Unknown currency '{currency}'.")
        # app_timezone falls back silently at read time, but a bad value saved
        # here is a latent bug, so reject it at the point of entry instead.
        if app_timezone(tz).key != tz:
            errors.append(f"Unknown timezone '{tz}'.")
        if age_rule not in AGE_RULES:
            errors.append("Age rule must be one of: " + ", ".join(AGE_RULES) + ".")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/settings.html",
                                   settings=request.form,
                                   choices=_settings_choices()), 400

        set_setting("club_name", club_name)
        set_setting("currency", currency)
        set_setting("app_timezone", tz)
        set_setting("age_rule", age_rule)
        flash("Settings saved.", "ok")
        return redirect(url_for("admin.settings"))

    return render_template("admin/settings.html",
                           settings=_current_settings(),
                           choices=_settings_choices())


# ── Events ───────────────────────────────────────────────
def _event_form(form):
    data = {
        "name": (form.get("name") or "").strip()[:160],
        "summary": (form.get("summary") or "").strip()[:400],
        "description": (form.get("description") or "").strip()[:4000],
        "venue": (form.get("venue") or "").strip()[:160],
        "event_date": (form.get("event_date") or "").strip()[:10],
        "reg_opens_at": (form.get("reg_opens_at") or "").strip()[:10],
        "reg_closes_at": (form.get("reg_closes_at") or "").strip()[:10],
        "status": (form.get("status") or "draft").strip(),
    }
    errors = []
    if len(data["name"]) < 3:
        errors.append("Give the event a name.")
    if ages.parse_iso(data["event_date"]) is None:
        errors.append("Give the race date.")
    if data["status"] not in EVENT_STATUSES:
        errors.append("That isn't a valid status.")
    for field, label in (("reg_opens_at", "opens"), ("reg_closes_at", "closes")):
        if data[field] and ages.parse_iso(data[field]) is None:
            errors.append(f"The registration {label} date isn't a date.")
    if (data["reg_opens_at"] and data["reg_closes_at"]
            and data["reg_opens_at"] > data["reg_closes_at"]):
        errors.append("Registration would close before it opened.")
    if (data["reg_closes_at"] and data["event_date"]
            and data["reg_closes_at"] > data["event_date"]):
        errors.append("Registration would close after the race has been run.")
    return data, errors


@bp.route("/events/new", methods=["GET", "POST"])
def event_new():
    if request.method == "POST":
        data, errors = _event_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/event_form.html", event=request.form,
                                   statuses=EVENT_STATUSES, creating=True), 400
        stamp = now()
        with transaction() as conn:
            event_id = conn.execute(
                """INSERT INTO events (slug,name,summary,description,venue,
                        event_date,reg_opens_at,reg_closes_at,status,
                        created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_unique_slug(_slugify(data["name"])), data["name"],
                 data["summary"] or None, data["description"] or None,
                 data["venue"] or None, data["event_date"],
                 data["reg_opens_at"] or None, data["reg_closes_at"] or None,
                 data["status"], stamp, stamp)).lastrowid
        flash(f"{data['name']} created. Add its races next.", "ok")
        return redirect(url_for("admin.event_edit", event_id=event_id))
    return render_template("admin/event_form.html",
                           event={"status": "draft"}, statuses=EVENT_STATUSES,
                           creating=True)


@bp.route("/events/<int:event_id>", methods=["GET", "POST"])
def event_edit(event_id):
    event = _get_event(event_id)
    if request.method == "POST":
        data, errors = _event_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            merged = dict(event); merged.update(request.form)
            return render_template("admin/event_form.html", event=merged,
                                   statuses=EVENT_STATUSES, creating=False,
                                   races=_races_of(event_id)), 400
        with transaction() as conn:
            conn.execute(
                """UPDATE events SET name=?, summary=?, description=?, venue=?,
                       event_date=?, reg_opens_at=?, reg_closes_at=?, status=?,
                       updated_at=? WHERE event_id=?""",
                (data["name"], data["summary"] or None,
                 data["description"] or None, data["venue"] or None,
                 data["event_date"], data["reg_opens_at"] or None,
                 data["reg_closes_at"] or None, data["status"], now(), event_id))
        flash("Event saved.", "ok")
        return redirect(url_for("admin.event_edit", event_id=event_id))

    return render_template("admin/event_form.html", event=event,
                           statuses=EVENT_STATUSES, creating=False,
                           races=_races_of(event_id))


def _races_of(event_id):
    return query_all(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM entries en
                    WHERE en.race_id = r.race_id AND en.status='confirmed') AS entered,
                  (SELECT COUNT(*) FROM age_groups g WHERE g.race_id = r.race_id)
                      AS bands
           FROM races r WHERE r.event_id = ?
           ORDER BY r.sort_order, r.race_id""", (event_id,))


@bp.post("/events/<int:event_id>/delete")
def event_delete(event_id):
    event = _get_event(event_id)
    entered = query_one(
        """SELECT COUNT(*) AS n FROM entries en
             JOIN races r ON r.race_id = en.race_id WHERE r.event_id = ?""",
        (event_id,))["n"]
    if entered:
        # The cascade would take real entries with it. Closing is the move.
        flash(f"{event['name']} has {entered} entries, so it can't be deleted. "
              "Set it to closed instead.", "error")
        return redirect(url_for("admin.event_edit", event_id=event_id))
    with transaction() as conn:
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
    flash(f"{event['name']} deleted.", "ok")
    return redirect(url_for("admin.dashboard"))


# ── Races and their bands ────────────────────────────────
def _race_form(form):
    data = {
        "name": (form.get("name") or "").strip()[:120],
        "discipline": (form.get("discipline") or "").strip(),
        "swim_distance": (form.get("swim_distance") or "").strip()[:40],
        "bike_distance": (form.get("bike_distance") or "").strip()[:40],
        "run_distance": (form.get("run_distance") or "").strip()[:40],
        "start_time": (form.get("start_time") or "").strip()[:20],
        "capacity": _int_or_none(form.get("capacity")),
        "min_age": _int_or_none(form.get("min_age")),
        "sort_order": _int_or_none(form.get("sort_order")) or 0,
    }
    try:
        data["price_bhd"] = float((form.get("price_bhd") or "0").strip() or 0)
    except ValueError:
        data["price_bhd"] = -1
    errors = []
    if len(data["name"]) < 2:
        errors.append("Give the race a name.")
    if data["discipline"] not in DISCIPLINES:
        errors.append("Choose a discipline ITC actually runs.")
    if data["price_bhd"] < 0:
        errors.append("That price isn't a number.")
    if (form.get("capacity") or "").strip() and data["capacity"] is None:
        errors.append("Capacity has to be a whole number, or empty for no limit.")
    if data["capacity"] is not None and data["capacity"] < 1:
        errors.append("A capacity of 0 would mean nobody may enter. "
                      "Leave it empty for no limit.")
    return data, errors


@bp.route("/events/<int:event_id>/races/new", methods=["GET", "POST"])
def race_new(event_id):
    event = _get_event(event_id)
    if request.method == "POST":
        data, errors = _race_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/race_form.html", event=event,
                                   race=request.form, disciplines=DISCIPLINES,
                                   creating=True, bands_text=""), 400
        stamp = now()
        with transaction() as conn:
            race_id = conn.execute(
                """INSERT INTO races (event_id,name,discipline,swim_distance,
                        bike_distance,run_distance,start_time,capacity,
                        price_bhd,min_age,sort_order,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, data["name"], data["discipline"],
                 data["swim_distance"] or None, data["bike_distance"] or None,
                 data["run_distance"] or None, data["start_time"] or None,
                 data["capacity"], data["price_bhd"], data["min_age"],
                 data["sort_order"], stamp, stamp)).lastrowid
        flash(f"{data['name']} added. Set its age groups below.", "ok")
        return redirect(url_for("admin.race_edit", race_id=race_id))
    return render_template("admin/race_form.html", event=event,
                           race={"discipline": "triathlon", "price_bhd": 0},
                           disciplines=DISCIPLINES, creating=True, bands_text="")


@bp.route("/races/<int:race_id>", methods=["GET", "POST"])
def race_edit(race_id):
    race = _get_race(race_id)
    if request.method == "POST":
        data, errors = _race_form(request.form)
        bands, band_errors = parse_bands(request.form.get("bands") or "")
        errors += band_errors
        if errors:
            for e in errors:
                flash(e, "error")
            merged = dict(race); merged.update(request.form)
            return render_template("admin/race_form.html",
                                   event=_get_event(race["event_id"]),
                                   race=merged, disciplines=DISCIPLINES,
                                   creating=False,
                                   bands_text=request.form.get("bands") or "",
                                   entered=_entered_count(race_id)), 400
        with transaction() as conn:
            conn.execute(
                """UPDATE races SET name=?, discipline=?, swim_distance=?,
                       bike_distance=?, run_distance=?, start_time=?,
                       capacity=?, price_bhd=?, min_age=?, sort_order=?,
                       updated_at=? WHERE race_id=?""",
                (data["name"], data["discipline"], data["swim_distance"] or None,
                 data["bike_distance"] or None, data["run_distance"] or None,
                 data["start_time"] or None, data["capacity"],
                 data["price_bhd"], data["min_age"], data["sort_order"],
                 now(), race_id))
            moved = _replace_bands(conn, race_id, bands, race["event_date"])
        flash("Race saved." + (f" {moved} entries had their category recomputed."
                               if moved else ""), "ok")
        return redirect(url_for("admin.race_edit", race_id=race_id))

    return render_template("admin/race_form.html",
                           event=_get_event(race["event_id"]), race=race,
                           disciplines=DISCIPLINES, creating=False,
                           bands_text=bands_to_text(_bands_of(race_id)),
                           entered=_entered_count(race_id))


def _entered_count(race_id):
    return query_one(
        "SELECT COUNT(*) AS n FROM entries WHERE race_id=? AND status='confirmed'",
        (race_id,))["n"]


def _bands_of(race_id):
    return query_all(
        """SELECT age_group_id,label,gender,min_age,max_age FROM age_groups
           WHERE race_id=? ORDER BY sort_order, age_group_id""", (race_id,))


def bands_to_text(bands) -> str:
    return "\n".join(
        "{} | {} | {} | {}".format(b["label"],
                                   "" if b["min_age"] is None else b["min_age"],
                                   "" if b["max_age"] is None else b["max_age"],
                                   b["gender"])
        for b in bands)


def parse_bands(text: str):
    """`Label | min | max | gender` per line. Blank ends are open ends.

    A textarea rather than a dozen sub-forms: an organiser setting up a series
    has the bands written down somewhere already, and pasting seven lines beats
    seven rounds of "add another".
    """
    rows, errors = [], []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if parts else ""
        if not label:
            errors.append(f"Line {i}: every band needs a label.")
            continue
        low = _int_or_none(parts[1]) if len(parts) > 1 else None
        high = _int_or_none(parts[2]) if len(parts) > 2 else None
        gender = (parts[3].lower() if len(parts) > 3 and parts[3] else "any")
        if len(parts) > 1 and parts[1] and low is None:
            errors.append(f"Line {i}: '{parts[1]}' isn't an age.")
        if len(parts) > 2 and parts[2] and high is None:
            errors.append(f"Line {i}: '{parts[2]}' isn't an age.")
        if gender not in GENDERS:
            errors.append(f"Line {i}: gender must be any, male or female.")
            gender = "any"
        if low is not None and high is not None and low > high:
            errors.append(f"Line {i}: {label} starts after it ends.")
        rows.append({"label": label[:60], "min_age": low, "max_age": high,
                     "gender": gender})
    return rows, errors


def _replace_bands(conn, race_id, bands, event_date) -> int:
    """Rewrite a race's bands and recompute every entry that used them.

    This is the reason entries carry a date of birth next to their age group.
    Without the recompute, an organiser fixing a band boundary would leave
    start lists showing categories that no longer exist.
    """
    rule = get_setting("age_rule", "dec31")
    # Detach first: age_group_id is a foreign key, so the old rows cannot be
    # deleted while entries still point at them.
    conn.execute("UPDATE entries SET age_group_id = NULL WHERE race_id = ?",
                 (race_id,))
    conn.execute("DELETE FROM age_groups WHERE race_id = ?", (race_id,))
    for i, band in enumerate(bands):
        conn.execute(
            """INSERT INTO age_groups (race_id,label,gender,min_age,max_age,sort_order)
               VALUES (?,?,?,?,?,?)""",
            (race_id, band["label"], band["gender"], band["min_age"],
             band["max_age"], i))
    if not bands:
        return 0

    fresh = [dict(r) for r in conn.execute(
        """SELECT age_group_id,label,gender,min_age,max_age FROM age_groups
           WHERE race_id=? ORDER BY sort_order""", (race_id,))]
    moved = 0
    for entry in conn.execute(
            "SELECT entry_id,date_of_birth,gender FROM entries WHERE race_id=?",
            (race_id,)).fetchall():
        age = ages.age_on(entry["date_of_birth"], event_date, rule)
        group = ages.match_group(fresh, age, entry["gender"])
        if group:
            conn.execute("UPDATE entries SET age_group_id=?, updated_at=? "
                         "WHERE entry_id=?",
                         (group["age_group_id"], now(), entry["entry_id"]))
            moved += 1
    return moved


@bp.post("/races/<int:race_id>/delete")
def race_delete(race_id):
    race = _get_race(race_id)
    entered = query_one("SELECT COUNT(*) AS n FROM entries WHERE race_id=?",
                        (race_id,))["n"]
    if entered:
        flash(f"{race['name']} has {entered} entries, so it can't be deleted.",
              "error")
        return redirect(url_for("admin.race_edit", race_id=race_id))
    with transaction() as conn:
        conn.execute("DELETE FROM races WHERE race_id = ?", (race_id,))
    flash(f"{race['name']} deleted.", "ok")
    return redirect(url_for("admin.event_edit", event_id=race["event_id"]))


# ── Entrants ─────────────────────────────────────────────
_ENTRANT_SQL = """
    SELECT en.*, r.name AS race_name, r.discipline, r.start_time,
           ag.label AS age_group, g.reference, u.email AS account_email,
           u.full_name AS account_name
      FROM entries en
      JOIN races r ON r.race_id = en.race_id
      JOIN registrations g ON g.registration_id = en.registration_id
      JOIN users u ON u.user_id = g.user_id
      LEFT JOIN age_groups ag ON ag.age_group_id = en.age_group_id
     WHERE r.event_id = ?
"""


def _entrants(event_id, race_id=None, status=None, search=None):
    sql, params = _ENTRANT_SQL, [event_id]
    if race_id:
        sql += " AND r.race_id = ?"
        params.append(race_id)
    if status:
        sql += " AND en.status = ?"
        params.append(status)
    if search:
        # The reference is matched on its code only, plus whole-reference
        # equality. Every reference starts "ITC-", so a plain LIKE on the whole
        # thing makes a search for the club "ITC" return the entire event.
        sql += (" AND (lower(en.first_name) LIKE ? OR lower(en.last_name) LIKE ?"
                " OR lower(COALESCE(en.club,'')) LIKE ?"
                " OR lower(substr(g.reference, 5)) LIKE ?"
                " OR lower(g.reference) = ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like, like, search.lower()]
    sql += " ORDER BY r.sort_order, r.race_id, en.last_name, en.first_name"
    return query_all(sql, tuple(params))


@bp.get("/events/<int:event_id>/entrants")
def entrants(event_id):
    event = _get_event(event_id)
    race_id = _int_or_none(request.args.get("race"))
    status = request.args.get("status") or ""
    search = (request.args.get("q") or "").strip()
    rows = _entrants(event_id, race_id, status if status in ENTRY_STATUSES else None,
                     search)
    rule = get_setting("age_rule", "dec31")
    for row in rows:
        row["age"] = ages.age_on(row["date_of_birth"], event["event_date"], rule)
    return render_template(
        "admin/entrants.html", event=event, rows=rows,
        races=_races_of(event_id), race_id=race_id, status=status,
        search=search, statuses=ENTRY_STATUSES,
        counts={s: sum(1 for r in rows if r["status"] == s) for s in ENTRY_STATUSES})


@bp.get("/events/<int:event_id>/entrants.csv")
def entrants_csv(event_id):
    """One file with everything a timing company asks for.

    It carries dates of birth, emergency contacts and medical notes, so it is
    personal data leaving the building. The screen that offers it says so.
    """
    event = _get_event(event_id)
    race_id = _int_or_none(request.args.get("race"))
    status = request.args.get("status") or ""
    rows = _entrants(event_id, race_id,
                     status if status in ENTRY_STATUSES else None,
                     (request.args.get("q") or "").strip())
    rule = get_setting("age_rule", "dec31")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "bib", "race", "discipline", "start_time", "first_name", "last_name",
        "gender", "date_of_birth", "age", "age_group", "club", "shirt_size",
        "email", "phone", "emergency_name", "emergency_phone", "medical_notes",
        "guardian_name", "status", "reference", "account_name", "account_email",
        "waiver_accepted_at", "entered_at",
    ])
    for r in rows:
        writer.writerow([
            r["bib_number"] or "", r["race_name"], r["discipline"],
            r["start_time"] or "", r["first_name"], r["last_name"], r["gender"],
            r["date_of_birth"], ages.age_on(r["date_of_birth"], event["event_date"], rule),
            r["age_group"] or "", r["club"] or "", r["shirt_size"] or "",
            r["email"] or "", r["phone"] or "", r["emergency_name"] or "",
            r["emergency_phone"] or "", r["medical_notes"] or "",
            r["guardian_name"] or "", r["status"], r["reference"],
            r["account_name"], r["account_email"],
            r["waiver_accepted_at"] or "", r["created_at"],
        ])

    filename = f"{event['slug']}-entrants-{date.today().isoformat()}.csv"
    return Response(
        # The BOM is for Excel: without it, a name with an accent in it opens
        # as mojibake, and somebody retypes the whole start list by hand.
        "﻿" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"})


# ── Bibs and entry fixes ─────────────────────────────────
@bp.post("/races/<int:race_id>/bibs")
def assign_bibs(race_id):
    """Number a race in one go.

    Cancelled entries are skipped — a withdrawn athlete holding bib 14 means
    the pack handed out on the morning has a gap nobody can explain. Existing
    numbers are kept unless the organiser asks to redo them, because bibs get
    printed and a silent renumber would invalidate the print run.
    """
    race = _get_race(race_id)
    start = _int_or_none(request.form.get("start")) or 1
    prefix = (request.form.get("prefix") or "").strip()[:8]
    overwrite = bool(request.form.get("overwrite"))

    rows = query_all(
        """SELECT entry_id, bib_number FROM entries
           WHERE race_id = ? AND status <> 'cancelled'
           ORDER BY last_name, first_name, entry_id""", (race_id,))
    n, assigned = start, 0
    with transaction() as conn:
        for row in rows:
            if row["bib_number"] and not overwrite:
                continue
            conn.execute("UPDATE entries SET bib_number=?, updated_at=? WHERE entry_id=?",
                         (f"{prefix}{n}", now(), row["entry_id"]))
            n, assigned = n + 1, assigned + 1
    flash(f"{assigned} bib{'' if assigned == 1 else 's'} assigned on "
          f"{race['name']}." if assigned else
          "Every entry already had a bib — tick 'renumber' to redo them.",
          "ok" if assigned else "error")
    return redirect(url_for("admin.entrants", event_id=race["event_id"],
                            race=race_id))


@bp.post("/entries/<int:entry_id>")
def entry_update(entry_id):
    """Fix one entry: its bib, or its status.

    Status is here because waitlists and late withdrawals are phone calls, not
    self-service. Moving someone to confirmed is capacity-checked, otherwise an
    organiser clearing a waitlist could quietly oversubscribe the race.
    """
    entry = query_one(
        """SELECT en.*, r.event_id, r.capacity, r.name AS race_name
           FROM entries en JOIN races r ON r.race_id = en.race_id
           WHERE en.entry_id = ?""", (entry_id,))
    if entry is None:
        abort(404)

    bib = (request.form.get("bib_number") or "").strip()[:16]
    status = (request.form.get("status") or entry["status"]).strip()
    if status not in ENTRY_STATUSES:
        abort(400)

    if (status == "confirmed" and entry["status"] != "confirmed"
            and entry["capacity"] is not None):
        taken = query_one(
            "SELECT COUNT(*) AS n FROM entries WHERE race_id=? AND status='confirmed'",
            (entry["race_id"],))["n"]
        if taken >= entry["capacity"]:
            flash(f"{entry['race_name']} is full, so that entry can't be "
                  "confirmed. Raise the capacity first.", "error")
            return redirect(url_for("admin.entrants", event_id=entry["event_id"]))

    with transaction() as conn:
        conn.execute(
            """UPDATE entries SET bib_number=?, status=?,
                   cancelled_at = CASE WHEN ?='cancelled'
                        THEN COALESCE(cancelled_at, ?) ELSE NULL END,
                   updated_at=? WHERE entry_id=?""",
            (bib or None, status, status, now(), now(), entry_id))
    flash(f"{entry['first_name']} {entry['last_name']} updated.", "ok")
    return redirect(url_for("admin.entrants", event_id=entry["event_id"],
                            race=request.form.get("race") or None))
