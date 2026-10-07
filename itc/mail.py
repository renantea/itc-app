"""Transactional email — the one piece of this app that leaves the machine.

Deliberately **off by default**. `ITC_EMAIL_ENABLED` must be set *and* a Brevo
key present before anything is actually posted; otherwise the message is
logged in full and reported as "not sent". That ordering matters: a half-built
events app on a box that already holds a working mail key is one careless
import away from mailing real people, and a test run should never be the thing
that discovers it.

Brevo is reused because the other apps on this box already send through it.
The sender address still has to be one Brevo has verified — ITC's own domain
needs verifying there before `events@internationaltriathlonclub.com` will send.
"""
import logging
import os

logger = logging.getLogger(__name__)

BREVO_URL = os.environ.get("ITC_BREVO_URL", "https://api.brevo.com/v3/smtp/email")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()

#: Explicit opt-in. Anything other than 1/true/yes and nothing is posted.
ENABLED = os.environ.get("ITC_EMAIL_ENABLED", "").lower() in ("1", "true", "yes")

SENDER_EMAIL = os.environ.get("ITC_MAIL_SENDER", "job-agent@byteswell.com")
SENDER_NAME = os.environ.get("ITC_MAIL_SENDER_NAME", "ITC Events")
REPLY_TO = os.environ.get("ITC_MAIL_REPLY_TO", "").strip()


def send(to_email: str, to_name: str, subject: str, html: str):
    """Returns (sent, info). Never raises — mail must not fail an entry.

    An athlete who is on the start list but did not get an email has a minor
    problem. An athlete whose entry was rolled back because a mail server was
    slow has a real one.
    """
    if not to_email:
        return False, "no address"
    if not ENABLED:
        logger.info("EMAIL (disabled, not sent) to=%s subject=%s", to_email, subject)
        return False, "email disabled (set ITC_EMAIL_ENABLED=1)"
    if not BREVO_API_KEY:
        logger.warning("EMAIL (no key, not sent) to=%s subject=%s", to_email, subject)
        return False, "BREVO_API_KEY not configured"

    # Imported here, not at module scope: mail is optional and off by default,
    # so a missing HTTP library should degrade to "not sent" rather than stop
    # the service from booting at all.
    try:
        import requests
    except ImportError:
        logger.warning("EMAIL (requests not installed, not sent) to=%s", to_email)
        return False, "requests is not installed"

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    if REPLY_TO:
        payload["replyTo"] = {"email": REPLY_TO}
    try:
        r = requests.post(BREVO_URL, json=payload, timeout=20, headers={
            "api-key": BREVO_API_KEY, "content-type": "application/json",
            "accept": "application/json"})
    except requests.RequestException as exc:
        logger.warning("Brevo send failed: %s", exc)
        return False, f"network error: {exc}"
    if r.status_code in (200, 201, 202):
        try:
            return True, r.json().get("messageId", "sent")
        except ValueError:
            return True, "sent"
    logger.warning("Brevo HTTP %s: %s", r.status_code, r.text[:200])
    return False, f"Brevo HTTP {r.status_code}"
