from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, assert_never

from .models import ChatMessage, Comment, Topic, Writing

Kind = Literal["writing", "note", "margin"]

EXCERPT_LEN = 180
LATELY_LIMIT = 16


def excerpt(text: str, limit: int = EXCERPT_LEN) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{cut}…"


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _when(obj: Writing | Comment) -> datetime:
    return obj.accepted_at or obj.created_at


def _writing_href(topic_id: int, writing_id: int) -> str:
    return f"/topics/{topic_id}#writing-{writing_id}"


def _comment_href(topic_id: int, writing_id: int | None) -> str:
    if writing_id:
        return f"/topics/{topic_id}#writing-{writing_id}"
    return f"/topics/{topic_id}#comments"


@dataclass
class Moment:
    kind: Kind
    topic_id: int
    topic_title: str
    author: str
    at: datetime
    excerpt: str
    href: str
    heading: str = ""

    @property
    def kind_label(self) -> str:
        if self.kind == "writing":
            return "A writing"
        if self.kind == "note":
            return "A note"
        if self.kind == "margin":
            return "In the margin"
        assert_never(self.kind)


@dataclass
class Waiting:
    title: str
    href: str
    author: str
    at: datetime | None


@dataclass
class TableCard:
    id: int
    title: str
    prompt: str
    accepted_at: datetime | None
    latest_writing: Moment | None
    latest_margin: Moment | None
    waiting: list[Waiting] = field(default_factory=list)


@dataclass
class TableView:
    lately: list[Moment]
    cards: list[TableCard]


def _writing_moment(topic: Topic, writing: Writing) -> Moment:
    return Moment(
        kind="writing",
        topic_id=topic.id,
        topic_title=topic.title,
        author=writing.author,
        at=_when(writing),
        excerpt=excerpt(writing.body),
        href=_writing_href(topic.id, writing.id),
        heading=writing.title or "Untitled writing",
    )


def _note_moment(topic: Topic, comment: Comment) -> Moment:
    return Moment(
        kind="note",
        topic_id=topic.id,
        topic_title=topic.title,
        author=comment.author,
        at=_when(comment),
        excerpt=excerpt(comment.body),
        href=_comment_href(topic.id, comment.writing_id),
    )


def _margin_moment(topic: Topic, message: ChatMessage) -> Moment:
    return Moment(
        kind="margin",
        topic_id=topic.id,
        topic_title=topic.title,
        author=message.author,
        at=message.created_at,
        excerpt=excerpt(message.body),
        href=f"/topics/{topic.id}",
    )


def build_table(topics: list[Topic], user: str) -> TableView:
    lately: list[Moment] = []
    cards: list[TableCard] = []
    for topic in topics:
        if topic.share_status != "shared":
            continue
        writing_moments = [_writing_moment(topic, w) for w in topic.writings if w.share_status == "shared"]
        note_moments = [_note_moment(topic, c) for c in topic.comments if c.share_status == "shared"]
        margin_moments = [_margin_moment(topic, m) for m in topic.messages]
        lately.extend(writing_moments)
        lately.extend(note_moments)
        lately.extend(margin_moments)
        waiting = [
            Waiting(
                title=w.title or "A writing",
                href=_writing_href(topic.id, w.id),
                author=w.author,
                at=w.offered_at,
            )
            for w in topic.writings
            if w.share_status == "offered" and w.author != user
        ]
        cards.append(
            TableCard(
                id=topic.id,
                title=topic.title,
                prompt=topic.prompt,
                accepted_at=topic.accepted_at,
                latest_writing=max(writing_moments, key=lambda m: _aware(m.at), default=None),
                latest_margin=max(margin_moments, key=lambda m: _aware(m.at), default=None),
                waiting=waiting,
            )
        )
    lately.sort(key=lambda m: _aware(m.at), reverse=True)
    cards.sort(key=lambda c: _aware(c.accepted_at), reverse=True)
    return TableView(lately=lately[:LATELY_LIMIT], cards=cards)
