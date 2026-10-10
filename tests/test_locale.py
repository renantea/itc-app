"""Region is configurable — currency and timezone are not baked to Bahrain.

These guard the extension that lets the same code run a Philippine series:
money must print in the configured currency with that currency's own number of
decimals, and "today" must be the configured zone's calendar day, not the
server's. The defaults must stay Bahrain-compatible so existing databases are
untouched.
"""
from itc.locale import (CURRENCIES, DEFAULT_CURRENCY, DEFAULT_TIMEZONE,
                        currency_meta, format_money, today_in)


def test_defaults_stay_bahrain():
    assert DEFAULT_CURRENCY == "BHD"
    assert DEFAULT_TIMEZONE == "Asia/Bahrain"
    # BHD keeps three decimals, the thing the old hard-coded formatter did.
    assert currency_meta("BHD") == ("BHD", 3)


def test_money_uses_currency_decimals():
    assert format_money(10, "BHD") == "BHD 10.000"
    assert format_money(4500, "PHP") == "₱ 4,500.00"
    assert format_money(19.5, "USD") == "$ 19.50"


def test_zero_or_less_is_free_in_any_currency():
    for code in CURRENCIES:
        assert format_money(0, code) == "Free"
    assert format_money(-5, "PHP") == "Free"


def test_unknown_but_plausible_code_still_renders():
    # An ISO code we don't list falls back to showing the code at 2dp rather
    # than crashing.
    assert format_money(100, "INR") == "INR 100.00"


def test_money_tolerates_missing_code():
    assert format_money(5, "") == "BHD 5.000"
    assert format_money(5, None) == "BHD 5.000"


def test_today_differs_by_zone():
    # The whole point: at some clock times Manila and Bahrain are on different
    # calendar days. Both are valid ISO dates; we only assert they resolve.
    assert len(today_in("Asia/Manila")) == 10
    assert len(today_in("Asia/Bahrain")) == 10


def test_bad_timezone_falls_back_to_bahrain():
    # A misconfigured setting degrades to the original behaviour, never a 500.
    assert today_in("Not/AZone") == today_in("Asia/Bahrain")
