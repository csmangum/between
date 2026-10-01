from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models import ChatMessage, Topic
from app.reading import word_count
from app.views import reading_time, when_tag

HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
CONTENTS_OPEN = '<nav class="contents" aria-label="Writings in this topic">'


def _contents(page: str) -> str:
    return page.split(CONTENTS_OPEN, 1)[1].split("</nav>", 1)[0]


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _topic(client: TestClient, title: str = "Letters") -> int:
    created = client.post("/topics", data={"title": title, "prompt": ""}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _writing(client: TestClient, topic_id: int, body: str, title: str = "") -> int:
    response = client.post(f"/topics/{topic_id}/writings", data={"title": title, "body": body}, follow_redirects=False)
    return int(response.headers["location"].rsplit("-", 1)[-1])


def _pair():
    a = TestClient(app)
    b = TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _shared_topic(a: TestClient, b: TestClient) -> int:
    topic_id = _topic(a)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


# --- filters -------------------------------------------------------------------


def test_when_tag_carries_the_exact_moment():
    moment = datetime(2026, 3, 4, 15, 6, 7, tzinfo=UTC)
    html = str(when_tag(moment))
    assert html.startswith('<time datetime="2026-03-04T15:06:07+00:00" title="')
    assert html.endswith("</time>")
    assert "2026" in html
    assert when_tag(None) == ""


def test_when_tag_escapes_nothing_unsafe_through():
    naive = datetime(2026, 1, 1, 0, 0, 0)
    html = str(when_tag(naive))
    assert 'datetime="2026-01-01T00:00:00+00:00"' in html


def test_reading_time_speaks_in_the_room_voice():
    assert reading_time("") == ""
    assert reading_time(None) == ""
    assert reading_time("a few words here") == "under a minute"
    assert reading_time(" ".join(["word"] * 199)) == "under a minute"
    assert reading_time(" ".join(["word"] * 200)) == "1 min read"
    assert reading_time(" ".join(["word"] * 1000)) == "5 min read"
    assert word_count("one two  three\nfour") == 4


# --- preview -------------------------------------------------------------------


def test_preview_needs_someone_in_the_room(client: TestClient):
    response = client.post("/preview", data={"body": "# hi"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_preview_renders_like_the_page_and_stays_clean(client: TestClient):
    _login(client, "chris", "pass1")
    response = client.post(
        "/preview",
        data={"body": "# A heading\n\n<script>alert(1)</script>**bold** [x](javascript:alert(1))"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "<h1>A heading</h1>" in response.text
    assert "<strong>bold</strong>" in response.text
    assert "<script" not in response.text
    assert "javascript:" not in response.text


def test_preview_stores_nothing(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    client.post("/preview", data={"body": "never-kept-anywhere"})
    page = client.get(f"/topics/{topic_id}")
    assert "never-kept-anywhere" not in page.text
    export = client.get("/export.md")
    assert "never-kept-anywhere" not in export.text


# --- theme ---------------------------------------------------------------------


def test_theme_loads_before_paint_and_can_be_switched(client: TestClient):
    door = client.get("/login")
    assert '<script src="/static/theme.js"></script>' in door.text
    assert "data-theme-toggle" in door.text
    assert '<meta name="color-scheme" content="light dark">' in door.text
    _login(client, "chris", "pass1")
    home = client.get("/")
    assert '<script src="/static/theme.js"></script>' in home.text
    assert home.text.count("data-theme-toggle") == 1
    assert '<script src="/static/desk.js" defer></script>' in home.text
    assert "<script>" not in home.text
    for path in ("/static/theme.js", "/static/desk.js", "/static/room.js", "/static/chat.js"):
        assert client.get(path).status_code == 200


def test_stylesheet_is_fully_tokenised():
    """Colours live in the token block at the top; every rule below it draws from tokens,
    so the paper theme cannot inherit a stray dark hex. Print styles are the one exception."""
    css = Path("app/static/app.css").read_text(encoding="utf-8").split("@media print")[0]
    rules = css.split(':root[data-theme="dark"]', 1)[1]
    stray = [line.strip() for line in rules.splitlines() if HEX_COLOUR.search(line) and "url(" not in line]
    assert stray == [], stray
    assert "light-dark(" in css
    assert ':root[data-theme="light"] { color-scheme: light; }' in css


def test_narrow_screen_rules_come_last():
    """The narrow-screen block adjusts rules of equal specificity, so it only works if it is
    the later of the two. It once sat near the top and silently lost to .topic-layout."""
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    narrow = css.index("@media (max-width: 880px)")
    for selector in (".topic-layout {", ".chat-panel {", ".chat-log {", "button, .btn {", ".writing:target {"):
        assert css.index(selector) < narrow, f"{selector} is defined after the narrow-screen block"
    assert "grid-template-columns: 1fr" in css[narrow:]


# --- the page ------------------------------------------------------------------


def test_topic_page_marks_the_writing_surface(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    page = client.get(f"/topics/{topic_id}").text
    assert 'data-keep="writing-new" data-writing' in page
    assert 'data-keep="note-topic"' in page
    assert 'data-keep="topic-edit"' in page
    assert 'data-user="chris"' in page


def test_writings_carry_their_reading_time_and_exact_time(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    _writing(client, topic_id, " ".join(["word"] * 450), title="Long")
    page = client.get(f"/topics/{topic_id}").text
    assert '<span class="reading-time">2 min read</span>' in page
    assert "<time datetime=" in page
    kept = client.get("/archive").text
    assert '<span class="reading-time">2 min read</span>' in kept


def test_contents_list_appears_with_several_writings(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    first = _writing(client, topic_id, "one", title="First page")
    page = client.get(f"/topics/{topic_id}").text
    assert 'aria-label="Writings in this topic"' not in page
    _writing(client, topic_id, "two", title="Second page")
    page = client.get(f"/topics/{topic_id}").text
    assert 'aria-label="Writings in this topic"' not in page
    _writing(client, topic_id, "three")
    page = client.get(f"/topics/{topic_id}").text
    assert 'aria-label="Writings in this topic"' in page
    contents = _contents(page)
    assert f'<a href="#writing-{first}">' in contents
    assert "First page" in contents
    assert "Untitled writing" in contents


def test_contents_list_shows_only_titles_of_sealed_pages():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    opened = [
        _writing(a, topic_id, "open-body-one", title="Opened one"),
        _writing(a, topic_id, "open-body-two", title="Opened two"),
    ]
    sealed = _writing(a, topic_id, "secret-body", title="Sealed title")
    private = _writing(a, topic_id, "private-body", title="Private title")
    for wid in (*opened, sealed):
        a.post(f"/writings/{wid}/offer", follow_redirects=False)
    for wid in opened:
        b.post(f"/writings/{wid}/accept", follow_redirects=False)
    page = b.get(f"/topics/{topic_id}").text
    contents = _contents(page)
    assert "Opened one" in contents
    assert "Sealed title" in contents
    assert "· sealed" in contents
    assert "Private title" not in page
    assert "secret-body" not in page
    assert "private-body" not in page
    assert f"#writing-{private}" not in page


def test_margin_lines_from_the_same_person_group_together_when_close_in_time(db_session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    topic = db_session.get(Topic, topic_id)
    now = datetime.now(UTC)
    db_session.add(
        ChatMessage(topic_id=topic.id, author="chris", body="first-line", created_at=now - timedelta(hours=2))
    )
    db_session.add(
        ChatMessage(
            topic_id=topic.id,
            author="chris",
            body="second-line",
            created_at=now - timedelta(hours=2) + timedelta(seconds=30),
        )
    )
    db_session.add(
        ChatMessage(topic_id=topic.id, author="chris", body="an-hour-later", created_at=now - timedelta(hours=1))
    )
    db_session.add(ChatMessage(topic_id=topic.id, author="friend", body="third-line", created_at=now))
    db_session.commit()
    page = a.get(f"/topics/{topic_id}").text
    bubbles = [chunk.split(">", 1)[0] for chunk in page.split('<div class="bubble')[1:]]
    assert len(bubbles) == 4
    assert "cont" not in bubbles[0]
    assert " cont" in bubbles[1]
    assert "cont" not in bubbles[2], "an hour's gap deserves its own header"
    assert "cont" not in bubbles[3]
    assert 'data-author="chris"' in bubbles[0]
    assert 'data-at="' in bubbles[0]
    assert 'id="chat-new"' in page
    assert '<div class="chat-log-wrap">' in page
