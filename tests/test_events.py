"""Open question 6, answered: one quiet badge, kept truthful by a per-person stream. No messenger."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import events
from app.main import app
from app.routes.events import frame, frames


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


def _writing(client: TestClient, topic_id: int, body: str = "words") -> int:
    r = client.post(f"/topics/{topic_id}/writings", data={"title": "", "body": body}, follow_redirects=False)
    return int(r.headers["location"].rsplit("-", 1)[-1])


def _data_lines(body: str) -> list[dict]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


@pytest.fixture()
def told(monkeypatch) -> Iterator[list[tuple]]:
    """Capture what the stream would have published, as (recipient, what, topic, sealed)."""
    seen: list[tuple] = []

    def publish(user: str, payload: dict) -> None:
        seen.append((user, payload["what"], payload["topic"], payload["sealed"]))

    monkeypatch.setattr(events.stream, "publish", publish)
    yield seen


# --- the stream itself -----------------------------------------------------------------------


def test_stream_delivers_across_threads_and_caps_listeners():
    async def scenario() -> tuple[list, int, int]:
        s = events.Stream()
        first = s.subscribe("chris")
        publisher = threading.Thread(target=s.publish, args=("chris", {"n": 1}))
        publisher.start()
        publisher.join()
        got = [await asyncio.wait_for(first.queue.get(), 1)]
        s.publish("friend", {"n": 2})  # nobody listening: nothing happens
        others = [s.subscribe("chris") for _ in range(events.MAX_LISTENERS_PER_PERSON)]
        evicted = await asyncio.wait_for(first.queue.get(), 1)  # the oldest seat is shown out
        got.append(evicted)
        live = s.listening("chris")
        for listener in others:
            s.unsubscribe(listener)
        return got, live, s.listening("chris")

    got, live, after = asyncio.run(scenario())
    assert got == [{"n": 1}, None]
    assert live == events.MAX_LISTENERS_PER_PERSON and after == 0


def test_frames_begin_with_hello_and_end_when_closed(client: TestClient):
    async def scenario() -> list[str]:
        out: list[str] = []
        gen = frames("friend")
        out.append(await gen.__anext__())
        assert events.stream.listening("friend") == 1
        events.stream.publish("friend", {"type": "topic", "what": "opened", "topic": 7, "sealed": 0})
        out.append(await gen.__anext__())
        events.stream.close_all()
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        return out

    out = asyncio.run(scenario())
    assert out[0].startswith("retry: 3000\n")
    assert _data_lines(out[0]) == [{"type": "hello", "sealed": 0}]
    assert out[1] == frame({"type": "topic", "what": "opened", "topic": 7, "sealed": 0})
    assert events.stream.listening("friend") == 0


def test_events_requires_a_session_without_redirecting(client: TestClient):
    response = client.get("/events", follow_redirects=False)
    assert response.status_code == 401


def test_events_endpoint_streams_a_live_offer(db_session: Session):
    a, b = _pair()
    result: dict = {}

    def listen() -> None:
        # The test client buffers the whole response, so the stream is read once it has been closed.
        response = b.get("/events")
        result["status"] = response.status_code
        result["headers"] = dict(response.headers)
        result["body"] = response.text

    reader = threading.Thread(target=listen, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 5
        while events.stream.listening("friend") == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert events.stream.listening("friend") == 1

        topic_id = _topic(a, "Live")
        a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    finally:
        time.sleep(0.1)
        events.stream.close_all()
    reader.join(timeout=5)
    assert not reader.is_alive()

    assert result["status"] == 200
    assert result["headers"]["content-type"].startswith("text/event-stream")
    assert result["headers"]["cache-control"] == "no-store"
    assert result["headers"]["x-accel-buffering"] == "no"
    assert _data_lines(result["body"]) == [
        {"type": "hello", "sealed": 0},
        {"type": "topic", "what": "sealed", "topic": topic_id, "sealed": 1},
    ]


def test_pages_open_the_stream_only_for_a_signed_in_person(client: TestClient):
    assert "data-me=" not in client.get("/login").text
    _login(client, "chris", "pass1")
    home = client.get("/").text
    assert 'data-me="chris" data-other="Friend"' in home
    script = client.get("/static/room.js").text
    assert 'new EventSource("/events")' in script
    assert "chat" not in script.split("One quiet stream")[1].split("Drafts:")[0]  # the margin is not a notification


# --- what gets told, and to whom --------------------------------------------------------------


def test_every_consent_transition_tells_the_other_person(told: list[tuple]):
    a, b = _pair()
    topic_id = _topic(a)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/decline", follow_redirects=False)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    assert told == [
        ("friend", "sealed", topic_id, 1),
        ("chris", "unopened", topic_id, 0),
        ("friend", "sealed", topic_id, 1),
        ("chris", "opened", topic_id, 0),
    ]
    told.clear()

    wid = _writing(a, topic_id)
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/decline", follow_redirects=False)
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    a.post(f"/writings/{wid}/revision", data={"title": "", "body": "v2"}, follow_redirects=False)
    a.post(f"/writings/{wid}/revision/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/revision/decline", follow_redirects=False)
    a.post(f"/writings/{wid}/revision/offer", follow_redirects=False)
    a.post(f"/writings/{wid}/revision/revoke", follow_redirects=False)
    a.post(f"/writings/{wid}/revision/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/revision/accept", follow_redirects=False)
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    assert [(who, what) for who, what, _, _ in told] == [
        ("friend", "sealed"),
        ("chris", "unopened"),
        ("friend", "sealed"),
        ("chris", "opened"),
        ("friend", "sealed"),
        ("chris", "unopened"),
        ("friend", "sealed"),
        ("friend", "returned"),
        ("friend", "sealed"),
        ("chris", "opened"),
        ("friend", "returned"),
    ]
    assert {t[2] for t in told} == {topic_id}
    told.clear()

    r = a.post(f"/topics/{topic_id}/comments", data={"body": "a note"}, follow_redirects=False)
    assert r.status_code == 303
    cid = int(a.get("/export.json").json()["topics"][0]["comments"][0]["id"])
    a.post(f"/comments/{cid}/offer", follow_redirects=False)
    b.post(f"/comments/{cid}/accept", follow_redirects=False)
    a.post(f"/comments/{cid}/revoke", follow_redirects=False)
    a.post(f"/comments/{cid}/offer", follow_redirects=False)
    b.post(f"/comments/{cid}/decline", follow_redirects=False)
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    assert [(who, what) for who, what, _, _ in told] == [
        ("friend", "sealed"),
        ("chris", "opened"),
        ("friend", "returned"),
        ("friend", "sealed"),
        ("chris", "unopened"),
        ("friend", "returned"),
    ]


def test_private_acts_and_the_margin_tell_nobody(told: list[tuple]):
    a, b = _pair()
    topic_id = _topic(a)
    wid = _writing(a, topic_id)
    a.post(f"/writings/{wid}/edit", data={"title": "", "body": "changed"}, follow_redirects=False)
    a.post(f"/topics/{topic_id}/comments", data={"body": "private note"}, follow_redirects=False)
    a.post(f"/topics/{topic_id}/edit", data={"title": "Renamed", "prompt": ""}, follow_redirects=False)
    assert told == []

    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    told.clear()
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        ws.send_json({"type": "chat", "body": "in the margin"})
        ws.receive_json()
    a.post(f"/writings/{wid}/revision", data={"title": "", "body": "v2"}, follow_redirects=False)
    a.post(f"/writings/{wid}/revision/discard", follow_redirects=False)
    assert told == []
