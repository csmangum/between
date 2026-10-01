"""Read-side queries shared by several routes. Access filtering still goes through `access`."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from . import access
from .db import SessionLocal
from .models import Topic, Withdrawal, Writing


def sealed_offer_count(user: str | None, db: Session | None = None) -> int:
    if not user:
        return 0
    own = db is not None
    session = db or SessionLocal()
    try:
        return session.query(Topic).filter(Topic.created_by != user, Topic.share_status == "offered").count()
    finally:
        if not own:
            session.close()


def writing_counts(db: Session, topics: list[Topic], viewer: str) -> dict[int, int]:
    """Writings this person may know about, per topic, in one query so the desk avoids N+1."""
    if not topics:
        return {}
    rows = (
        db.query(Writing.topic_id, func.count(Writing.id))
        .filter(
            Writing.topic_id.in_([t.id for t in topics]),
            or_(Writing.author == viewer, Writing.share_status.in_(("offered", "shared"))),
        )
        .group_by(Writing.topic_id)
        .all()
    )
    return {topic_id: count for topic_id, count in rows}


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
    return [t for t in topics if access.topic_visible(user, t, contributor=t.id in mine)]


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


def open_topic_summaries(db: Session, user: str) -> list[tuple[int, str]]:
    """Topic IDs and titles this person may open, without loading their contents."""
    mine = access.contributed_topic_ids(db, user)
    rows = (
        db.query(Topic.id, Topic.title)
        .filter(
            (Topic.created_by == user) | (Topic.share_status == "shared") | Topic.id.in_(mine)
        )
        .order_by(Topic.created_at.asc())
        .all()
    )
    return [(topic_id, title) for topic_id, title in rows]


def topic_withdrawals(db: Session, topic_id: int) -> list[Withdrawal]:
    """Every stub this topic has produced, oldest first. The caller filters by who may see each."""
    return db.query(Withdrawal).filter(Withdrawal.topic_id == topic_id).order_by(Withdrawal.withdrawn_at.asc()).all()


def withdrawals_for(db: Session, user: str) -> list[Withdrawal]:
    """What returned to the other person's desk after this person had opened it."""
    stubs = db.query(Withdrawal).order_by(Withdrawal.withdrawn_at.asc()).all()
    return [s for s in stubs if access.withdrawal_visible(user, s)]
