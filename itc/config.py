"""Configuration — secrets come from the environment, never from code."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _secret_key() -> str:
    key = os.environ.get("ITC_SECRET_KEY")
    if key:
        return key
    if os.environ.get("ITC_ENV", "development") == "production":
        raise RuntimeError(
            "ITC_SECRET_KEY must be set in production. Generate one with:\n"
            "  python3 -c \"import secrets; print(secrets.token_hex(32))\"")
    # Development only — random per process, so sessions reset on restart.
    import secrets
    return secrets.token_hex(32)


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = os.environ.get("ITC_DATABASE", os.path.join(BASE_DIR, "itc.db"))
    ENV_NAME = os.environ.get("ITC_ENV", "development")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Secure cookies once there is TLS in front. Separate from ITC_ENV so a
    # TLS-fronted review deploy can harden cookies without other side effects.
    SESSION_COOKIE_SECURE = (
        os.environ.get("ITC_ENV") == "production"
        or os.environ.get("ITC_SECURE_COOKIES", "").lower() in ("1", "true", "yes"))
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("ITC_SESSION_SECONDS", 12 * 3600))

    # Entry forms carry no uploads yet; this is a sanity bound on form posts.
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-secret"
    DATABASE = ":memory:"
    SESSION_COOKIE_SECURE = False
