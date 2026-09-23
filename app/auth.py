from __future__ import annotations

import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Person:
    username: str
    display: str
    password: str


def _person(prefix: str) -> Person:
    username = os.getenv(f"{prefix}_NAME", prefix.lower()).strip().lower()
    display = os.getenv(f"{prefix}_DISPLAY", username.title()).strip()
    raw = os.getenv(f"{prefix}_PASSWORD", "change-me")
    return Person(username=username, display=display, password=raw)


PEOPLE: dict[str, Person] = {}


def load_people() -> dict[str, Person]:
    global PEOPLE
    if not PEOPLE:
        for prefix in ("USER1", "USER2"):
            p = _person(prefix)
            PEOPLE[p.username] = p
    return PEOPLE


def verify(username: str, password: str) -> Person | None:
    people = load_people()
    person = people.get(username.strip().lower())
    if not person:
        return None
    expected = person.password.encode("utf-8")
    given = password.encode("utf-8")
    if len(expected) != len(given):
        # keep compare constant-ish without raising
        hmac.compare_digest(expected, expected)
        return None
    if hmac.compare_digest(expected, given):
        return person
    return None


def display_for(username: str) -> str:
    people = load_people()
    person = people.get(username)
    return person.display if person else username
