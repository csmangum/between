from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Writing
from app.store import data_root

from .conftest import CHRIS_PASSWORD, FRIEND_PASSWORD


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _shared_writing(client: TestClient, db_session: Session) -> tuple[int, int]:
    _login(client, "chris", CHRIS_PASSWORD)
    created = client.post("/topics", data={"title": "Letters", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(
        f"/topics/{topic_id}/writings",
        data={"title": "First", "body": "the opened page"},
        follow_redirects=False,
    )
    writing_id = db_session.query(Writing).filter(Writing.topic_id == topic_id).one().id
    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", CHRIS_PASSWORD)
    client.post(f"/writings/{writing_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    client.post(f"/writings/{writing_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", CHRIS_PASSWORD)
    return topic_id, writing_id


def test_revision_stays_sealed_until_opened(client: TestClient, db_session: Session):
    topic_id, writing_id = _shared_writing(client, db_session)
    saved = client.post(
        f"/writings/{writing_id}/revision",
        data={"title": "Second", "body": "a later page"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    desk = client.get(f"/topics/{topic_id}")
    assert "a later page" in desk.text
    assert "the opened page" in desk.text

    client.post(f"/writings/{writing_id}/revision/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    sealed = client.get(f"/topics/{topic_id}")
    assert "the opened page" in sealed.text
    assert "a later page" not in sealed.text
    assert "Open the revision" in sealed.text

    shared_files = list((data_root() / "shared").rglob(f"writing-{writing_id}.md"))
    assert shared_files
    assert "a later page" not in shared_files[0].read_text(encoding="utf-8")
    export = client.get("/export.md")
    assert "a later page" not in export.text

    opened = client.post(f"/writings/{writing_id}/revision/accept", follow_redirects=False)
    assert opened.status_code == 303
    page = client.get(f"/topics/{topic_id}")
    assert "a later page" in page.text
    assert "the opened page" not in page.text
    assert "Second" in page.text
    assert "a later page" in shared_files[0].read_text(encoding="utf-8")


def test_declined_revision_returns_to_the_desk(client: TestClient, db_session: Session):
    topic_id, writing_id = _shared_writing(client, db_session)
    client.post(
        f"/writings/{writing_id}/revision",
        data={"title": "Second", "body": "a later page"},
        follow_redirects=False,
    )
    client.post(f"/writings/{writing_id}/revision/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    client.post(f"/writings/{writing_id}/revision/decline", follow_redirects=False)
    still = client.get(f"/topics/{topic_id}")
    assert "the opened page" in still.text
    assert "a later page" not in still.text
    assert "Open the revision" not in still.text

    client.post("/logout", follow_redirects=False)
    _login(client, "chris", CHRIS_PASSWORD)
    desk = client.get(f"/topics/{topic_id}")
    assert "a later page" in desk.text
    assert "Send this revision" in desk.text
    writing = db_session.get(Writing, writing_id)
    assert writing.revision_status == "private"
    assert writing.body == "the opened page"


def test_pulling_the_writing_back_keeps_the_revision(client: TestClient, db_session: Session):
    topic_id, writing_id = _shared_writing(client, db_session)
    client.post(
        f"/writings/{writing_id}/revision",
        data={"title": "Second", "body": "a later page"},
        follow_redirects=False,
    )
    client.post(f"/writings/{writing_id}/revoke", follow_redirects=False)
    writing = db_session.get(Writing, writing_id)
    db_session.refresh(writing)
    assert writing.share_status == "private"
    assert writing.body == "a later page"
    assert writing.revision_status is None
    page = client.get(f"/topics/{topic_id}")
    assert "a later page" in page.text
