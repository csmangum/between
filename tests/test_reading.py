"""Read it through: one topic laid out in the order it was written, showing only what is open, with nothing to
press. Sealed things stay on the topic page; the view says so and points there."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import reading
from app.main import app
from app.models import Comment, Topic, Writing


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _pair() -> tuple[TestClient, TestClient]:
    a, b = TestClient(app), TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _topic(client: TestClient, title: str = "Letters", prompt: str = "") -> int:
    created = client.post("/topics", data={"title": title, "prompt": prompt}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _shared_topic(a: TestClient, b: TestClient, **kwargs) -> int:
    topic_id = _topic(a, **kwargs)
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


def _note(client: TestClient, topic_id: int, body: str, writing_id: int | None = None) -> None:
    data = {"body": body} | ({"writing_id": str(writing_id)} if writing_id else {})
    client.post(f"/topics/{topic_id}/comments", data=data, follow_redirects=False)


def _ids(page: str) -> list[str]:
    """Anchors in document order: writing-N, note-N, margin-N."""
    return re.findall(r'id="((?:writing|note|margin)-\d+)"', page)


# --- the measure -------------------------------------------------------------------------------


def test_words_and_minutes():
    assert reading.word_count("") == 0
    assert reading.word_count("**Don’t** stop — it's `fine`, isn’t it?") == 6
    assert reading.minutes_for(0) == 0
    assert reading.minutes_for(1) == 1
    assert reading.minutes_for(reading.WORDS_PER_MINUTE * 3) == 3


# --- what is laid out, and in what order -------------------------------------------------------


def test_pages_and_loose_notes_fall_in_the_order_they_were_written(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client, "Orchard", prompt="About the trees.")
    first = _writing(client, topic_id, "one two three", title="First")
    _note(client, topic_id, "a note between pages")
    second = _writing(client, topic_id, "four five", title="Second")
    _note(client, topic_id, "a note on the second page", writing_id=second)

    page = client.get(f"/topics/{topic_id}/read").text
    note_ids = [c.id for c in db_session.query(Comment).order_by(Comment.id)]
    assert _ids(page) == [f"writing-{first}", f"note-{note_ids[0]}", f"writing-{second}", f"note-{note_ids[1]}"]
    assert "About the trees." in page
    assert 'class="contents"' in page and f'href="#writing-{second}"' in page
    assert "2 pages" in page and "15 words" in page and "about 1 minute" in page  # notes count toward the read
    assert "<form" not in page.split('id="content"')[1]  # nothing to press inside the reading
    assert "Read it through" in client.get(f"/topics/{topic_id}").text
    assert f'href="/topics/{topic_id}/read"' in client.get("/archive").text


def test_one_page_has_no_contents_and_an_empty_topic_says_so(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client, "Thin")
    assert "Nothing to read here yet" in client.get(f"/topics/{topic_id}/read").text
    _writing(client, topic_id, "a single page")
    page = client.get(f"/topics/{topic_id}/read").text
    assert 'class="contents"' not in page and "1 page" in page


# --- who may read what -------------------------------------------------------------------------


def test_the_view_shows_openness_only_and_sends_sealed_things_to_the_topic_page(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Field", prompt="open-prompt-word")
    opened = _opened_writing(a, b, topic_id, "opened-page-word", title="Opened")
    sealed = _writing(a, topic_id, "sealed-page-word", title="sealed-title-word")
    a.post(f"/writings/{sealed}/offer", follow_redirects=False)
    _writing(a, topic_id, "hidden-page-word")
    _note(a, topic_id, "private-note-word", writing_id=opened)
    _note(b, topic_id, "their-own-note-word", writing_id=opened)

    theirs = b.get(f"/topics/{topic_id}/read").text
    assert "open-prompt-word" in theirs and "opened-page-word" in theirs and "their-own-note-word" in theirs
    for word in ("sealed-page-word", "sealed-title-word", "hidden-page-word", "private-note-word"):
        assert word not in theirs, word
    assert _ids(theirs)[0] == f"writing-{opened}"
    assert "1 sealed page wait" in theirs and f'href="/topics/{topic_id}"' in theirs

    mine = a.get(f"/topics/{topic_id}/read").text
    assert "hidden-page-word" in mine and "private-note-word" in mine and "sealed page" not in mine
    assert _ids(mine) == [f"writing-{opened}", *_ids(mine)[1:]]


def test_a_contributor_reads_their_own_words_but_not_the_prompt_after_a_return(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Field", prompt="open-prompt-word")
    _opened_writing(a, b, topic_id, "their-page-word")
    _writing(b, topic_id, "my-page-word", title="Mine")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    page = b.get(f"/topics/{topic_id}/read").text
    assert "my-page-word" in page and "their-page-word" not in page and "open-prompt-word" not in page
    assert "Returned to Chris" in page  # the stub for the page they had opened


def test_an_outsider_and_a_sealed_offer_are_redirected(db_session: Session):
    a, b = _pair()
    private = _topic(a, "Secret")
    assert b.get(f"/topics/{private}/read", follow_redirects=False).headers["location"] == "/"
    a.post(f"/topics/{private}/offer", follow_redirects=False)
    sealed = b.get(f"/topics/{private}/read", follow_redirects=False)
    assert sealed.status_code == 303 and sealed.headers["location"] == f"/topics/{private}"
    assert b.get("/topics/999/read", follow_redirects=False).headers["location"] == "/"
    anonymous = TestClient(app)
    assert anonymous.get(f"/topics/{private}/read", follow_redirects=False).headers["location"] == "/login"


# --- the margin and the neighbours -------------------------------------------------------------


def test_the_margin_reads_only_while_the_topic_is_shared(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        ws.send_json({"type": "chat", "body": "a margin-only-word here"})
        ws.receive_json()
    page = a.get(f"/topics/{topic_id}/read").text
    assert 'id="margin"' in page and "margin-only-word" in page and re.search(r'id="margin-\d+"', page)
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    assert "margin-only-word" not in a.get(f"/topics/{topic_id}/read").text


def test_earlier_and_later_walk_through_what_you_keep(db_session: Session):
    a, b = _pair()
    first = _topic(a, "First")
    theirs = _topic(b, "Theirs, private")
    last = _shared_topic(a, b, title="Last")

    middle = b.get(f"/topics/{theirs}/read").text
    assert f'href="/topics/{first}/read"' not in middle  # the first is not theirs to read
    assert f'href="/topics/{last}/read"' in middle and "Later" in middle

    mine = a.get(f"/topics/{first}/read").text
    assert f'href="/topics/{last}/read"' in mine and f'href="/topics/{theirs}/read"' not in mine
    assert 'class="reading-prev"' not in mine
    assert 'class="reading-next"' not in a.get(f"/topics/{last}/read").text


def test_build_reading_on_bare_objects(db_session: Session):
    topic = Topic(title="Bare", created_by="chris", share_status="private", prompt="p")
    db_session.add(topic)
    db_session.flush()
    db_session.add(Writing(topic_id=topic.id, author="chris", title="", body="w " * 440, share_status="private"))
    db_session.commit()
    loaded = db_session.get(Topic, topic.id)
    assert loaded is not None
    view = reading.build_reading(db_session, "chris", loaded)
    assert [p.kind for p in view.pieces] == ["page"] and view.pages[0].title == "Untitled writing"
    assert view.words == 440 and view.minutes == 2 and view.sealed == 0 and view.margin == []
    assert view.previous is None and view.following is None and view.prompt == "p"
