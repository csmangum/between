from __future__ import annotations

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from . import auth
from .models import ChatMessage, Comment, Topic, Writing


def other_username(user: str) -> str | None:
    people = auth.load_people()
    for name in people:
        if name != user:
            return name
    return None


def contributed_topic_ids(db: Session, user: str) -> set[int]:
    """Topics where this person has written anything — a writing, a note, or a line in the margin."""
    stmt = union(
        select(Writing.topic_id).where(Writing.author == user),
        select(Comment.topic_id).where(Comment.author == user),
        select(ChatMessage.topic_id).where(ChatMessage.author == user),
    )
    return {row[0] for row in db.execute(stmt)}


def contributed(user: str, topic: Topic) -> bool:
    """Same question answered from the loaded object. Objects without relationships count as no."""
    for attr in ("writings", "comments", "messages"):
        items = getattr(topic, attr, None)
        if items and any(getattr(item, "author", None) == user for item in items):
            return True
    return False


def topic_visible(user: str, topic: Topic, contributor: bool | None = None) -> bool:
    if topic.created_by == user:
        return True
    if topic.share_status in {"offered", "shared"}:
        return True
    return contributed(user, topic) if contributor is None else contributor


def topic_open(user: str, topic: Topic, contributor: bool | None = None) -> bool:
    """True when this person may read the topic's contents (their own words are always readable)."""
    if topic.created_by == user:
        return True
    if topic.share_status == "shared":
        return True
    return contributed(user, topic) if contributor is None else contributor


def topic_awaits(user: str, topic: Topic) -> bool:
    """A sealed offer from the other person that this person has not opened."""
    return topic.created_by != user and topic.share_status == "offered"


def _parent_visible(user: str, obj: Writing | Comment) -> bool:
    topic = getattr(obj, "topic", None)
    return True if topic is None else topic_visible(user, topic)


def _parent_open(user: str, obj: Writing | Comment) -> bool:
    topic = getattr(obj, "topic", None)
    return True if topic is None else topic_open(user, topic)


def writing_visible(user: str, writing: Writing) -> bool:
    if writing.author == user:
        return True
    return _parent_visible(user, writing) and writing.share_status in {"offered", "shared"}


def writing_open(user: str, writing: Writing) -> bool:
    if writing.author == user:
        return True
    return _parent_open(user, writing) and writing.share_status == "shared"


def comment_visible(user: str, comment: Comment) -> bool:
    if comment.author == user:
        return True
    return _parent_visible(user, comment) and comment.share_status in {"offered", "shared"}


def comment_open(user: str, comment: Comment) -> bool:
    if comment.author == user:
        return True
    return _parent_open(user, comment) and comment.share_status == "shared"


def has_words_from_others(topic: Topic, user: str) -> bool:
    """Anything in this topic that someone other than `user` wrote."""
    for attr in ("writings", "comments", "messages"):
        if any(item.author != user for item in getattr(topic, attr, [])):
            return True
    return False
