from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.models import ChatMessage, Topic

from .conftest import CHRIS_PASSWORD, FRIEND_PASSWORD, make_client


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_offer_accept_flow(client: TestClient):
    _login(client, "chris", CHRIS_PASSWORD)
    created = client.post("/topics", data={"title": "Letters", "prompt": "private note"}, follow_redirects=False)
    assert created.status_code == 303
    topic_url = created.headers["location"]
    topic_id = topic_url.rsplit("/", 1)[-1]

    offered = client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    assert offered.status_code == 303

    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)

    home = client.get("/")
    assert home.status_code == 200
    assert "Waiting for you" in home.text
    assert "Letters" in home.text
    assert "private note" not in home.text

    consent = client.get(f"/topics/{topic_id}")
    assert consent.status_code == 200
    assert "Sealed for you" in consent.text
    assert "Open it" in consent.text

    accepted = client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    assert accepted.status_code == 303
    opened = client.get(f"/topics/{topic_id}")
    assert opened.status_code == 200
    assert "private note" in opened.text
    assert "kept between you" in opened.text
    assert 'label for="chat-body"' in opened.text


def test_websocket_stops_after_topic_revoke(db_session: Session):
    with make_client() as author, make_client() as reader:
        _login(author, "chris", CHRIS_PASSWORD)
        created = author.post("/topics", data={"title": "Letters", "prompt": ""}, follow_redirects=False)
        topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
        author.post(f"/topics/{topic_id}/offer", follow_redirects=False)

        _login(reader, "friend", FRIEND_PASSWORD)
        reader.post(f"/topics/{topic_id}/accept", follow_redirects=False)

        with reader.websocket_connect(f"/ws/topics/{topic_id}") as ws:
            assert ws.receive_json()["type"] == "presence"
            author.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
            ws.send_json({"type": "chat", "body": "still here?"})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4404

    assert db_session.query(ChatMessage).count() == 0
    topic = db_session.get(Topic, topic_id)
    assert topic.share_status == "private"


def test_draft_route_closes_after_topic_revoke(client: TestClient):
    _login(client, "chris", CHRIS_PASSWORD)
    created = client.post("/topics", data={"title": "Letters", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", CHRIS_PASSWORD)
    client.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    response = client.post(f"/topics/{topic_id}/draft", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
