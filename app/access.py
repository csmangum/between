from __future__ import annotations

from . import auth
from .models import Comment, Topic, Writing


def other_username(user: str) -> str | None:
    people = auth.load_people()
    for name in people:
        if name != user:
            return name
    return None


def topic_visible(user: str, topic: Topic) -> bool:
    if topic.created_by == user:
        return True
    return topic.share_status in {"offered", "shared"}


def topic_open(user: str, topic: Topic) -> bool:
    """True when this person may read the topic's contents."""
    if topic.created_by == user:
        return True
    return topic.share_status == "shared"


def writing_visible(user: str, writing: Writing) -> bool:
    if writing.author == user:
        return True
    return writing.share_status in {"offered", "shared"}


def writing_open(user: str, writing: Writing) -> bool:
    if writing.author == user:
        return True
    return writing.share_status == "shared"


def comment_visible(user: str, comment: Comment) -> bool:
    if comment.author == user:
        return True
    return comment.share_status in {"offered", "shared"}


def comment_open(user: str, comment: Comment) -> bool:
    if comment.author == user:
        return True
    return comment.share_status == "shared"
