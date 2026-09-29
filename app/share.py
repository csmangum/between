from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, assert_never

from .models import Comment, Topic, Writing, utcnow

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


def clear_revision(writing: Writing) -> None:
    writing.revision_title = None
    writing.revision_body = None
    writing.revision_status = None
    writing.revision_offered_at = None


def save_revision(writing: Writing, title: str, body: str) -> None:
    writing.revision_title = title
    writing.revision_body = body
    writing.revision_status = "private"
    writing.revision_offered_at = None


def offer_revision(writing: Writing) -> None:
    writing.revision_status = "offered"
    writing.revision_offered_at = utcnow()


def return_revision(writing: Writing) -> None:
    """Decline or pull-back: the draft returns to the author's desk."""
    writing.revision_status = "private"
    writing.revision_offered_at = None


def accept_revision(writing: Writing) -> None:
    writing.title = writing.revision_title or ""
    writing.body = writing.revision_body or ""
    writing.updated_at = utcnow()
    clear_revision(writing)


def fold_revision_into_private(writing: Writing) -> None:
    """When the whole writing returns to the desk, keep the author's latest words."""
    if writing.revision_body:
        writing.title = writing.revision_title or ""
        writing.body = writing.revision_body
    clear_revision(writing)


def fold_topic(topic: Topic) -> tuple[list[Writing], list[Comment]]:
    """The topic left the table: every writing and note inside returns to its own author's desk.
    Returns what changed so the caller can rewrite mirrors."""
    writings = [w for w in topic.writings if w.share_status != "private" or w.revision_status]
    comments = [c for c in topic.comments if c.share_status != "private"]
    for w in writings:
        fold_revision_into_private(w)
        set_status(w, "private")
    for c in comments:
        set_status(c, "private")
    return writings, comments


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
