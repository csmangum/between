"""PBKDF2-SHA256 password hashing with only the standard library.

Hash format: ``pbkdf2_sha256$<iterations>$<salt hex>$<digest hex>``.

Run ``python -m app.passwords`` to hash a password for ``USERn_PASSWORD_HASH``.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import secrets
import sys

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
_DIGEST_LEN = hashlib.sha256().digest_size


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def is_hash(value: str) -> bool:
    return value.startswith(f"{ALGORITHM}$") and value.count("$") == 3


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if iterations < 1 or len(expected) != _DIGEST_LEN:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def compare_plaintext(expected: str, given: str) -> bool:
    """Constant-time comparison that does not leak the length of the secret."""
    a = hashlib.sha256(expected.encode("utf-8")).digest()
    b = hashlib.sha256(given.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def main() -> int:
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password to hash: ")
        again = getpass.getpass("Again: ")
        if password != again:
            print("The two entries did not match.", file=sys.stderr)
            return 1
    if not password:
        print("Refusing to hash an empty password.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
