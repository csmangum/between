"""Read-side queries shared by several routes. Access filtering still goes through `access`."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from . import access
from .db import SessionLocal
from .models import ChatMessage, Topic, Writing


def sealed_offer_count(user: str | None, db: Session | None = None) -> int:
    if not user:
        return 0
    own = db is not None
    session = db or SessionLocal()
    try:
        return (
            session.query(Topic)
            .filter(Topic.created_by != user, Topic.share_status == "offered")
            .count()
        )
    finally:
        if not own:
            session.close()


def attach_topic_counts(db: Session, topics: list[Topic], viewer: str) -> None:
    """Batch-load writing/message counts to avoid N+1 on the desk."""
    if not topics:
        return
    ids = [t.id for t in topics]
    writing_counts = dict(
        db.query(Writing.topic_id, func.count(Writing.id))
        .filter(
            Writing.topic_id.in_(ids),
            or_(Writing.author == viewer, Writing.share_status.in_(("offered", "shared"))),
        )
        .group_by(Writing.topic_id)
        .all()
    )
    message_counts = dict(
        db.query(ChatMessage.topic_id, func.count(ChatMessage.id))
        .filter(ChatMessage.topic_id.in_(ids))
        .group_by(ChatMessage.topic_id)
        .all()
    )
    for topic in topics:
        topic.writing_count = writing_counts.get(topic.id, 0)
        topic.message_count = message_counts.get(topic.id, 0)


def load_topic(db: Session, topic_id: int) -> Topic | None:
    return (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .filter(Topic.id == topic_id)
        .one_or_none()
    )


def visible_topics(db: Session, user: str) -> list[Topic]:
    topics = db.query(Topic).order_by(Topic.updated_at.desc()).all()
    mine = access.contributed_topic_ids(db, user)
    visible = [t for t in topics if access.topic_visible(user, t, contributor=t.id in mine)]
    attach_topic_counts(db, visible, user)
    return visible


def shared_topics(db: Session) -> list[Topic]:
    return (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .filter(Topic.share_status == "shared")
        .order_by(Topic.updated_at.desc())
        .all()
    )


def open_topics(db: Session, user: str) -> list[Topic]:
    topics = (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .order_by(Topic.created_at.asc())
        .all()
    )
    return [t for t in topics if access.topic_open(user, t)]
