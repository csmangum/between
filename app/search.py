"""Full-text search over what a person may read.

The index is an FTS5 table kept in step by SQLite triggers on the four source tables, so no write path in the
app can forget it. Who may see a hit is decided here at query time, through `access`, on the loaded objects:
the index knows words, never permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, assert_never

from markupsafe import Markup, escape
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, selectinload

from . import access
from .models import ChatMessage, Comment, Topic, Writing

Kind = Literal["topic", "writing", "comment", "chat"]

CANDIDATES = 200
MAX_HITS = 60
MAX_TERMS = 8
MAX_QUERY_CHARS = 120
START, END = "\x01", "\x02"
TERM = re.compile(r"[\w'’-]+", re.UNICODE)
# Emphasis, code and heading marks, plus '>' only where it opens a blockquote line.
MARKUP_NOISE = re.compile(r"[*`#]+|(?<!\w)_+|_+(?!\w)|^[ \t]*>+[ \t]?", re.MULTILINE)

# kind, table, title expression, body expression
SOURCES: tuple[tuple[Kind, str, str, str], ...] = (
    ("topic", "topics", "title", "prompt"),
    ("writing", "writings", "title", "body"),
    ("comment", "comments", "''", "body"),
    ("chat", "chat_messages", "''", "body"),
)


def _topic_column(table: str) -> str:
    return "id" if table == "topics" else "topic_id"


def ensure_index(engine: Engine) -> None:
    """Create the FTS table and its triggers if they are missing, and fill it the first time."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
                "kind UNINDEXED, object_id UNINDEXED, topic_id UNINDEXED, title, body, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        )
        for kind, table, title, body in SOURCES:
            tid = _topic_column(table)
            new_title = "''" if title == "''" else f"NEW.{title}"
            values = f"'{kind}', NEW.id, NEW.{tid}, {new_title}, NEW.{body}"
            remove = f"DELETE FROM search_index WHERE kind = '{kind}' AND object_id = OLD.id;"
            conn.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS search_{table}_ai AFTER INSERT ON {table} BEGIN "
                    f"INSERT INTO search_index(kind, object_id, topic_id, title, body) VALUES ({values}); END"
                )
            )
            conn.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS search_{table}_au AFTER UPDATE ON {table} BEGIN {remove} "
                    f"INSERT INTO search_index(kind, object_id, topic_id, title, body) VALUES ({values}); END"
                )
            )
            conn.execute(
                text(f"CREATE TRIGGER IF NOT EXISTS search_{table}_ad AFTER DELETE ON {table} BEGIN {remove} END")
            )
        empty = conn.execute(text("SELECT NOT EXISTS (SELECT 1 FROM search_index)")).scalar()
        if empty:
            rebuild(conn)


def rebuild(conn: Connection) -> None:
    """Refill the index from the tables, inside the caller's transaction."""
    conn.execute(text("DELETE FROM search_index"))
    for kind, table, title, body in SOURCES:
        tid = _topic_column(table)
        conn.execute(
            text(
                f"INSERT INTO search_index(kind, object_id, topic_id, title, body) "
                f"SELECT '{kind}', id, {tid}, {title}, {body} FROM {table}"
            )
        )


def fts_query(raw: str) -> str:
    """Turn whatever was typed into an FTS5 expression that cannot error: quoted prefix terms, all required."""
    terms = TERM.findall(raw[:MAX_QUERY_CHARS])[:MAX_TERMS]
    return " ".join('"' + term.replace('"', '""') + '"*' for term in terms if term.strip("'’-"))


def excerpt(snippet: str) -> Markup:
    """The snippet SQLite produced, HTML-escaped, with the match marked and Markdown punctuation thinned."""
    cleaned = re.sub(r"\s+", " ", MARKUP_NOISE.sub("", snippet)).strip()
    return Markup(str(escape(cleaned)).replace(START, "<mark>").replace(END, "</mark>"))


@dataclass(frozen=True)
class Hit:
    kind: Kind
    object_id: int
    topic_id: int
    topic_title: str
    author: str
    when: datetime
    title: Markup
    excerpt: Markup
    href: str


