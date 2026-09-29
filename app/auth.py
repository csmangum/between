"""The two people, their passwords, and the session fields that prove a login is still good."""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, MutableMapping

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
SAMPLE_PASSWORDS = {
    "",
    "change-me",
    "change-me-to-a-long-random-string",
    "dev-only-change-me",
    "test-secret",
}
SESSION_USER = "user"
SESSION_FINGERPRINT = "fp"
SESSION_ISSUED = "iat"
SESSION_SEEN = "seen"


@dataclass(frozen=True)
class Person:
    username: str
    display: str
    password_hash: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt$N$r$p$salt$hash — stdlib only, no extra dependency."""
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def check_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=SCRYPT_LEN,
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, _unb64(expected))


def _deterministic_salt(username: str) -> bytes:
    """Plaintext env passwords are hashed in memory; a stable salt keeps session fingerprints stable."""
    from . import config

    return hmac.new(config.SECRET_KEY.encode("utf-8"), f"salt:{username}".encode("utf-8"), hashlib.sha256).digest()[:16]


def _person(prefix: str) -> Person:
    from . import config

    username = os.getenv(f"{prefix}_NAME", prefix.lower()).strip().lower()
    display = os.getenv(f"{prefix}_DISPLAY", username.title()).strip()
    encoded = os.getenv(f"{prefix}_PASSWORD_HASH", "").strip()
    raw = os.getenv(f"{prefix}_PASSWORD", "").strip()
    if encoded:
        if not encoded.startswith("scrypt$"):
            config.refuse(f"{prefix}_PASSWORD_HASH is not a value produced by `python -m app.auth`.")
    elif raw in config.SAMPLE_SECRETS:
        config.refuse(
            f"{prefix}_PASSWORD_HASH (preferred) or {prefix}_PASSWORD must be set to a real value. "
            "Generate a hash with `python -m app.auth`."
        )
    else:
        encoded = hash_password(raw, salt=_deterministic_salt(username))
    return Person(username=username, display=display, password_hash=encoded)


PEOPLE: dict[str, Person] = {}


def load_people() -> dict[str, Person]:
    global PEOPLE
    if not PEOPLE:
        loaded: dict[str, Person] = {}
        for prefix in ("USER1", "USER2"):
            p = _person(prefix)
            if p.username in loaded:
                config.refuse("USER1_NAME and USER2_NAME must be different people.")
            loaded[p.username] = p
        PEOPLE = loaded
    return PEOPLE


def verify(username: str, password: str) -> Person | None:
    people = load_people()
    person = people.get(username.strip().lower())
    if not person:
        # Burn the same work as a real check so unknown names are not faster.
        check_password(password, _DUMMY_HASH)
        return None
    return person if check_password(password, person.password_hash) else None


def display_for(username: str) -> str:
    people = load_people()
    person = people.get(username)
    return person.display if person else username


def fingerprint(person: Person) -> str:
    """Changes when the password changes, so old sessions stop working. Keyed so the cookie
    (signed, not encrypted) does not carry an offline-crackable digest of the password."""
    from . import config

    mac = hmac.new(config.SECRET_KEY.encode("utf-8"), person.password_hash.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:24]


def start_session(session: MutableMapping[str, Any], person: Person) -> None:
    now = int(time.time())
    session.clear()
    session[SESSION_USER] = person.username
    session[SESSION_FINGERPRINT] = fingerprint(person)
    session[SESSION_ISSUED] = now
    session[SESSION_SEEN] = now


def session_user(session: MutableMapping[str, Any]) -> str | None:
    """Return the logged-in username if the session is still valid, else clear it."""
    from . import config

    username = session.get(SESSION_USER)
    if not username:
        return None
    person = load_people().get(username)
    issued = session.get(SESSION_ISSUED)
    now = int(time.time())
    valid = (
        person is not None
        and isinstance(issued, int)
        and now - issued <= config.SESSION_ABSOLUTE_SECONDS
        and hmac.compare_digest(str(session.get(SESSION_FINGERPRINT, "")), fingerprint(person))
    )
    if not valid:
        session.clear()
        return None
    seen = session.get(SESSION_SEEN)
    if not isinstance(seen, int) or now - seen >= config.SESSION_TOUCH_SECONDS:
        session[SESSION_SEEN] = now  # re-signs the cookie: sliding idle expiry
    return username


_DUMMY_HASH = hash_password("not-a-real-password", salt=b"\x00" * 16)


def _cli() -> None:
    """python -m app.auth  →  prints a USERn_PASSWORD_HASH value for .env"""
    if sys.stdin.isatty():
        first = getpass.getpass("Password: ")
        second = getpass.getpass("Again: ")
        if first != second:
            raise SystemExit("Passwords did not match.")
    else:
        first = sys.stdin.readline().rstrip("\n")
    if first in SAMPLE_PASSWORDS or len(first) < 8:
        raise SystemExit("Choose a password of at least 8 characters that is not a sample value.")
    print(hash_password(first))


if __name__ == "__main__":
    _cli()
