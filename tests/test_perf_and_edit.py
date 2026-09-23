from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.main import attach_topic_counts
from app.markdown_render import render_markdown
from app.models import ChatMessage, Comment, Topic, Writing


def _login(client, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_markdown_cache_hits():
    first = render_markdown("hello **world**\n\n- a\n- b")
    second = render_markdown("hello **world**\n\n- a\n- b")
    assert first == second
    assert "<strong>world</strong>" in first
    from app.markdown_render import _render_cached

    assert _render_cached.cache_info().hits >= 1


def test_markdown_blocks_remote_images():
    html = render_markdown("![tracker](https://example.com/pixel.png)")
    assert "<img" not in html
    assert "tracker" in html


def test_edit_private_writing_keeps_single_local_file(client, db_session: Session):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Edit me", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{topic_id}/writings", data={"title": "Draft", "body": "v1"}, follow_redirects=False)

    writing = db_session.query(Writing).filter(Writing.topic_id == topic_id).one()
    writing_id = writing.id

    edited = client.post(
        f"/writings/{writing_id}/edit",
        data={"title": "Draft v2", "body": "revised privately"},
        follow_redirects=False,
    )
    assert edited.status_code == 303
    page = client.get(f"/topics/{topic_id}")
    assert "revised privately" in page.text
    assert "Draft v2" in page.text

    folder = Path("/tmp/between-tests/local/chris/0001-edit-me")
    assert (folder / f"writing-{writing_id}.md").exists()
    assert list(folder.glob(f"writing-{writing_id}-*.md")) == []


def test_home_avoids_n_plus_one(client, db_session: Session):
    _login(client, "chris", "pass1")
    for i in range(12):
        topic = Topic(title=f"T{i}", prompt="", created_by="chris", share_status="private")
        db_session.add(topic)
        db_session.flush()
        db_session.add(Writing(topic_id=topic.id, author="chris", title="w", body="body", share_status="private"))
    db_session.commit()
    topics = db_session.query(Topic).all()
    queries: list[str] = []

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _count(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    try:
        attach_topic_counts(db_session, topics, "chris")
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _count)
    count_queries = [q for q in queries if "count(" in q.lower() or "count (" in q.lower()]
    assert len(count_queries) <= 2
    assert topics[0].writing_count == 1
    home = client.get("/")
    assert home.status_code == 200
    assert "T0" in home.text


def test_shared_counts_hide_private_drafts(client, db_session: Session):
    topic = Topic(title="Letters", prompt="", created_by="chris", share_status="shared")
    db_session.add(topic)
    db_session.flush()
    db_session.add(Writing(topic_id=topic.id, author="chris", title="draft", body="private", share_status="private"))
    db_session.add(Writing(topic_id=topic.id, author="chris", title="open", body="shared", share_status="shared"))
    db_session.commit()

    _login(client, "friend", "pass2")
    home = client.get("/")
    assert home.status_code == 200
    assert "Letters" in home.text
    assert "1 writings" in home.text
    assert "2 writings" not in home.text


def test_comment_requires_matching_topic_writing(client, db_session: Session):
    _login(client, "chris", "pass1")
    first = client.post("/topics", data={"title": "One", "prompt": ""}, follow_redirects=False)
    second = client.post("/topics", data={"title": "Two", "prompt": ""}, follow_redirects=False)
    first_id = int(first.headers["location"].rsplit("/", 1)[-1])
    second_id = int(second.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{second_id}/writings", data={"title": "Elsewhere", "body": "body"}, follow_redirects=False)
    writing = db_session.query(Writing).filter(Writing.topic_id == second_id).one()

    response = client.post(
        f"/topics/{first_id}/comments",
        data={"body": "cross-topic", "writing_id": writing.id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/topics/{first_id}"
    assert db_session.query(Comment).count() == 0


def test_private_comment_mirror_recreated_on_decline_and_revoke(client, db_session: Session):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Comments", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", "pass1")
    client.post(f"/topics/{topic_id}/comments", data={"body": "quiet note"}, follow_redirects=False)

    comment = db_session.query(Comment).one()
    local_path = Path(f"/tmp/between-tests/local/chris/0001-comments/comment-{comment.id}.md")
    assert local_path.exists()
    assert "_status: private_" in local_path.read_text(encoding="utf-8")

    client.post(f"/comments/{comment.id}/offer", follow_redirects=False)
    local_path.unlink()
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")
    client.post(f"/comments/{comment.id}/decline", follow_redirects=False)
    assert local_path.exists()
    assert "_status: private_" in local_path.read_text(encoding="utf-8")

    client.post("/logout", follow_redirects=False)
    _login(client, "chris", "pass1")
    client.post(f"/comments/{comment.id}/offer", follow_redirects=False)
    local_path.unlink()
    client.post(f"/comments/{comment.id}/revoke", follow_redirects=False)
    assert local_path.exists()
    assert "_status: private_" in local_path.read_text(encoding="utf-8")


def test_invalid_writing_ids_redirect_home(client):
    _login(client, "chris", "pass1")
    for path in ("offer", "accept", "decline", "revoke"):
        response = client.post(f"/writings/9999/{path}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_accept_writing_requires_shared_topic(client, db_session: Session):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Letters", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", "pass1")
    client.post(f"/topics/{topic_id}/writings", data={"title": "Draft", "body": "v1"}, follow_redirects=False)
    writing = db_session.query(Writing).one()
    client.post(f"/writings/{writing.id}/offer", follow_redirects=False)
    client.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")
    response = client.post(f"/writings/{writing.id}/accept", follow_redirects=False)
    assert response.status_code == 303
    db_session.expire_all()
    writing = db_session.get(Writing, writing.id)
    assert writing.share_status == "offered"


def test_static_cache_header(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age" in cache_control
    assert "immutable" not in cache_control


def test_health_reports_db(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["db"] == "ok"