def _href(kind: Kind, topic_id: int, object_id: int) -> str:
    if kind == "topic":
        return f"/topics/{topic_id}/read"
    if kind == "writing":
        return f"/topics/{topic_id}/read#writing-{object_id}"
    if kind == "comment":
        return f"/topics/{topic_id}/read#note-{object_id}"
    if kind == "chat":
        return f"/topics/{topic_id}/read#margin-{object_id}"
    assert_never(kind)


def _load(db: Session, kind: Kind, ids: list[int]) -> dict[int, Topic | Writing | Comment | ChatMessage]:
    if not ids:
        return {}
    if kind == "topic":
        rows: list[Topic | Writing | Comment | ChatMessage] = list(db.query(Topic).filter(Topic.id.in_(ids)))
    elif kind == "writing":
        rows = list(db.query(Writing).options(selectinload(Writing.topic)).filter(Writing.id.in_(ids)))
    elif kind == "comment":
        rows = list(db.query(Comment).options(selectinload(Comment.topic)).filter(Comment.id.in_(ids)))
    elif kind == "chat":
        rows = list(db.query(ChatMessage).options(selectinload(ChatMessage.topic)).filter(ChatMessage.id.in_(ids)))
    else:
        assert_never(kind)
    return {row.id: row for row in rows}


def _readable(user: str, kind: Kind, obj: Topic | Writing | Comment | ChatMessage) -> bool:
    """Search answers exactly what the archive would show: openness, never visibility."""
    if kind == "topic":
        assert isinstance(obj, Topic)
        return access.topic_prompt_open(user, obj) if obj.prompt else access.topic_open(user, obj)
    if kind == "writing":
        assert isinstance(obj, Writing)
        return access.writing_open(user, obj)
    if kind == "comment":
        assert isinstance(obj, Comment)
        return access.comment_open(user, obj)
    if kind == "chat":
        assert isinstance(obj, ChatMessage)
        return obj.topic.share_status == "shared"
    assert_never(kind)


def _as_kind(value: str) -> Kind:
    if value == "topic":
        return "topic"
    if value == "writing":
        return "writing"
    if value == "comment":
        return "comment"
    if value == "chat":
        return "chat"
    raise ValueError(f"unknown search kind: {value}")


def search(db: Session, user: str, raw: str, limit: int = MAX_HITS) -> list[Hit]:
    expression = fts_query(raw)
    if not expression:
        return []
    rows = db.execute(
        text(
            "SELECT kind, object_id, topic_id, "
            f"snippet(search_index, 3, '{START}', '{END}', '', 12) AS title_snip, "
            f"snippet(search_index, 4, '{START}', '{END}', '…', 18) AS body_snip "
            "FROM search_index WHERE search_index MATCH :q ORDER BY bm25(search_index, 0, 0, 0, 3.0, 1.0) "
            "LIMIT :n"
        ),
        {"q": expression, "n": CANDIDATES},
    ).all()
    wanted: dict[Kind, list[int]] = {"topic": [], "writing": [], "comment": [], "chat": []}
    for kind_value, object_id, _topic_id, _t, _b in rows:
        wanted[_as_kind(kind_value)].append(int(object_id))
    loaded = {kind: _load(db, kind, ids) for kind, ids in wanted.items()}

    hits: list[Hit] = []
    for kind_value, object_id, topic_id, title_snip, body_snip in rows:
        kind = _as_kind(kind_value)
        obj = loaded[kind].get(int(object_id))
        if obj is None or not _readable(user, kind, obj):
            continue
        topic = obj if isinstance(obj, Topic) else obj.topic
        if isinstance(obj, Topic):
            author, title = obj.created_by, title_snip or obj.title
        elif isinstance(obj, Writing):
            author, title = obj.author, title_snip or obj.title or "Untitled writing"
        else:
            author, title = obj.author, ""
        hits.append(
            Hit(
                kind=kind,
                object_id=obj.id,
                topic_id=int(topic_id),
                topic_title=topic.title,
                author=author,
                when=obj.created_at,
                title=excerpt(title),
                excerpt=excerpt(body_snip),
                href=_href(kind, int(topic_id), obj.id),
            )
        )
        if len(hits) >= limit:
            break
    return hits
