"""Open question 2, answered: when something open between you returns to a desk, the other person keeps a
line that says so — never the page. Sealed offers that were never opened leave nothing behind."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import share
from app.main import app
from app.models import Comment, Topic, Withdrawal, Writing


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _pair() -> tuple[TestClient, TestClient]:
    a, b = TestClient(app), TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _topic(client: TestClient, title: str = "Letters") -> int:
    created = client.post("/topics", data={"title": title, "prompt": ""}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _shared_topic(a: TestClient, b: TestClient, title: str = "Letters") -> int:
    topic_id = _topic(a, title)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


def _writing(client: TestClient, topic_id: int, body: str, title: str = "") -> int:
    r = client.post(f"/topics/{topic_id}/writings", data={"title": title, "body": body}, follow_redirects=False)
    return int(r.headers["location"].rsplit("-", 1)[-1])


def _opened_writing(a: TestClient, b: TestClient, topic_id: int, body: str, title: str = "") -> int:
    wid = _writing(a, topic_id, body, title)
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    return wid


def _stubs(db: Session) -> list[Withdrawal]:
    return db.query(Withdrawal).order_by(Withdrawal.id).all()


# --- the rule itself -------------------------------------------------------------------------


def test_only_what_was_open_leaves_a_stub(db_session: Session):
    topic = Topic(id=1, title="T", created_by="chris", share_status="shared")
    opened = Writing(id=2, topic_id=1, author="chris", body="x", title="Opened", share_status="shared")
    opened.topic = topic
    sealed = Writing(id=3, topic_id=1, author="chris", body="y", share_status="offered")
    sealed.topic = topic
    private = Writing(id=4, topic_id=1, author="chris", body="z", share_status="private")
    private.topic = topic

    stub = share.withdrawal("writing", opened, "chris")
    assert stub is not None
    assert (stub.kind, stub.object_id, stub.topic_id, stub.title, stub.author, stub.actor) == (
        "writing",
        2,
        1,
        "Opened",
        "chris",
        "chris",
    )
    assert share.withdrawal("writing", sealed, "chris") is None
    assert share.withdrawal("writing", private, "chris") is None
    assert share.withdrawal("topic", topic, "chris") is not None


def test_folding_a_topic_records_a_stub_for_each_thing_that_was_open(db_session: Session):
    topic = Topic(id=1, title="T", created_by="chris", share_status="shared")
    mine = Writing(id=2, topic_id=1, author="chris", body="x", share_status="shared")
    theirs = Writing(id=3, topic_id=1, author="friend", body="y", share_status="shared")
    sealed = Writing(id=4, topic_id=1, author="chris", body="z", share_status="offered")
    note = Comment(id=5, topic_id=1, author="friend", body="n", share_status="shared")
    for obj in (mine, theirs, sealed, note):
        obj.topic = topic
    topic.writings = [mine, theirs, sealed]
    topic.comments = [note]

    writings, comments, stubs = share.fold_topic(topic, "chris")

    assert {w.id for w in writings} == {2, 3, 4} and [c.id for c in comments] == [5]
    assert all(w.share_status == "private" for w in writings)
    assert [(s.kind, s.object_id, s.author) for s in stubs] == [
        ("topic", 1, "chris"),
        ("writing", 2, "chris"),
        ("writing", 3, "friend"),
        ("comment", 5, "friend"),
    ]
    assert all(s.actor == "chris" for s in stubs)


# --- through the room ------------------------------------------------------------------------


def test_returning_an_opened_writing_leaves_a_line_for_the_reader(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _opened_writing(a, b, topic_id, "the-body-they-read", title="Evening")

    r = a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    assert r.status_code == 303

    stubs = _stubs(db_session)
    assert len(stubs) == 1 and stubs[0].kind == "writing" and stubs[0].object_id == wid
    assert stubs[0].opened_at is not None and stubs[0].author == "chris" and stubs[0].actor == "chris"

    reader = b.get(f"/topics/{topic_id}").text
    assert "the-body-they-read" not in reader
    assert "Returned to Chris's desk" in reader and "Evening" in reader
    assert "Chris returned it to their desk" in reader

    # the author sees the page itself, plus the fact that the other person keeps a line about it
    author = a.get(f"/topics/{topic_id}").text
    assert "the-body-they-read" in author
    assert author.count("keeps a note that it was open, not the page.") == 2  # the flash, and the line under the page
    assert "Returned to Friend's desk" not in author


def test_a_sealed_writing_pulled_back_or_declined_leaves_nothing(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "never-opened")
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/decline", follow_redirects=False)
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    assert _stubs(db_session) == []
    assert "Returned to" not in b.get(f"/topics/{topic_id}").text


def test_opening_it_again_clears_the_stub(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _opened_writing(a, b, topic_id, "body", title="Again")
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    assert len(_stubs(db_session)) == 1

    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    assert len(_stubs(db_session)) == 1  # still sealed: the stub stands until it is actually opened
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    db_session.expire_all()
    assert _stubs(db_session) == []
    assert "Returned to" not in b.get(f"/topics/{topic_id}").text


def test_returning_a_topic_leaves_lines_for_both_sides(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Together")
    a_wid = _opened_writing(a, b, topic_id, "a-page", title="From A")
    b_wid = _opened_writing(b, a, topic_id, "b-page", title="From B")

    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    kinds = {(s.kind, s.object_id): s for s in _stubs(db_session)}
    assert set(kinds) == {("topic", topic_id), ("writing", a_wid), ("writing", b_wid)}
    assert all(s.actor == "chris" for s in kinds.values())

    # B contributed, so B still opens the topic shell: the line explains what happened, and lists A's page.
    b_page = b.get(f"/topics/{topic_id}").text
    assert "when Chris returned it to their desk" in b_page
    assert "From A" in b_page and "a-page" not in b_page
    assert "From B" not in b_page.split("Returned to Chris's desk")[-1]  # B's own page is not a stub for B

    # A lost B's page too, by A's own hand; the line says so without pretending otherwise.
    a_page = a.get(f"/topics/{topic_id}").text
    assert "From B" in a_page and "b-page" not in a_page
    assert "returned to Friend's desk" in a_page and "when you returned the topic" in a_page


def test_archive_and_exports_keep_the_line_not_the_page(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Gone")
    _opened_writing(a, b, topic_id, "secret-after", title="Letter")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    # B never wrote in it, so the topic is no longer open to B at all; the archive keeps the stub band.
    archive = b.get("/archive").text
    assert "secret-after" not in archive
    assert "Returned to Chris's desk" in archive and "Gone" in archive and "Letter" in archive

    exported = json.loads(b.get("/export.json").text)
    assert exported["topics"] == []
    [group] = exported["returned_topics"]
    assert {s["kind"] for s in group} == {"topic", "writing"}
    assert all("body" not in s for s in group)
    assert group[0]["returned_by"] == "chris" and group[0]["opened_at"].endswith("+00:00")

    md = b.get("/export.md").text
    assert "## Returned" in md and "“Gone”" in md and "“Letter”" in md and "secret-after" not in md

    # A's archive and exports are unchanged: the pages are A's own.
    a_archive = a.get("/archive").text
    assert "secret-after" in a_archive and "Returned to" not in a_archive
    assert json.loads(a.get("/export.json").text)["returned_topics"] == []


def test_the_line_outlives_the_page(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Brief")
    wid = _opened_writing(a, b, topic_id, "body", title="Removed later")
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    a.post(f"/writings/{wid}/delete", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Writing, wid) is None
    assert len(_stubs(db_session)) == 1
    assert "Removed later" in b.get(f"/topics/{topic_id}").text
