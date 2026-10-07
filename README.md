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

**Email is off until you switch it on:**

- `ITC_EMAIL_ENABLED=1` — required before anything is actually sent. Unset,
  every message is logged with its subject and reported as "not sent", which
  is what you want on a box that already holds a working mail key.
- `BREVO_API_KEY` — shared from `~/.openclaw/.env`, same as the other apps.
- `ITC_MAIL_SENDER` — defaults to a BytesWell address because that is what
  Brevo has verified. **ITC's own domain must be verified in Brevo** before
  `events@internationaltriathlonclub.com` will send.

## Tests

```bash
.venv/bin/python -m pytest tests
```

## Accessibility and mobile gate

```bash
# needs the service up, and Playwright in whatever interpreter runs it
ITC_AUDIT_EMAIL=you@example.com ITC_AUDIT_PASSWORD=... \
  python tools/audit_a11y.py            # 360px, exits non-zero on findings
python tools/audit_a11y.py --width 320  # as small as real phones get
```

Twelve pages, checked rather than eyeballed: viewport overflow, 40px tap
targets, accessible names, heading order, landmarks, AA contrast against the
*composited* background, and a keyboard pass (first tab stop is the skip link,
it reaches `#main`, every stop keeps a focus ring). Currently **0 findings at
320px and 360px**.

The first run found 65. Almost all of it was six root causes repeated across
pages — see the comments in `static/css/app.css` next to `--tide-dk`,
`--sun-cta` and `--muted`, which record the measured ratios rather than the
intention.

## Backups

```bash
.venv/bin/python tools/backup.py        # → backups/itc-YYYYmmdd-HHMMSS.db.gz
ITC_BACKUP_DIR=/srv/backups ITC_BACKUP_KEEP=30 .venv/bin/python tools/backup.py
```

Uses SQLite's online-backup API, **not** a file copy: the app runs in WAL mode,
so copying the file mid-write gives a torn database plus a `-wal` you did not
copy. Each backup is opened and `PRAGMA integrity_check`ed before it is kept,
and rejected if a table is missing — a backup nobody opened is a guess. Keeps
14 by default, 0600 in a 0700 directory, because entry data is personal.

To restore: `gunzip -c backups/itc-….db.gz > itc.db` with the service stopped.

A nightly timer ships but is **not installed** — adding a scheduler to your
machine is your call:

```bash
cp itc-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now itc-backup.timer
systemctl --user list-timers itc-backup.timer
```

## Deploying a change

```bash
git pull
systemctl --user restart itc-app
journalctl --user -u itc-app -n 30
curl -s localhost:9797/healthz
```

The schema upgrades itself on boot (`init_db` runs `CREATE TABLE IF NOT EXISTS`
plus the additive `_migrate`), so there is no migration step. Take a backup
first anyway if the release touches `db.py`.

Before a release that matters: `pytest`, then the audit above against the
running service.

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

## After entering

`/my-races` lists what the account has entered, grouped by reference, upcoming
first. `/my-races/<reference>` is the entry itself.

- **Withdrawal is per entry, not per registration.** A parent who entered three
  children and has one with a broken wrist pulls that one.
- **Asking happens on its own page.** A `confirm()` dialog would be an inline
  handler and the CSP blocks those — the dialog would silently never appear and
  a mis-tap would withdraw someone. GET asks, POST acts, no script involved.
- **A cancelled entry is kept, not deleted.** It is the record that somebody
  accepted a waiver and then withdrew. Capacity counts confirmed entries, so
  the place comes back the moment it is cancelled.

## Organiser area

`/admin`, behind `role = 'admin'`. Reached from the account page rather than a
fourth item in the main header — that bar already overflowed a 390px screen
once.

```bash
.venv/bin/python make_admin.py liam@example.com   # promote, after they register
.venv/bin/python make_admin.py --list             # who is an organiser
.venv/bin/python make_admin.py --demote EMAIL
```

There is deliberately no way to mint an organiser through the web.

- **Age bands are edited as text** — `Label | from | to | gender`, one per line,
  blank ends meaning open ends. Seven lines pasted beats seven rounds of "add
  another".
- **Saving bands recomputes every entry on that race** from the date of birth
  it has carried since Phase 4. Without that, fixing a boundary leaves start
  lists showing categories that no longer exist.
- **Deleting is refused once anyone has entered** — close the event instead.
- **Bibs** are assigned in surname order, skipping withdrawn entries, and
  existing numbers are kept unless you tick renumber. Bibs get printed.
- **The CSV** is the deliverable for the timing company. It carries dates of
  birth, emergency contacts and medical notes — personal data leaving the
  building, and the screen says so. UTF-8 with a BOM, because Excel mangles
  accented names without it and somebody retypes the start list by hand.

## Status

Phases 1–7 of 8 complete — foundation, public event browsing, accounts,
registration flow, confirmation and withdrawal, organiser admin, and the
mobile/accessibility/ops pass. 130 tests; 0 audit findings at 320px and 360px.

**Ready for a real entry list, with two things still owed by ITC:**

1. **The real race calendar.** Everything `seed_events.py` writes is sample
   data and says so in each event summary.
2. **The waiver wording** in `templates/_waiver.html`, and a decision on the
   `age_rule` setting — `dec31` (World Triathlon) is the shipped default and
   has not been confirmed.

Phase 8 is payments; `races.price_bhd` has been in the schema since Phase 1 and
is 0 everywhere, so it is a feature rather than a migration. See PLAN.md.
