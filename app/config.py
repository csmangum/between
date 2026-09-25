"""Environment-driven settings, validated once so a misconfigured host refuses to start."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

MIN_SECRET_KEY_CHARS = 32
PLACEHOLDER_SECRETS = {"", "dev-only-change-me", "change-me", "change-me-to-a-long-random-string"}


class ConfigError(RuntimeError):
    """Raised when the environment is missing something the app must not run without."""


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}.") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return value


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if key in PLACEHOLDER_SECRETS:
        raise ConfigError(
            "SECRET_KEY is not set (or is still the placeholder). It signs the session cookie; "
            "anyone who knows it can log in as either person. Generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` and put it in .env."
        )
    if len(key) < MIN_SECRET_KEY_CHARS:
        raise ConfigError(f"SECRET_KEY must be at least {MIN_SECRET_KEY_CHARS} characters long.")
    return key


def _allowed_hosts() -> list[str]:
    raw = os.getenv("ALLOWED_HOSTS", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str
    secret_key: str
    https_only: bool
    session_max_age: int
    allowed_hosts: list[str]
    max_request_bytes: int
    login_ip_limit: int
    login_user_limit: int
    login_window_seconds: int


def load_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Between").strip() or "Between",
        secret_key=_secret_key(),
        https_only=_flag("HTTPS_ONLY"),
        session_max_age=_int("SESSION_DAYS", default=7, minimum=1) * 24 * 60 * 60,
        allowed_hosts=_allowed_hosts(),
        max_request_bytes=_int("MAX_REQUEST_KB", default=2048, minimum=64) * 1024,
        login_ip_limit=_int("LOGIN_ATTEMPTS_PER_IP", default=5, minimum=1),
        login_user_limit=_int("LOGIN_ATTEMPTS_PER_NAME", default=20, minimum=1),
        login_window_seconds=_int("LOGIN_WINDOW_MINUTES", default=15, minimum=1) * 60,
    )
