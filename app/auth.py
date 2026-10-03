"""The two people, their passwords, and the session fields that prove a login is still good.

An optional admin login can act as either person and switch between them. It is not a third
person in the room, and it exists only when ADMIN_PASSWORD_HASH or ADMIN_PASSWORD is set.
"""

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
SESSION_ADMIN = "adm"
ADMIN_USERNAME = "admin"


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


def _encoded_password(prefix: str, username: str, *, required: bool) -> str | None:
    """Hash from `{prefix}_PASSWORD_HASH`, or from a plaintext `{prefix}_PASSWORD`.

    Empty means unset. A required account refuses to start; the admin login does not exist.
    """
    from . import config

    encoded = os.getenv(f"{prefix}_PASSWORD_HASH", "").strip()
    raw = os.getenv(f"{prefix}_PASSWORD", "").strip()
    if encoded:
        if not encoded.startswith("scrypt$"):
            config.refuse(f"{prefix}_PASSWORD_HASH is not a value produced by `python -m app.auth`.")
        return encoded
    if not raw:
        if required:
            config.refuse(
                f"{prefix}_PASSWORD_HASH (preferred) or {prefix}_PASSWORD must be set to a real value. "
                "Generate a hash with `python -m app.auth`."
            )
        return None
    if raw in config.SAMPLE_SECRETS:
        config.refuse(
            f"{prefix}_PASSWORD_HASH (preferred) or {prefix}_PASSWORD must be set to a real value. "
            "Generate a hash with `python -m app.auth`."
        )
    return hash_password(raw, salt=_deterministic_salt(username))


def _person(prefix: str) -> Person:
    username = os.getenv(f"{prefix}_NAME", prefix.lower()).strip().lower()
    display = os.getenv(f"{prefix}_DISPLAY", username.title()).strip()
    encoded = _encoded_password(prefix, username, required=True)
    if encoded is None:
        raise AssertionError(f"{prefix} password was required")
    return Person(username=username, display=display, password_hash=encoded)


PEOPLE: dict[str, Person] = {}
_PRIMARY_USER = ""
_ADMIN: Person | None = None
_ADMIN_LOADED = False


def admin_person() -> Person | None:
    """The operator login, or None when no admin secret is configured.

    Plaintext secrets use the same secret-derived salt as a person's env password, so the
    session fingerprint stays stable until that secret changes.
    """
    global _ADMIN, _ADMIN_LOADED
    if not _ADMIN_LOADED:
        encoded = _encoded_password("ADMIN", ADMIN_USERNAME, required=False)
        _ADMIN = Person(username=ADMIN_USERNAME, display="Admin", password_hash=encoded) if encoded else None
        _ADMIN_LOADED = True
    return _ADMIN


def load_people() -> dict[str, Person]:
    global PEOPLE, _PRIMARY_USER
    if not PEOPLE:
        from . import config

        loaded: dict[str, Person] = {}
        primary = ""
        for prefix in ("USER1", "USER2"):
            p = _person(prefix)
            if p.username == ADMIN_USERNAME:
                config.refuse(f"{prefix}_NAME cannot be {ADMIN_USERNAME}.")
            if p.username in loaded:
                config.refuse("USER1_NAME and USER2_NAME must be different people.")
            if prefix == "USER1":
                primary = p.username
            loaded[p.username] = p
        _PRIMARY_USER = primary
        PEOPLE = loaded
        admin_person()
    return PEOPLE


def primary_username() -> str:
    """The person an admin session acts as until they switch. This is USER1."""
    load_people()
    return _PRIMARY_USER


def verify(username: str, password: str) -> Person | None:
    people = load_people()
    name = username.strip().lower()
    if name == ADMIN_USERNAME:
        person = admin_person()
        if person is None:
            check_password(password, _DUMMY_HASH)
            return None
        return person if check_password(password, person.password_hash) else None
    person = people.get(name)
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


def _digest_ok(got: object, expected: str) -> bool:
    return isinstance(got, str) and len(got) == len(expected) and hmac.compare_digest(got, expected)


def is_admin_session(session: MutableMapping[str, Any]) -> bool:
    admin = admin_person()
    if admin is None:
        return False
    return _digest_ok(session.get(SESSION_ADMIN), fingerprint(admin))


def start_session(session: MutableMapping[str, Any], person: Person) -> None:
    now = int(time.time())
    session.clear()
    if person.username == ADMIN_USERNAME:
        session[SESSION_USER] = primary_username()
        admin_fp = fingerprint(person)
        session[SESSION_FINGERPRINT] = admin_fp
        session[SESSION_ADMIN] = admin_fp
    else:
        session[SESSION_USER] = person.username
        session[SESSION_FINGERPRINT] = fingerprint(person)
    session[SESSION_ISSUED] = now
    session[SESSION_SEEN] = now


def switch_profile(session: MutableMapping[str, Any], username: str) -> bool:
    """Move an admin session onto one of the two people. Anyone else stays where they are."""
    if not is_admin_session(session):
        return False
    name = username.strip().lower()
    if name not in load_people():
        return False
    session[SESSION_USER] = name
    return True


def session_user(session: MutableMapping[str, Any]) -> str | None:
    """Return the logged-in username if the session is still valid, else clear it.

    An admin session returns the person they are acting as, not the admin name.
    """
    from . import config

    username = session.get(SESSION_USER)
    if not username:
        return None
    people = load_people()
    issued = session.get(SESSION_ISSUED)
    now = int(time.time())
    admin = admin_person()
    if admin is not None and is_admin_session(session):
        identity_ok = username in people and _digest_ok(session.get(SESSION_FINGERPRINT), fingerprint(admin))
    else:
        person = people.get(username)
        identity_ok = person is not None and _digest_ok(session.get(SESSION_FINGERPRINT), fingerprint(person))
    valid = identity_ok and isinstance(issued, int) and now - issued <= config.SESSION_ABSOLUTE_SECONDS
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
