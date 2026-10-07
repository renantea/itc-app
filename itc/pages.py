"""Public pages.

Phase 1 is the shell: a styled home page that proves the stack is wired end to
end. Phase 2 fills it with real events.
"""
from flask import Blueprint, render_template

from itc.db import query_all

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    # Reads through the real query path rather than a placeholder, so Phase 2
    # only has to add rows and a template — not discover that the wiring was
    # never exercised.
    events = query_all(
        """
        SELECT e.event_id, e.slug, e.name, e.summary, e.venue, e.event_date,
               e.status,
               (SELECT COUNT(*) FROM races r WHERE r.event_id = e.event_id) AS race_count
        FROM events e
        WHERE e.status IN ('open', 'closed')
        ORDER BY e.event_date
        """)
    return render_template("home.html", events=events)
