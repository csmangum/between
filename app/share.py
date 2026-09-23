from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, assert_never

from .models import utcnow

ShareStatus = Literal["private", "offered", "shared"]


class Shareable(Protocol):
    share_status: str
    offered_at: datetime | None
    accepted_at: datetime | None


def normalize(status: str) -> ShareStatus:
    if status == "private":
        return "private"
    if status == "offered":
        return "offered"
    if status == "shared":
        return "shared"
    raise ValueError(f"unknown share status: {status}")


def label(status: str) -> str:
    """UI copy matched to the desk / sealed offer / shared table metaphor."""
    kind = normalize(status)
    if kind == "private":
        return "on your desk"
    if kind == "offered":
        return "sealed"
    if kind == "shared":
        return "kept between you"
    assert_never(kind)


def set_status(obj: Shareable, status: ShareStatus) -> None:
    obj.share_status = status
    if status == "offered":
        obj.offered_at = utcnow()
        obj.accepted_at = None
    elif status == "shared":
        obj.accepted_at = utcnow()
    elif status == "private":
        obj.offered_at = None
        obj.accepted_at = None
    else:
        assert_never(status)
