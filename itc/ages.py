"""Age groups: worked out, never chosen.

An entrant gives a date of birth; the category falls out of it. That is how
race entry works everywhere, and the reason is simple — if the athlete picks
the band, someone eventually picks an easier one.

Two conventions are in use and they disagree by up to a year:

  dec31     age as of 31 December of the race year — World Triathlon, USAT,
            British Triathlon. Everyone born in the same year races the same
            category all season.
  race_day  age on the morning of the race — Ironman.

Which one ITC follows is a `settings` row (`age_rule`), not a constant here,
because the answer changes the category printed on a start list and I have not
had it confirmed. `dec31` is the default: ITC is a triathlon club and the
World Triathlon rule is the one its series would inherit.
"""
from datetime import date

#: Both conventions, for validation and for the admin screen in Phase 6.
AGE_RULES = ("dec31", "race_day")

#: Nobody competing today was born before this. A typo'd year (1066, 19) is
#: far more likely than a 105-year-old aquathlete, and the error message is
#: kinder than a category of "M-840".
EARLIEST_BIRTH_YEAR = 1920


def parse_iso(value) -> date:
    """A YYYY-MM-DD string as a date, or None if it isn't one.

    Browsers send `type=date` as ISO, but this also runs against hand-typed
    input and against whatever is sitting in a saved draft.
    """
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def age_on(dob, event_date, rule: str = "dec31") -> int:
    """The entrant's competition age for this race.

    `dec31` deliberately ignores the day and month: the rule is "age you turn
    this year", so a December baby and a January baby of the same year are the
    same category.
    """
    dob = parse_iso(dob)
    event_date = parse_iso(event_date)
    if dob is None or event_date is None:
        return None
    if rule == "race_day":
        had_birthday = (event_date.month, event_date.day) >= (dob.month, dob.day)
        return event_date.year - dob.year - (0 if had_birthday else 1)
    return event_date.year - dob.year


def covers(group, age: int) -> bool:
    """Does this band include that age? An open end means open."""
    low = group["min_age"]
    high = group["max_age"]
    if low is not None and age < low:
        return False
    if high is not None and age > high:
        return False
    return True


def match_group(groups, age: int, gender: str):
    """The band an entrant of this age and gender belongs to, or None.

    Age decides first, gender only breaks the tie. The fallbacks matter:

      * an exact gender match wins;
      * then a band marked `any`;
      * then the first band covering the age, whatever its gender.

    That last step exists for `other` on a race with only male and female
    bands. Putting them in a band an organiser can correct is better than
    refusing the entry of someone whose gender the age groups didn't
    anticipate. None is returned only when no band covers the age at all —
    which is a genuine eligibility answer, not a data gap.
    """
    if age is None:
        return None
    fits = [g for g in groups if covers(g, age)]
    if not fits:
        return None
    for g in fits:
        if g["gender"] == gender:
            return g
    for g in fits:
        if g["gender"] == "any":
            return g
    return fits[0]


def band_summary(groups) -> str:
    """"10–14, 15–19, 20+" — for telling someone what the race does accept."""
    parts = []
    for g in groups:
        low, high = g["min_age"], g["max_age"]
        if low is not None and high is not None:
            parts.append(f"{low}–{high}")
        elif low is not None:
            parts.append(f"{low}+")
        elif high is not None:
            parts.append(f"under {high + 1}")
    # dict.fromkeys: de-duplicated, order kept (gendered bands repeat the span).
    return ", ".join(dict.fromkeys(parts))
