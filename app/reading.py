"""The reading view: one topic laid out to be read, not worked on.

Everything a person may open in the topic, in the order it was written, with nothing to press. Pages carry
their notes beneath them; loose notes fall between pages by time; the margin, when the topic is shared, closes
the piece. Access is the archive's: openness, decided through `access` on each object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal

from sqlalchemy.orm import Session

from . import access
from .markdown_render import render_markdown
from .models import ChatMessage, Comment, Topic, Withdrawal, Writing
from .queries import open_topic_summaries, topic_withdrawals

WORDS_PER_MINUTE = 220
WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def word_count(text: str) -> int:
    class VisibleText(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.text: list[str] = []

        def handle_data(self, data: str) -> None:
            self.text.append(data)

    visible = VisibleText()
    visible.feed(render_markdown(text or ""))
    return len(WORD.findall("".join(visible.text)))


def minutes_for(words: int) -> int:
    """Whole minutes at a reading pace; anything at all is at least a minute."""
    return max(1, round(words / WORDS_PER_MINUTE)) if words else 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Page:
    writing: Writing
    notes: list[Comment]

    kind: Literal["page"] = "page"

    @property
    def at(self) -> datetime:
        return self.writing.created_at

    @property
    def title(self) -> str:
        return self.writing.title or "Untitled writing"

    @property
    def words(self) -> int:
        return word_count(self.writing.body) + sum(word_count(n.body) for n in self.notes)


@dataclass(frozen=True)
class Aside:
    """A note on the topic itself, not on any page."""

    note: Comment

    kind: Literal["aside"] = "aside"

    @property
    def at(self) -> datetime:
        return self.note.created_at

    @property
    def words(self) -> int:
        return word_count(self.note.body)


Piece = Page | Aside


@dataclass(frozen=True)
class Neighbour:
    id: int
    title: str


@dataclass(frozen=True)
class Reading:
    topic: Topic
    prompt: str
    pieces: list[Piece]
    pages: list[Page]
    words: int
    minutes: int
    sealed: int
    margin: list[ChatMessage]
    returned: list[Withdrawal]
    previous: Neighbour | None
    following: Neighbour | None


def _neighbours(topic: Topic, topics: list[tuple[int, str]]) -> tuple[Neighbour | None, Neighbour | None]:
    ids = [topic_id for topic_id, _title in topics]
    if topic.id not in ids:
        return None, None
    i = ids.index(topic.id)
    before = topics[i - 1] if i > 0 else None
    after = topics[i + 1] if i + 1 < len(topics) else None
    return (
        Neighbour(*before) if before else None,
        Neighbour(*after) if after else None,
    )


def build_reading(db: Session, user: str, topic: Topic) -> Reading:
    """Assumes `access.topic_open(user, topic)`; the route decides that before asking."""
    open_notes = [c for c in topic.comments if access.comment_open(user, c)]
    pages = [
        Page(writing=w, notes=[c for c in open_notes if c.writing_id == w.id])
        for w in topic.writings
        if access.writing_open(user, w)
    ]
    page_ids = {p.writing.id for p in pages}
    asides = [Aside(note=c) for c in open_notes if c.writing_id is None or c.writing_id not in page_ids]
    pieces: list[Piece] = sorted([*pages, *asides], key=lambda p: _aware(p.at))
    words = sum(p.words for p in pieces)
    sealed = sum(1 for w in topic.writings if access.writing_visible(user, w) and not access.writing_open(user, w))
    sealed += sum(1 for c in topic.comments if access.comment_visible(user, c) and not access.comment_open(user, c))
    previous, following = _neighbours(topic, open_topic_summaries(db, user))
    return Reading(
        topic=topic,
        prompt=topic.prompt if access.topic_prompt_open(user, topic) else "",
        pieces=pieces,
        pages=pages,
        words=words,
        minutes=minutes_for(words),
        sealed=sealed,
        margin=list(topic.messages) if topic.share_status == "shared" else [],
        returned=[s for s in topic_withdrawals(db, topic.id) if access.withdrawal_visible(user, s)],
        previous=previous,
        following=following,
    )
