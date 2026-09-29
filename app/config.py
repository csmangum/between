"""Process configuration. Refuses to start with sample secrets outside dev mode."""

from __future__ import annotations

import logging
import os
import secrets

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("between")

SAMPLE_SECRETS = {
    "",
    "change-me",
    "change-me-to-a-long-random-string",
    "dev-only-change-me",
    "test-secret",
}
MIN_SECRET_LENGTH = 32


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def refuse(message: str) -> None:
    raise SystemExit(f"Between refused to start: {message}")


DEV = _flag("BETWEEN_DEV")
APP_NAME = os.getenv("APP_NAME", "Between")
HTTPS_ONLY = _flag("HTTPS_ONLY")
SITE_ADDRESS = os.getenv("SITE_ADDRESS", "").strip()
MARKDOWN_MIRROR = os.getenv("MARKDOWN_MIRROR", "true").strip().lower() not in {"0", "false", "no"}

SESSION_IDLE_SECONDS = int(os.getenv("SESSION_IDLE_DAYS", "7")) * 86400
SESSION_ABSOLUTE_SECONDS = int(os.getenv("SESSION_MAX_DAYS", "30")) * 86400
SESSION_TOUCH_SECONDS = 12 * 3600


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if key not in SAMPLE_SECRETS and len(key) >= MIN_SECRET_LENGTH:
        return key
    if DEV:
        log.warning("SECRET_KEY is missing or a sample value; using an ephemeral key because BETWEEN_DEV=1")
        return secrets.token_urlsafe(48)
    refuse(
        f"SECRET_KEY must be a random string of at least {MIN_SECRET_LENGTH} characters "
        "(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')."
    )
    raise AssertionError("unreachable")


SECRET_KEY = _secret_key()
