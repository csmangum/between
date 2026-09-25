from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import passwords
from .config import ConfigError

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MIN_PASSWORD_CHARS = 12
PLACEHOLDER_PASSWORDS = {"", "change-me", "password", "changeme"}


@dataclass(frozen=True)
class Person:
    username: str
    display: str
    secret: str
    hashed: bool

    def check(self, password: str) -> bool:
        if self.hashed:
            return passwords.verify_password(password, self.secret)
        return passwords.compare_plaintext(self.secret, password)


def _person(prefix: str) -> Person:
    username = os.getenv(f"{prefix}_NAME", prefix.lower()).strip().lower()
    if not USERNAME_RE.match(username):
        raise ConfigError(
            f"{prefix}_NAME must be 1-40 characters of lowercase letters, digits, '-' or '_' "
            f"(it names a folder under data/local/); got {username!r}."
        )
    display = os.getenv(f"{prefix}_DISPLAY", "").strip() or username.title()

    hashed = os.getenv(f"{prefix}_PASSWORD_HASH", "").strip()
    plain = os.getenv(f"{prefix}_PASSWORD", "")
    if hashed:
        if not passwords.is_hash(hashed):
            raise ConfigError(
                f"{prefix}_PASSWORD_HASH is not in the expected format. "
                "Generate one with `python -m app.passwords`."
            )
        return Person(username=username, display=display, secret=hashed, hashed=True)
    if plain.strip().lower() in PLACEHOLDER_PASSWORDS:
        raise ConfigError(
            f"{prefix}_PASSWORD is missing or still a placeholder. Set a real password "
            f"(or better, {prefix}_PASSWORD_HASH from `python -m app.passwords`)."
        )
    if len(plain) < MIN_PASSWORD_CHARS:
        raise ConfigError(f"{prefix}_PASSWORD must be at least {MIN_PASSWORD_CHARS} characters.")
    return Person(username=username, display=display, secret=plain, hashed=False)


PEOPLE: dict[str, Person] = {}


def load_people() -> dict[str, Person]:
    global PEOPLE
    if not PEOPLE:
        loaded: dict[str, Person] = {}
        for prefix in ("USER1", "USER2"):
            p = _person(prefix)
            if p.username in loaded:
                raise ConfigError("USER1_NAME and USER2_NAME must be different people.")
            loaded[p.username] = p
        PEOPLE = loaded
    return PEOPLE


def verify(username: str, password: str) -> Person | None:
    people = load_people()
    person = people.get(username.strip().lower())
    if person is None:
        return None
    return person if person.check(password) else None


def display_for(username: str) -> str:
    people = load_people()
    person = people.get(username)
    return person.display if person else username
