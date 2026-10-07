"""Phase 5: what you entered, and getting back out of it.

Two jobs:

**Proof.** After confirming, an entrant needs something durable. The screen is
the record; the email is a copy of it, and the email is explicitly allowed to
fail without touching the entry (see `itc.mail`).

**Withdrawal.** A race that cannot be left fills up with people who are not
coming, and the organiser finds out on race morning. Cancelling an entry frees
its place immediately, because capacity counts confirmed entries and nothing
else.

Withdrawal is per **entry**, not per registration: a parent who entered three
children and has one with a broken wrist should pull that one, not all three.
"""
from datetime import date

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)

from itc import mail
from itc.auth import login_required
from itc.db import now, query_all, query_one, transaction

bp = Blueprint("registrations", __name__, url_prefix="/my-races")

#: Columns every view of an entry needs, joined to the race it is on.
_ENTRY_SELECT = """
    SELECT en.*, ra.name AS race_name, ra.discipline, ra.start_time,
           ra.swim_distance, ra.bike_distance, ra.run_distance,
           ag.label AS age_group,
           ev.name AS event_name, ev.event_date, ev.venue, ev.slug,
           g.reference, g.registration_id, g.created_at AS entered_at
    FROM entries en
    JOIN registrations g ON g.registration_id = en.registration_id
    JOIN races ra        ON ra.race_id = en.race_id
    JOIN events ev       ON ev.event_id = g.event_id
    LEFT JOIN age_groups ag ON ag.age_group_id = en.age_group_id
"""


def _my_entries(where: str, params: tuple):
    return query_all(f"{_ENTRY_SELECT} WHERE g.user_id = ? {where}",
                     (g.user["user_id"],) + params)


@bp.get("")
@login_required
def index():
    """Everything this account has entered, next race first."""
    today = date.today().isoformat()
    upcoming = _my_entries(
        "AND ev.event_date >= ? ORDER BY ev.event_date, ra.sort_order, en.entry_id",
        (today,))
    past = _my_entries(
        "AND ev.event_date < ? ORDER BY ev.event_date DESC, en.entry_id", (today,))
    # Grouped by registration so one order reads as one order, with its
    # reference, rather than as a pile of loose athletes.
    return render_template("my_races.html",
                           upcoming=_by_registration(upcoming),
                           past=_by_registration(past))


def _by_registration(rows):
    orders = {}
    for row in rows:
        orders.setdefault(row["reference"], {
            "reference": row["reference"],
            "event_name": row["event_name"],
            "event_date": row["event_date"],
            "venue": row["venue"],
            "slug": row["slug"],
            "entries": [],
        })["entries"].append(row)
    return list(orders.values())


@bp.get("/<reference>")
@login_required
def detail(reference):
    entries = _my_entries("AND g.reference = ? ORDER BY en.entry_id", (reference,))
    if not entries:
        # 404 rather than 403: whether somebody else's reference exists is not
        # this account's business.
        abort(404)
    event = entries[0]
    return render_template(
        "my_race_detail.html", reference=reference, entries=entries, event=event,
        is_past=event["event_date"] < date.today().isoformat(),
        live=[e for e in entries if e["status"] != "cancelled"])


@bp.route("/<reference>/withdraw/<int:entry_id>", methods=["GET", "POST"])
@login_required
def withdraw(reference, entry_id):
    """Take one athlete off the start list and give the place back.

    Two steps on purpose. A `confirm()` dialog would be an inline handler, and
    this app's CSP blocks those — the dialog would silently never appear and a
    mis-tap on a phone would withdraw someone. A GET that asks, and a POST that
    acts, needs no script at all.
    """
    entry = query_one(
        f"{_ENTRY_SELECT} WHERE en.entry_id = ? AND g.user_id = ? AND g.reference = ?",
        (entry_id, g.user["user_id"], reference))
    if entry is None:
        abort(404)

    if request.method == "GET":
        if entry["status"] == "cancelled" or \
                entry["event_date"] < date.today().isoformat():
            return redirect(url_for("registrations.detail", reference=reference))
        return render_template("withdraw_confirm.html", entry=entry,
                               reference=reference)

    if entry["status"] == "cancelled":
        flash("That entry was already withdrawn.", "error")
    elif entry["event_date"] < date.today().isoformat():
        # Nothing to free, and the start list is now a historical record.
        flash("That race has already taken place, so it can't be withdrawn from.",
              "error")
    else:
        with transaction() as conn:
            conn.execute(
                """UPDATE entries SET status = 'cancelled', cancelled_at = ?,
                       updated_at = ? WHERE entry_id = ? AND status <> 'cancelled'""",
                (now(), now(), entry_id))
        flash(f"{entry['first_name']} {entry['last_name']} has been withdrawn "
              f"from {entry['race_name']}. The place is back in the pool.", "ok")
    return redirect(url_for("registrations.detail", reference=reference))


# ── The confirmation email ───────────────────────────────
def send_confirmation(reference) -> tuple:
    """Email the account holder their entry. Returns (sent, info).

    Called after the entry is already written and committed. Mail problems are
    reported, never raised — see the note in `itc.mail`.
    """
    entries = _my_entries("AND g.reference = ? ORDER BY en.entry_id", (reference,))
    if not entries:
        return False, "no such registration"
    try:
        html = render_template("email_confirmation.html", reference=reference,
                               entries=entries, event=entries[0],
                               user=g.user)
        subject = f"You're entered — {entries[0]['event_name']} ({reference})"
        return mail.send(g.user["email"], g.user["full_name"], subject, html)
    except Exception as exc:                       # noqa: BLE001 — see docstring
        current_app.logger.exception("Confirmation email failed")
        return False, str(exc)
