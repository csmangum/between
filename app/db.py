from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _default_db_path() -> str:
    root = Path(__file__).resolve().parent.parent
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data / 'between.db'}"


DATABASE_URL = os.getenv("DATABASE_URL") or _default_db_path()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA cache_size=-8000")
        except Exception:
            pass
        cursor.close()


def migrate() -> None:
    """Add share columns and indexes to an existing local SQLite file."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    extras = {
        "topics": [
            ("source_id", "VARCHAR(36)"),
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
        ],
        "writings": [
            ("source_id", "VARCHAR(36)"),
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
            ("revision_title", "VARCHAR(240)"),
            ("revision_body", "TEXT"),
            ("revision_status", "VARCHAR(20)"),
            ("revision_offered_at", "DATETIME"),
        ],
        "comments": [
            ("source_id", "VARCHAR(36)"),
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
        ],
        "withdrawals": [
            ("source_id", "VARCHAR(36)"),
        ],
    }
    indexes = [
        ("ix_topics_updated_at", "topics", "updated_at"),
        ("ix_topics_share_status", "topics", "share_status"),
        ("ix_topics_created_by", "topics", "created_by"),
        ("ix_writings_topic_id", "writings", "topic_id"),
        ("ix_writings_share_status", "writings", "share_status"),
        ("ix_comments_topic_id", "comments", "topic_id"),
        ("ix_comments_writing_id", "comments", "writing_id"),
        ("ix_chat_messages_topic_id", "chat_messages", "topic_id"),
    ]
    with engine.begin() as conn:
        for table, cols in extras.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            if not rows:
                continue
            have = {row[1] for row in rows}
            for name, spec in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {spec}"))
            if "source_id" in {name for name, _ in cols}:
                rows = conn.execute(text(f"SELECT rowid FROM {table} WHERE source_id IS NULL")).fetchall()
                for (rowid,) in rows:
                    conn.execute(
                        text(f"UPDATE {table} SET source_id = :source_id WHERE rowid = :rowid"),
                        {"source_id": str(uuid4()), "rowid": rowid},
                    )
        if conn.execute(text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'withdrawals'")).fetchone():
            conn.execute(
                text(
                    "UPDATE withdrawals SET source_id = "
                    "(SELECT source_id FROM topics WHERE withdrawals.kind = 'topic' "
                    "AND topics.id = withdrawals.object_id) "
                    "WHERE kind = 'topic' AND EXISTS "
                    "(SELECT 1 FROM topics WHERE topics.id = withdrawals.object_id)"
                )
            )
            conn.execute(
                text(
                    "UPDATE withdrawals SET source_id = "
                    "(SELECT source_id FROM writings WHERE withdrawals.kind = 'writing' "
                    "AND writings.id = withdrawals.object_id) "
                    "WHERE kind = 'writing' AND EXISTS "
                    "(SELECT 1 FROM writings WHERE writings.id = withdrawals.object_id)"
                )
            )
            conn.execute(
                text(
                    "UPDATE withdrawals SET source_id = "
                    "(SELECT source_id FROM comments WHERE withdrawals.kind = 'comment' "
                    "AND comments.id = withdrawals.object_id) "
                    "WHERE kind = 'comment' AND EXISTS "
                    "(SELECT 1 FROM comments WHERE comments.id = withdrawals.object_id)"
                )
            )
        have_indexes: set[str] = set()
        for table in ("topics", "writings", "comments", "chat_messages"):
            have_indexes.update(row[1] for row in conn.execute(text(f"PRAGMA index_list('{table}')")).fetchall())
        for name, table, column in indexes:
            if name not in have_indexes:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
