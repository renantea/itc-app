"""Public pages: what's on, and what's in each event.

Read-only. Entry itself arrives in Phase 4 — every "Enter" control here is
deliberately inert and says so, because a button that looks live and does
nothing is worse than one that admits it isn't ready.
"""
from datetime import date

from flask import Blueprint, abort, render_template

from itc.db import query_all, query_one

bp = Blueprint("pages", __name__)

#: Statuses a visitor may see. 'draft' is ITC's own workspace.
PUBLIC_STATUSES = ("open", "closed", "completed")


def _event_card_rows(upcoming: bool):
    """Events for the listing, newest-first for past, soonest-first for future.

    `today` is passed in as a bound parameter rather than using SQLite's
    date('now'): that is UTC, and a race day here is a Bahrain calendar day, so
    at 02:00 local the two disagree and an event vanishes a day early.
    """
    today = date.today().isoformat()
    comparison = ">=" if upcoming else "<"
    order = "ASC" if upcoming else "DESC"
    return query_all(
        f"""
        SELECT e.event_id, e.slug, e.name, e.summary, e.venue, e.event_date,
               e.status,
               (SELECT COUNT(*) FROM races r WHERE r.event_id = e.event_id)
                   AS race_count,
               (SELECT GROUP_CONCAT(DISTINCT r.discipline) FROM races r
                 WHERE r.event_id = e.event_id) AS disciplines
        FROM events e
        WHERE e.status IN ('open', 'closed', 'completed')
          AND e.event_date {comparison} ?
        ORDER BY e.event_date {order}
        """, (today,))


@bp.get("/")
def home():
    return render_template("home.html",
                           events=_event_card_rows(upcoming=True),
                           past=_event_card_rows(upcoming=False))


@bp.get("/events/<slug>")
def event(slug):
    ev = query_one(
        "SELECT * FROM events WHERE slug = ? AND status IN ('open','closed','completed')",
        (slug,))
    if ev is None:
        abort(404)

    # Places left is confirmed entries against capacity. Counted here only to
    # *show* a number; the authoritative check happens inside the transaction
    # that writes an entry (Phase 4), because anything read at render time is
    # already stale by the time someone presses the button.
    races = query_all(
        """
        SELECT r.*,
               (SELECT COUNT(*) FROM entries en
                 WHERE en.race_id = r.race_id AND en.status = 'confirmed') AS taken
        FROM races r
        WHERE r.event_id = ?
        ORDER BY r.sort_order, r.race_id
        """, (ev["event_id"],))

    groups = {}
    if races:
        marks = ",".join("?" * len(races))
        for row in query_all(
                f"""SELECT race_id, label, gender, min_age, max_age
                    FROM age_groups WHERE race_id IN ({marks})
                    ORDER BY race_id, sort_order""",
                tuple(r["race_id"] for r in races)):
            groups.setdefault(row["race_id"], []).append(row)

    for race in races:
        cap = race["capacity"]
        race["places_left"] = None if cap is None else max(cap - race["taken"], 0)
        race["is_full"] = cap is not None and race["taken"] >= cap
        race["age_groups"] = groups.get(race["race_id"], [])

    return render_template("event.html", event=ev, races=races,
                           is_past=ev["event_date"] < date.today().isoformat())
