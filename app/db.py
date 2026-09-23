from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _default_db_path() -> str:
    root = Path(__file__).resolve().parent.parent
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data / 'between.db'}"


DATABASE_URL = os.getenv("DATABASE_URL", _default_db_path())

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        try:
            cursor.execute("PRAGMA journal_mode=MEMORY")
        except Exception:
            pass
        cursor.close()


def migrate() -> None:
    """Add share columns to an existing local SQLite file."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text

    extras = {
        "topics": [
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
        ],
        "writings": [
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
        ],
        "comments": [
            ("share_status", "VARCHAR(20) DEFAULT 'private'"),
            ("offered_at", "DATETIME"),
            ("accepted_at", "DATETIME"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in extras.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            if not rows:
                continue
            have = {row[1] for row in rows}
            for name, spec in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {spec}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
