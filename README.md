# ITC Events

Race registration for the International Triathlon Club — running, duathlon,
triathlon and aquathlon. Flask + SQLite + gunicorn, the same stack as the
other apps on this box.

Build plan and open questions: [PLAN.md](PLAN.md).

## Run

```bash
./run.sh                               # dev,  http://127.0.0.1:9797
gunicorn -b 127.0.0.1:9797 wsgi:app    # prod, behind a tunnel
```

## Service

Runs as the **user** systemd unit `itc-app` on `127.0.0.1:9797`.

```bash
systemctl --user status itc-app
systemctl --user restart itc-app       # after a pull
journalctl --user -u itc-app -n 50
```

There is no system-level unit — `sudo systemctl restart itc-app` will not find
it. Surviving a reboot needs `loginctl enable-linger $USER` as well as an
enabled unit (already on for this account).

## Config (`.env`, not committed)

- `ITC_SECRET_KEY` — session secret. Without it the app invents one per start
  and signs everyone out on each restart.
- `ITC_ENV` — `development` | `production`.
- `ITC_DATABASE` — defaults to `./itc.db`.

## Tests

```bash
.venv/bin/python -m pytest tests
```

## Shape of the data

The split that matters is **registrations vs entries**. One parent entering
three children is one registration and three entries. Capacity, start lists and
the timing company's export all count entries, because an entry is one human on
one start line.

`races.price_bhd` exists and is 0 everywhere: payments are a later phase, and
adding the column now means that phase is a feature rather than a migration of
live registrations.

## Seed data

```bash
.venv/bin/python seed_events.py        # idempotent
```

Everything it writes is **sample data** and is tagged as such in each event's
summary. Only the Eid Aquathlon's name and venue are real (from the club's own
blog post); dates, distances, start times and capacities are invented and must
be replaced with ITC's calendar before anyone is invited to enter.

## Entering a race

`/enter/<race_id>` → party size → one screen per athlete → review → confirm.

- **Progress is a database row** (`entry_drafts`), not the session cookie. Four
  athletes with medical notes exceed the 4 KB a cookie holds, and an oversized
  cookie is dropped silently — which looks like the app eating your typing.
  Only an opaque token goes in the session. Abandoned drafts are swept after
  seven days.
- **Nothing is written until Confirm.** Capacity, duplicates and eligibility are
  re-checked at that moment inside one `BEGIN IMMEDIATE` transaction, so two
  people cannot take the same last place.
- **Age groups are derived, never chosen** — see `itc/ages.py`. The convention
  is the `age_rule` setting: `dec31` (World Triathlon, the default) or
  `race_day` (Ironman). **ITC has not confirmed which they use.** Changing the
  setting is enough; every entry keeps its date of birth, so categories can be
  recomputed.
- **The waiver in `templates/_waiver.html` is a placeholder.** The app records
  when each athlete accepted it, and that record is worth only what the text
  says. ITC's own wording must replace it before entries go live.

## Status

Phases 1–4 of 8 complete — foundation, public event browsing, accounts,
registration flow. Next: confirmation email and "my registrations" (Phase 5).
See PLAN.md.
