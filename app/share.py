from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, assert_never
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Comment, Topic, Withdrawal, Writing, utcnow

ShareStatus = Literal["private", "offered", "shared"]
Kind = Literal["topic", "writing", "comment"]


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


def withdrawal(kind: Kind, obj: Topic | Writing | Comment, actor: str) -> Withdrawal | None:
    """The stub the other person keeps when something open between you returns to a desk.
    Nothing is kept for a sealed offer pulled back or declined: an unopened letter was never part of the record.
    Call it before the status changes."""
    if obj.share_status != "shared":
        return None
    if isinstance(obj, Topic):
        topic, author, title = obj, obj.created_by, obj.title
    else:
        topic, author, title = obj.topic, obj.author, getattr(obj, "title", "")
    source_id = obj.source_id or str(uuid4())
    if not obj.source_id:
        obj.source_id = source_id
    return Withdrawal(
        kind=kind,
        source_id=source_id,
        object_id=obj.id,
        topic_id=topic.id,
        topic_title=topic.title,
        title=title,
        author=author,
        actor=actor,
        opened_at=obj.accepted_at,
        withdrawn_at=utcnow(),
    )


def clear_withdrawals(db: Session, kind: Kind, source_id: str) -> None:
    """Opened again: the live object now tells the truth, so the stub has nothing left to say."""
    db.execute(delete(Withdrawal).where(Withdrawal.kind == kind, Withdrawal.source_id == source_id))


def fold_topic(topic: Topic, actor: str) -> tuple[list[Writing], list[Comment], list[Withdrawal]]:
    """The topic left the table: every writing and note inside returns to its own author's desk.
    Returns what changed so the caller can rewrite mirrors, and the stubs owed to whoever lost access."""
    writings = [w for w in topic.writings if w.share_status != "private" or w.revision_status]
    comments = [c for c in topic.comments if c.share_status != "private"]
    stubs = [withdrawal("topic", topic, actor)]
    stubs += [withdrawal("writing", w, actor) for w in writings]
    stubs += [withdrawal("comment", c, actor) for c in comments]
    for w in writings:
        fold_revision_into_private(w)
        set_status(w, "private")
    for c in comments:
        set_status(c, "private")
    return writings, comments, [s for s in stubs if s is not None]


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
