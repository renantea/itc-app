"""Region, currency and timezone — configurable so the app is not tied to one
country.

The app started Bahrain-only: prices were printed as ``BHD`` with three
decimals and "today" was whatever the server's clock said, assuming the server
ran on Bahrain time. Both are now driven by settings so the same code runs a
Philippine series (PHP, two decimals, Asia/Manila) without a fork.

Settings read here (all have Bahrain-compatible defaults, so an existing
database keeps behaving exactly as before):

- ``currency``        ISO code, e.g. ``BHD`` or ``PHP``. Default ``BHD``.
- ``app_timezone``    IANA zone used to decide the local calendar day, e.g.
                      ``Asia/Bahrain`` or ``Asia/Manila``. Default
                      ``Asia/Bahrain``.

Currency decimals are not a setting: they are a fixed property of the currency
(BHD always has 3, PHP always 2), so they live in the table below and are
looked up from the chosen code.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: code -> (symbol/prefix shown before the amount, decimal places).
#: The prefix is what reads naturally in the club's context; for most of these
#: the ISO code itself is clearest, so that is what we show.
CURRENCIES = {
    "BHD": ("BHD", 3),
    "PHP": ("₱", 2),   # peso sign
    "USD": ("$", 2),
    "EUR": ("€", 2),
    "AED": ("AED", 2),
    "SAR": ("SAR", 2),
    "GBP": ("£", 2),
}

DEFAULT_CURRENCY = "BHD"
DEFAULT_TIMEZONE = "Asia/Bahrain"


def currency_meta(code: str):
    """(prefix, decimals) for a code, falling back to the code itself at 2dp
    so an unknown but valid ISO code still renders sensibly."""
    code = (code or DEFAULT_CURRENCY).upper()
    return CURRENCIES.get(code, (code, 2))


def format_money(amount, code: str) -> str:
    """Price for display. Zero or less is 'Free' — every event is free today,
    and that reads better than 'PHP 0.00'."""
    value = float(amount or 0)
    if value <= 0:
        return "Free"
    prefix, decimals = currency_meta(code)
    return f"{prefix} {value:,.{decimals}f}"


def app_timezone(name: str):
    """Resolve the configured zone, falling back to Bahrain if the name is
    missing or the system lacks the tz database for it, so a misconfigured
    setting degrades to the original behaviour rather than crashing."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def today_in(name: str) -> str:
    """The local calendar day in the configured zone, as YYYY-MM-DD.

    A race day is a calendar date, not an instant. Deciding "is this event
    upcoming?" from UTC makes an event disappear a day early in the small hours
    of local time; deciding it from the event's own zone does not.
    """
    return datetime.now(app_timezone(name)).date().isoformat()
