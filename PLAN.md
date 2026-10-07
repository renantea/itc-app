# ITC Events — build plan

A race-registration app for International Triathlon Club. Running, duathlon,
triathlon and aquathlon. Free entry now, paid entry later.

**Nothing has been built yet.** This document is the plan; each phase below is
a separate instruction, so a long build can't be cut off half-finished.

- Stack: Python + Flask + SQLite + gunicorn — the same as go2silog-ops,
  madison-2026 and the rest, so it deploys and is maintained the same way.
- Service: user systemd unit `itc-app`, bound to `127.0.0.1:9797` (port is free).
- Repo: `/home/sysad/.openclaw/workspace/projects/itc-app`.

---

## What I checked, and what I couldn't

**Tathaker** renders entirely in the browser and currently lists no live
events, so I could not read their registration mechanics — only that it is a
general Bahrain **ticketing** site: browse events → event detail → *Buy
Tickets*. Worth saying plainly, because ticketing and race registration are
different problems:

| Ticketing (Tathaker) | Race registration (what ITC needs) |
|---|---|
| Buyer identity is enough | Every **entrant** needs their own record |
| Quantity × price | Age group, date of birth, gender per entrant |
| No eligibility rules | Category derived from age; cut-off dates |
| No safety data | Emergency contact, medical notes, waiver |
| Ticket is the output | Bib number, start wave, results later |

So I have modelled this on how race entry actually works (Ironman, World
Triathlon, parkrun) rather than copying a ticket shop. If you can get me a
screenshot of a live Tathaker checkout, I'll fold anything useful back in.

**The existing ITC site** (`projects/itc-astro`) already has a brand, and it
already points "Events" at `itcevents.internationaltriathlonclub.com` — this
app is presumably the replacement for that.

---

## Three decisions I need from you

These change the build, so I'd rather ask than guess.

**1. Is the age group chosen, or worked out?**
Your note says the participant *selects* an age group. In real race entry the
age group is **derived from date of birth**, precisely so nobody can enter an
easier category. The two conventions differ:

- **World Triathlon / USAT** — age as of **31 December of the race year**
- **Ironman** — age **on race day**

My recommendation: collect date of birth, compute the category, show it
read-only ("Your category: M35–39"). Tell me which rule ITC follows. *(I
couldn't verify the governing-body wording today — web search was down — so
please confirm rather than take my word for it.)*

**2. Who is the account holder?**
"Register on a participant's behalf" — is the account holder usually a parent
or a team captain? It decides whether the account holder is always entrant #1,
or purely an organiser who may not race at all. I'd build the second: the
account holder is an organiser, and adds themselves as an entrant only if they
want to.

**3. Waiver wording.**
Race entry normally requires accepting a liability waiver **per entrant**, and
a guardian signature for under-18s. I'll leave a placeholder, but it needs real
text from ITC before the first live event.

---

## Data model

```
users              the account that registers (organiser or athlete)
events             "ITC Spring Series — Round 2", a date, a venue, a status
races              what you can enter within an event: discipline, distances,
                   capacity, price (0 for now)
age_groups         bands per race: label, gender, min/max age
registrations      one order — who registered, when, status
entries            ONE ROW PER ATHLETE: name, DOB, gender, computed age group,
                   emergency contact, waiver, shirt size, club, bib number
```

The split that matters is **registrations vs entries**. One parent registering
three children is one registration and three entries. Everything operational —
capacity, start lists, the timing company's export — works off entries.

**Statuses:** event `draft → open → closed → completed`; entry
`confirmed → cancelled`, plus `waitlisted` once capacity bites.

---

## Phases

Each is self-contained and ends somewhere sensible. Say "do phase N" and I'll
build, test and restart it.

### Phase 1 — Foundation
Repo layout, app factory, config, SQLite schema + migration runner, systemd
unit on 9797, base layout, theme tokens, `/healthz`.
*Done when:* the service runs under systemd and serves a styled empty shell.

### Phase 2 — Events, publicly
Event list and event detail (races, distances, dates, capacity remaining),
seeded with real ITC events. Read-only, no accounts yet.
*Done when:* a visitor can browse what's on, on a phone.

### Phase 3 — Accounts
Register, log in, log out, password hashing, sessions, CSRF. Reuses the
auth shape from the other apps rather than inventing one.
*Done when:* an account can be created and signed back into.

### Phase 4 — The registration flow ← *the heart of it*
Pick race → how many entrants → details per entrant → review → confirm.
Capacity checks, duplicate-entry guard, age-group derivation, waiver per
entrant, progress saved between steps.
*Done when:* one account can enter a family of four in one go.

### Phase 5 — Confirmation and "My registrations"
Confirmation screen + email, entrant list per account, cancel an entry,
free the place back to capacity.
*Done when:* an entrant gets written proof and can withdraw.

### Phase 6 — Organiser admin
Event/race/age-group CRUD, entrant list with filters, capacity dashboard,
**CSV export for the timing company**, bib assignment.
*Done when:* ITC can run an event start to finish without touching the DB.

### Phase 7 — Polish
Mobile pass at 360px, keyboard and screen-reader pass, test suite, seed data,
deploy and backup notes.
*Done when:* the suite is green and it's ready for a real entry list.

### Phase 8 — Payments *(deferred, as you said)*
Price per race, checkout, refunds on cancellation. The schema carries a
`price` column from Phase 1 so this does not need a rebuild — it stays 0.00
until you want it.

---

## Look and feel

You asked for strong, vibrant, endurance blue. ITC already has a palette on
the Astro site, and I'd **reuse it** rather than invent a second ITC look:

| | |
|---|---|
| `#0a1826` | deep water navy — the base |
| `#10273c` | raised surface |
| `#14e0c4` | tidal teal — the energy accent |
| `#ff5b41` | sunrise coral — effort, used for the one action that matters |

Deep navy carries the weight, teal is the vibrant note, coral is reserved for
the primary action ("Enter this race") so the thing you want pressed is the one
colour nothing else uses. If you'd rather go bluer and drop the coral, say so
in Phase 1 — it's a token change, not a rewrite.

**Mobile first.** Most race entries are made on a phone, often on the day.
Single column, large tap targets, the entry wizard one step per screen.

---

## Operational notes

- Backups: nightly SQLite copy, same pattern as the other apps.
- Entrant data is personal (DOB, emergency contacts, medical notes) — admin
  pages behind a role check, no entrant data in logs, and a retention position
  to agree before the first live event.
- Capacity is enforced **at the point of writing the entry**, not when the page
  is rendered, so two people racing for the last place can't both get it.

---

*Plan only — no application code has been written yet.*
