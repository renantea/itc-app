"""ITC Events — race registration for International Triathlon Club.

Running, duathlon, triathlon and aquathlon. One account registers one or many
athletes; every athlete gets their own entry record, because that is what a
start line, a capacity limit and a timing company all count.

  ./run.sh                               development, http://127.0.0.1:9797
  gunicorn -b 127.0.0.1:9797 wsgi:app    production, behind a tunnel
"""
import os
from datetime import datetime, timezone

from flask import Flask, render_template, request

from itc.config import Config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Self-hosted fonts would be better, but the pages are plain and the club site
# already pulls from Google Fonts; keep the two looking alike.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def create_app(config_object=Config, overrides: dict = None) -> Flask:
    app = Flask(__name__,
                template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"))
    app.config.from_object(config_object)
    if overrides:
        app.config.update(overrides)

    from itc import db
    app.teardown_appcontext(db.close_db)
    if app.config["DATABASE"] != ":memory:":
        db.init_db(app.config["DATABASE"])

    from itc.pages import bp as pages_bp
    app.register_blueprint(pages_bp)

    _register_headers(app)
    _register_errors(app)
    _register_template_helpers(app)

    @app.get("/healthz")
    def healthz():
        db.scalar("SELECT 1")
        return {"ok": True, "env": app.config["ENV_NAME"]}

    return app


def _register_headers(app: Flask) -> None:
    @app.after_request
    def headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        # Pages are rendered per request and will carry entrant data. Caching
        # them serves one person's details to the next, and a cached document
        # also means a deploy can land and stay invisible.
        if "text/html" in response.headers.get("Content-Type", ""):
            response.headers.setdefault("Cache-Control", "no-store, must-revalidate")
        # Static assets are versioned by mtime (see asset_v), so they can be
        # cached hard without a stale-asset problem.
        elif request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response


def _register_errors(app: Flask) -> None:
    messages = {
        403: "You don't have access to this page",
        404: "That page doesn't exist",
        413: "That upload is too large",
        500: "Something went wrong at our end",
    }

    def handler(err):
        code = getattr(err, "code", 500) or 500
        if code == 500:
            app.logger.exception("Unhandled error")
        return render_template("error.html", code=code,
                               message=messages.get(code, "Error")), code

    for code in messages:
        app.register_error_handler(code, handler)


def _register_template_helpers(app: Flask) -> None:
    from itc.db import get_setting

    @app.template_filter("racedate")
    def racedate(value, fmt="%a %d %b %Y"):
        """A race day is a calendar date, printed as one."""
        if not value:
            return ""
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime(fmt)
        except ValueError:
            return str(value)

    @app.template_filter("money")
    def money(value):
        amount = float(value or 0)
        return "Free" if amount <= 0 else f"BHD {amount:,.3f}"

    @app.context_processor
    def inject():
        return {
            "club_name": get_setting("club_name", "International Triathlon Club"),
            "now_year": datetime.now(timezone.utc).year,
            "asset_v": _asset_version,
        }


def _asset_version(filename: str) -> int:
    """Cache-busting stamp from the file's own modification time.

    Hand-typed version numbers get forgotten, and a forgotten bump ships CSS
    that returning browsers never fetch — fine in a fresh browser, broken for
    everyone else, which is the worst way to find out.
    """
    from flask import current_app
    try:
        return int(os.path.getmtime(
            os.path.join(current_app.static_folder, filename)))
    except OSError:
        return 0
