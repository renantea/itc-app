"""Seed the calendar with sample events so the pages have something to show.

    .venv/bin/python seed_events.py

Idempotent: an event whose slug already exists is left alone, so running this
twice is safe and editing a seeded event in the app is not undone.

EVERY EVENT HERE IS A PLACEHOLDER except the Eid Aquathlon name and venue,
which come from the club's own blog post. Dates, distances, start times and
capacities are invented and must be replaced with ITC's real calendar before
anyone is invited to enter. The `is_sample` marker in the summary is there so
nobody mistakes seeded data for the real thing.
"""
from datetime import date, timedelta

from itc.db import DB_PATH, connect, init_db, now

SAMPLE_TAG = "SAMPLE DATA — replace with ITC's real calendar"


def _d(days_ahead: int) -> str:
    return (date.today() + timedelta(days=days_ahead)).isoformat()


# Standard-ish bands. ITC can edit these per race once admin exists (Phase 6).
STANDARD_AGE_GROUPS = [
    ("Junior 13-15", "any", 13, 15),
    ("Youth 16-19", "any", 16, 19),
    ("20-29", "any", 20, 29),
    ("30-39", "any", 30, 39),
    ("40-49", "any", 40, 49),
    ("50-59", "any", 50, 59),
    ("60+", "any", 60, None),
]

JUNIOR_AGE_GROUPS = [
    ("Tri-Star 8-9", "any", 8, 9),
    ("Tri-Star 10-11", "any", 10, 11),
    ("Tri-Star 12-13", "any", 12, 13),
]

EVENTS = [
    {
        "slug": "itc-eid-aquathlon-asb",
        "name": "ITC Eid Aquathlon",
        "summary": "A swim-and-run celebration open to athletes of every level.",
        "description": (
            "Come and join us at the American School Bahrain for a swim-and-run "
            "event built for the whole family. Whether you're chasing a fast time "
            "or lining up for your very first race, there's a distance and a "
            "welcome for you."),
        "venue": "American School Bahrain",
        "event_date": _d(28),
        "status": "open",
        "races": [
            {"name": "Aquathlon — Standard", "discipline": "aquathlon",
             "swim_distance": "750 m", "run_distance": "5 km",
             "start_time": "06:30", "capacity": 120, "min_age": 16,
             "age_groups": STANDARD_AGE_GROUPS},
            {"name": "Aquathlon — Sprint", "discipline": "aquathlon",
             "swim_distance": "400 m", "run_distance": "2.5 km",
             "start_time": "07:30", "capacity": 150, "min_age": 13,
             "age_groups": STANDARD_AGE_GROUPS},
            {"name": "Junior Aquathlon", "discipline": "aquathlon",
             "swim_distance": "100 m", "run_distance": "1 km",
             "start_time": "08:30", "capacity": 80, "min_age": 8,
             "age_groups": JUNIOR_AGE_GROUPS},
        ],
    },
    {
        "slug": "itc-spring-triathlon",
        "name": "ITC Spring Triathlon",
        "summary": "Swim, bike, run — the club's main sprint-distance race.",
        "description": (
            "The club's flagship sprint triathlon. Pool swim, a flat fast bike "
            "leg and a two-lap run. Relay teams of three welcome."),
        "venue": "To be confirmed",
        "event_date": _d(63),
        "status": "open",
        "races": [
            {"name": "Sprint Triathlon", "discipline": "triathlon",
             "swim_distance": "750 m", "bike_distance": "20 km",
             "run_distance": "5 km", "start_time": "06:00",
             "capacity": 200, "min_age": 16, "age_groups": STANDARD_AGE_GROUPS},
            {"name": "Super Sprint Triathlon", "discipline": "triathlon",
             "swim_distance": "400 m", "bike_distance": "10 km",
             "run_distance": "2.5 km", "start_time": "07:15",
             "capacity": 150, "min_age": 14, "age_groups": STANDARD_AGE_GROUPS},
        ],
    },
    {
        "slug": "itc-duathlon-series-r1",
        "name": "ITC Duathlon Series — Round 1",
        "summary": "Run, bike, run. No pool required.",
        "description": "First round of the winter duathlon series. Run, bike, run.",
        "venue": "To be confirmed",
        "event_date": _d(91),
        "status": "open",
        "races": [
            {"name": "Standard Duathlon", "discipline": "duathlon",
             "run_distance": "5 km + 2.5 km", "bike_distance": "20 km",
             "start_time": "07:00", "capacity": 120, "min_age": 16,
             "age_groups": STANDARD_AGE_GROUPS},
        ],
    },
    {
        "slug": "itc-corniche-5k",
        "name": "ITC Corniche 5K",
        "summary": "A flat, fast, friendly 5K. Walkers welcome.",
        "description": "A flat and fast 5K along the corniche. Runners and walkers.",
        "venue": "To be confirmed",
        "event_date": _d(14),
        "status": "open",
        "races": [
            {"name": "5K Run", "discipline": "running", "run_distance": "5 km",
             "start_time": "06:30", "capacity": None, "min_age": 10,
             "age_groups": STANDARD_AGE_GROUPS},
        ],
    },
    {
        # A past event, so the list has something to prove it hides finished races.
        "slug": "itc-winter-aquathlon-2026",
        "name": "ITC Winter Aquathlon",
        "summary": "Completed — results with the timing company.",
        "description": "Last season's winter aquathlon.",
        "venue": "American School Bahrain",
        "event_date": _d(-35),
        "status": "completed",
        "races": [
            {"name": "Aquathlon — Sprint", "discipline": "aquathlon",
             "swim_distance": "400 m", "run_distance": "2.5 km",
             "start_time": "07:00", "capacity": 150, "min_age": 13,
             "age_groups": STANDARD_AGE_GROUPS},
        ],
    },
]


def main():
    init_db()
    conn = connect()
    added_events = added_races = 0
    try:
        for ev in EVENTS:
            if conn.execute("SELECT 1 FROM events WHERE slug = ?",
                            (ev["slug"],)).fetchone():
                continue
            stamp = now()
            event_id = conn.execute(
                """INSERT INTO events (slug, name, summary, description, venue,
                        event_date, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (ev["slug"], ev["name"], f"{ev['summary']}  [{SAMPLE_TAG}]",
                 ev["description"], ev["venue"], ev["event_date"], ev["status"],
                 stamp, stamp)).lastrowid
            added_events += 1

            for order, race in enumerate(ev["races"]):
                race_id = conn.execute(
                    """INSERT INTO races (event_id, name, discipline, swim_distance,
                            bike_distance, run_distance, start_time, capacity,
                            price_bhd, min_age, sort_order, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                    (event_id, race["name"], race["discipline"],
                     race.get("swim_distance"), race.get("bike_distance"),
                     race.get("run_distance"), race.get("start_time"),
                     race.get("capacity"), race.get("min_age"), order,
                     stamp, stamp)).lastrowid
                added_races += 1
                for n, (label, gender, lo, hi) in enumerate(race["age_groups"]):
                    conn.execute(
                        """INSERT INTO age_groups (race_id, label, gender,
                                min_age, max_age, sort_order)
                           VALUES (?,?,?,?,?,?)""",
                        (race_id, label, gender, lo, hi, n))
        conn.commit()
    finally:
        conn.close()

    print(f"Seeded {added_events} event(s), {added_races} race(s) into {DB_PATH}.")
    if added_events:
        print(f"NOTE: {SAMPLE_TAG}.")


if __name__ == "__main__":
    main()
