"""Notes and margin lines render as Markdown; every timestamp is a <time> the browser can localize."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import ChatMessage
from app.views import iso_utc, when, when_soft

TIME_TAG = re.compile(r'<time datetime="(?P<iso>[^"]+)" data-when="(?P<mode>soft|exact)">(?P<text>[^<]*)</time>')


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


def _shared_topic(a: TestClient, b: TestClient) -> int:
    topic_id = _topic(a)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


# --- Markdown in notes and the margin --------------------------------------------------------


def test_notes_render_markdown_and_are_sanitized(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    body = "A **bold** aside <script>alert(1)</script>\n\n- one\n- two"
    client.post(f"/topics/{topic_id}/comments", data={"body": body}, follow_redirects=False)

    page = client.get(f"/topics/{topic_id}").text
    assert "<strong>bold</strong>" in page
    assert "<li>one</li>" in page
    assert "<script>" not in page
    assert 'class="body prose compact"' in page

    archive = client.get("/archive").text
    assert "<strong>bold</strong>" in archive
    assert "<script>" not in archive


def test_margin_history_renders_markdown(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    db_session.add(ChatMessage(topic_id=topic_id, author="chris", body="*quietly* said"))
    db_session.commit()

    for client in (a, b):
        page = client.get(f"/topics/{topic_id}").text
        assert "<em>quietly</em> said" in page
    archive = b.get("/archive").text
    assert "<em>quietly</em> said" in archive


def test_margin_frames_carry_rendered_html_and_an_instant():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "chat", "body": "a `code` word"})
        frame = ws.receive_json()
        while frame["type"] != "chat":
            frame = ws.receive_json()

    assert frame["body"] == "a `code` word"
    assert frame["html"] == "<p>a <code>code</code> word</p>"
    stamped = datetime.fromisoformat(frame["created_at"])
    assert stamped.tzinfo is not None
    assert abs((datetime.now(UTC) - stamped).total_seconds()) < 60


# --- Timestamps the browser can localize ------------------------------------------------------


def test_time_filters_emit_an_instant_with_the_server_text_as_fallback():
    naive = datetime(2026, 3, 4, 5, 6, 7)  # what SQLite hands back: naive, but stored as UTC
    tag = str(when_soft(naive))
    match = TIME_TAG.fullmatch(tag)
    assert match and match["mode"] == "soft"
    assert match["iso"] == "2026-03-04T05:06:07+00:00"
    assert match["text"]  # the server's own wording stays for browsers without scripts

    exact = TIME_TAG.fullmatch(str(when(naive)))
    assert exact and exact["mode"] == "exact"
    assert when_soft(None) == "" and when(None) == ""
    assert iso_utc(datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)) == "2026-03-04T05:06:07+00:00"


def test_pages_render_every_timestamp_as_a_time_element(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    a.post(f"/topics/{topic_id}/writings", data={"title": "w", "body": "words"}, follow_redirects=False)
    db_session.add(ChatMessage(topic_id=topic_id, author="chris", body="hello"))
    db_session.commit()

    for path in ("/", f"/topics/{topic_id}", "/table", "/archive"):
        page = a.get(path).text
        tags = TIME_TAG.findall(page)
        assert tags, path
        for iso, _mode, text in tags:
            assert datetime.fromisoformat(iso).tzinfo is not None, path
            assert text.strip(), path
    consent = b.get(f"/topics/{_offered_topic(a)}").text
    assert TIME_TAG.search(consent)


def _offered_topic(a: TestClient) -> int:
    topic_id = _topic(a, title="Sealed")
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    return topic_id


def test_exports_say_utc_instead_of_server_local_time(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    client.post(f"/topics/{topic_id}/writings", data={"title": "w", "body": "words"}, follow_redirects=False)
    client.post(f"/topics/{topic_id}/comments", data={"body": "a note"}, follow_redirects=False)

    markdown = client.get("/export.md").text
    assert "UTC_" in markdown.splitlines()[2]  # _Exported … UTC_
    assert re.search(r"\*Chris · \w{3} \d{2}, \d{4} · \d{2}:\d{2} UTC\*", markdown)
    assert re.search(r"\(\w{3} \d{2}, \d{4} · \d{2}:\d{2} UTC\): a note", markdown)

    payload = json.loads(client.get("/export.json").text)
    assert payload["exported_at"].endswith("+00:00")
    topic = payload["topics"][0]
    assert topic["created_at"].endswith("+00:00")
    assert topic["writings"][0]["created_at"].endswith("+00:00")
    assert topic["comments"][0]["created_at"].endswith("+00:00")


# --- The desk remembers ----------------------------------------------------------------------


def test_room_script_ships_drafts_and_local_times(client: TestClient):
    _login(client, "chris", "pass1")
    assert 'data-user="chris"' in client.get("/").text
    script = client.get("/static/room.js").text
    assert "between:draft:v2:" in script
    assert "between:draft:v1:" in script
    assert "localStorage" in script
    assert 'querySelectorAll("time[datetime]")' in script
    chat = client.get("/static/chat.js").text
    assert "Between.timeElement" in chat
    assert "msg.html" in chat


# --- The writing surface ---------------------------------------------------------------------

EDITOR_TAG = re.compile(r'<textarea id="(?P<id>[^"]+)"[^>]*data-editor="(?P<kind>[^"]+)"')


def test_writing_fields_ask_for_the_desk_editor(client: TestClient):
    _login(client, "chris", "pass1")
    topic_id = _topic(client)
    client.post(f"/topics/{topic_id}/writings", data={"title": "", "body": "A page."}, follow_redirects=False)

    page = client.get(f"/topics/{topic_id}").text
    kinds = {m["id"]: m["kind"] for m in EDITOR_TAG.finditer(page)}
    writing_ids = {k for k, v in kinds.items() if v == "writing"}
    note_ids = {k for k, v in kinds.items() if v == "note"}
    assert "wbody" in writing_ids
    assert {k for k in writing_ids if k.startswith("edit-body-")}, kinds
    assert {"topic-prompt", "topic-note"} <= note_ids
    assert {k for k in note_ids if k.startswith("note-")}, kinds
    assert "chat-body" not in kinds  # the margin keeps its own small composer
    assert '<script src="/static/desk.js" defer></script>' in page

    home = client.get("/").text
    assert {m["id"]: m["kind"] for m in EDITOR_TAG.finditer(home)} == {"prompt": "note"}


def test_desk_script_previews_through_the_server_and_needs_no_third_party(client: TestClient):
    script = client.get("/static/desk.js").text
    assert 'fetch("/preview"' in script
    assert 'credentials: "same-origin"' in script
    assert 'querySelectorAll("textarea[data-editor]")' in script
    assert "https://" not in script.replace("https?:", "")  # nothing loaded from elsewhere
