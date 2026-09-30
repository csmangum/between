from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Comment, Topic, Writing
from app.store import data_root


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _pair() -> tuple[TestClient, TestClient]:
    a = TestClient(app)
    b = TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _topic(client: TestClient, title: str = "Letters", prompt: str = "") -> int:
    created = client.post("/topics", data={"title": title, "prompt": prompt}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _writing(client: TestClient, topic_id: int, body: str, title: str = "w") -> int:
    r = client.post(f"/topics/{topic_id}/writings", data={"title": title, "body": body}, follow_redirects=False)
    return int(r.headers["location"].rsplit("-", 1)[-1])


def _note(client: TestClient, topic_id: int, body: str, writing_id: int | None = None) -> None:
    data = {"body": body}
    if writing_id:
        data["writing_id"] = str(writing_id)
    client.post(f"/topics/{topic_id}/comments", data=data, follow_redirects=False)


def _shared_topic(a: TestClient, b: TestClient) -> int:
    topic_id = _topic(a)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


# --- editing a private topic ---------------------------------------------------


def test_private_topic_can_be_retitled_and_its_mirror_follows(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client, title="First name", prompt="old note")
    wid = _writing(client, topic_id, "inside")
    local = data_root() / "local" / "chris"
    assert [p.name for p in local.glob(f"{topic_id:04d}-*")] == [f"{topic_id:04d}-first-name"]

    response = client.post(
        f"/topics/{topic_id}/edit", data={"title": "  Second name ", "prompt": "new note"}, follow_redirects=False
    )
    assert response.headers["location"] == f"/topics/{topic_id}"
    db_session.expire_all()
    topic = db_session.get(Topic, topic_id)
    assert (topic.title, topic.prompt) == ("Second name", "new note")

    folders = list(local.glob(f"{topic_id:04d}-*"))
    assert [p.name for p in folders] == [f"{topic_id:04d}-second-name"]
    assert (folders[0] / f"writing-{wid}.md").exists()
    assert "Second name" in (folders[0] / "topic.md").read_text()
    assert "Topic updated" in client.get(f"/topics/{topic_id}").text


def test_blank_title_keeps_the_old_one(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client, title="Keep me")
    client.post(f"/topics/{topic_id}/edit", data={"title": "  ", "prompt": ""}, follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Topic, topic_id).title == "Keep me"
    assert "The old one stays" in client.get(f"/topics/{topic_id}").text


def test_offered_or_shared_topics_cannot_be_retitled(db_session: Session):
    a, b = _pair()
    topic_id = _topic(a, title="Sealed name")
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    assert "Change the title" not in a.get(f"/topics/{topic_id}").text
    a.post(f"/topics/{topic_id}/edit", data={"title": "Bait"}, follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    b.post(f"/topics/{topic_id}/edit", data={"title": "Theirs"}, follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Topic, topic_id).title == "Sealed name"


def test_topic_page_offers_edit_and_remove_only_on_the_creators_private_desk(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    page = client.get(f"/topics/{topic_id}").text
    assert f'action="/topics/{topic_id}/edit"' in page
    assert f'action="/topics/{topic_id}/delete"' in page
    assert "data-confirm=" in page


def test_remove_topic_is_hidden_when_it_holds_their_words():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    _writing(b, topic_id, "theirs")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    page = a.get(f"/topics/{topic_id}").text
    assert f'action="/topics/{topic_id}/edit"' in page
    assert f'action="/topics/{topic_id}/delete"' not in page


# --- removing a private writing ---------------------------------------------------


def test_private_writing_and_its_own_notes_can_be_removed(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    wid = _writing(client, topic_id, "a page to lose")
    _note(client, topic_id, "note on it", wid)
    local = data_root() / "local" / "chris"
    assert list(local.rglob(f"writing-{wid}.md"))
    assert f'action="/writings/{wid}/delete"' in client.get(f"/topics/{topic_id}").text

    response = client.post(f"/writings/{wid}/delete", follow_redirects=False)
    assert response.headers["location"] == f"/topics/{topic_id}"
    db_session.expire_all()
    assert db_session.get(Writing, wid) is None
    assert db_session.query(Comment).filter(Comment.writing_id == wid).count() == 0
    assert not list(local.rglob(f"writing-{wid}.md"))
    assert not list(local.rglob("comment-*.md"))
    page = client.get(f"/topics/{topic_id}").text
    assert "a page to lose" not in page
    assert "Writing removed" in page


def test_sent_writing_must_be_pulled_back_before_removal(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "sent page")
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    a.post(f"/writings/{wid}/delete", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Writing, wid) is not None
    assert "Pull it back to your desk before removing it" in a.get(f"/topics/{topic_id}").text


def test_only_the_author_removes_a_writing(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "mine")
    b.post(f"/writings/{wid}/delete", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Writing, wid) is not None


def test_writing_holding_their_notes_stays(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "read together")
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    _note(b, topic_id, "their note", wid)
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)

    assert f'action="/writings/{wid}/delete"' not in a.get(f"/topics/{topic_id}").text
    a.post(f"/writings/{wid}/delete", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Writing, wid) is not None
    assert db_session.query(Comment).filter(Comment.author == "friend").count() == 1


# --- removing a private note ---------------------------------------------------


def test_private_note_can_be_removed_by_its_author(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    _note(client, topic_id, "a passing thought")
    cid = db_session.query(Comment).one().id
    local = data_root() / "local" / "chris"
    assert list(local.rglob(f"comment-{cid}.md"))
    assert f'action="/comments/{cid}/delete"' in client.get(f"/topics/{topic_id}").text

    response = client.post(f"/comments/{cid}/delete", follow_redirects=False)
    assert response.headers["location"] == f"/topics/{topic_id}#comments"
    db_session.expire_all()
    assert db_session.get(Comment, cid) is None
    assert not list(local.rglob(f"comment-{cid}.md"))


def test_private_note_with_own_reply_cannot_be_removed(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    _note(client, topic_id, "parent")
    parent_id = db_session.query(Comment).one().id
    client.post(
        f"/topics/{topic_id}/comments",
        data={"body": "reply", "parent_id": str(parent_id)},
        follow_redirects=False,
    )

    response = client.post(f"/comments/{parent_id}/delete", follow_redirects=False)

    assert response.headers["location"] == f"/topics/{topic_id}#comments"
    db_session.expire_all()
    assert db_session.get(Comment, parent_id) is not None
    assert db_session.query(Comment).count() == 2


def test_shared_note_and_their_note_cannot_be_removed(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    _note(a, topic_id, "sent note")
    cid = db_session.query(Comment).one().id
    a.post(f"/comments/{cid}/offer", follow_redirects=False)
    b.post(f"/comments/{cid}/accept", follow_redirects=False)

    b.post(f"/comments/{cid}/delete", follow_redirects=False)
    a.post(f"/comments/{cid}/delete", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Comment, cid) is not None
    assert "Pull the note back before removing it" in a.get(f"/topics/{topic_id}").text
