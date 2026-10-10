"""The organiser Settings screen — how the app leaves Bahrain without a
database edit.

The screen writes four settings; the ones that matter are currency and
timezone, because a wrong value there silently mislabels every price or shifts
every race day. So the tests are mostly about what it refuses to save.
"""
from itc.db import connect
from test_admin import _admin, _post


def _setting(app, key, default=""):
    """Read a setting straight from the test database, outside any request."""
    conn = connect(app.config["DATABASE"])
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def test_settings_needs_an_organiser(client):
    r = client.get("/admin/settings")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_an_athlete_cannot_open_settings(client):
    from test_entry import _signin
    _signin(client)
    assert client.get("/admin/settings").status_code == 403


def test_settings_page_shows_current_values(app):
    client = _admin(app)
    html = client.get("/admin/settings").get_data(as_text=True)
    assert "Settings" in html
    # Default currency selected.
    assert "BHD" in html
    # The live example price is rendered through the money filter in the
    # current (default BHD) currency.
    assert "BHD 1,500.000" in html


def test_saving_switches_region_to_philippines(app):
    client = _admin(app)
    r = _post(client, "/admin/settings",
              club_name="Mindanao Tri Series",
              currency="PHP",
              app_timezone="Asia/Manila",
              age_rule="dec31")
    assert r.status_code == 200
    assert _setting(app, "currency") == "PHP"
    assert _setting(app, "app_timezone") == "Asia/Manila"
    assert _setting(app, "club_name") == "Mindanao Tri Series"


def test_price_prints_in_new_currency_after_save(app):
    client = _admin(app)
    _post(client, "/admin/settings", club_name="Series", currency="PHP",
          app_timezone="Asia/Manila", age_rule="dec31")
    # The money filter is driven by the setting, so a rendered price follows.
    from itc.locale import format_money
    assert format_money(4500, _setting(app, "currency")) == "₱ 4,500.00"


def test_unknown_currency_is_rejected(app):
    client = _admin(app)
    r = _post(client, "/admin/settings", club_name="Series", currency="XYZ",
              app_timezone="Asia/Manila", age_rule="dec31")
    assert r.status_code == 400
    # Nothing was written.
    assert _setting(app, "currency") == "BHD"


def test_unknown_timezone_is_rejected(app):
    client = _admin(app)
    r = _post(client, "/admin/settings", club_name="Series", currency="PHP",
              app_timezone="Mars/Olympus", age_rule="dec31")
    assert r.status_code == 400
    assert _setting(app, "app_timezone") == "Asia/Bahrain"


def test_empty_club_name_is_rejected(app):
    client = _admin(app)
    r = _post(client, "/admin/settings", club_name="  ", currency="PHP",
              app_timezone="Asia/Manila", age_rule="dec31")
    assert r.status_code == 400
