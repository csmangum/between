from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

TEST_DB = Path("/tmp/between-perf-func.db")
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["USER1_NAME"] = "chris"
os.environ["USER1_DISPLAY"] = "Chris"
os.environ["USER1_PASSWORD"] = "pass1"
os.environ["USER2_NAME"] = "friend"
os.environ["USER2_DISPLAY"] = "Friend"
os.environ["USER2_PASSWORD"] = "pass2"

from app.db import Base, engine, get_db  # noqa: E402
from app.main import app, attach_topic_counts  # noqa: E402
from app.markdown_render import clear_markdown_cache, render_markdown  # noqa: E402
from app.models import Topic, Writing  # noqa: E402

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_markdown_cache_hits():
    clear_markdown_cache()
    first = render_markdown("hello **world**\n\n- a\n- b")
    second = render_markdown("hello **world**\n\n- a\n- b")
    assert first == second
    assert "<strong>world</strong>" in first
    from app.markdown_render import _render_cached

    assert _render_cached.cache_info().hits >= 1


def test_edit_private_writing(client: TestClient):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Edit me", "prompt": ""}, follow_redirects=False)
    topic_id = created.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/topics/{topic_id}/writings", data={"title": "Draft", "body": "v1"}, follow_redirects=False)

    db = TestingSessionLocal()
    writing = db.query(Writing).filter(Writing.topic_id == int(topic_id)).one()
    writing_id = writing.id
    db.close()

    edited = client.post(
        f"/writings/{writing_id}/edit",
        data={"title": "Draft v2", "body": "revised privately"},
        follow_redirects=False,
    )
    assert edited.status_code == 303
    page = client.get(f"/topics/{topic_id}")
    assert "revised privately" in page.text
    assert "Draft v2" in page.text


def test_home_avoids_n_plus_one(client: TestClient):
    _login(client, "chris", "pass1")
    db = TestingSessionLocal()
    for i in range(12):
        topic = Topic(title=f"T{i}", prompt="", created_by="chris", share_status="private")
        db.add(topic)
        db.flush()
        db.add(Writing(topic_id=topic.id, author="chris", title="w", body="body", share_status="private"))
    db.commit()
    topics = db.query(Topic).all()
    queries: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    attach_topic_counts(db, topics)
    # one query for writings + one for messages counts, not per-topic
    count_queries = [q for q in queries if "count(" in q.lower() or "count (" in q.lower()]
    assert len(count_queries) <= 2
    assert topics[0].writing_count == 1
    db.close()
    # page still renders
    home = client.get("/")
    assert home.status_code == 200
    assert "T0" in home.text


def test_health_reports_db(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["db"] == "ok"


def test_static_cache_header(client: TestClient):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "max-age" in response.headers.get("cache-control", "")
