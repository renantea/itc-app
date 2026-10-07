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

## Status

Phases 1–2 of 8 complete — foundation, and public event browsing. See PLAN.md.
