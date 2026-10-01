from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app import agent, auth, config
from app.db import SessionLocal
from app.hub import hub
from app.main import app
from app.markdown_render import render_markdown
from app.models import ChatMessage, Comment, Topic, Writing
from app.store import data_root


def _login(client: TestClient, username: str, password: str):
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303
    return response


def _topic(client: TestClient, title: str = "Letters", prompt: str = "") -> int:
    created = client.post("/topics", data={"title": title, "prompt": prompt}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _writing(client: TestClient, topic_id: int, body: str, title: str = "w") -> int:
    r = client.post(f"/topics/{topic_id}/writings", data={"title": title, "body": body}, follow_redirects=False)
    return int(r.headers["location"].rsplit("-", 1)[-1])


def _pair():
    a = TestClient(app)
    b = TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _shared_topic(a: TestClient, b: TestClient, **kwargs) -> int:
    topic_id = _topic(a, **kwargs)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


# --- headers, cookies, origin -------------------------------------------------


def test_security_headers_and_no_store(client: TestClient):
    _login(client, "chris", "pass1")
    home = client.get("/")
    csp = home.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self' ws://testserver wss://testserver" in csp
    assert home.headers["cache-control"] == "no-store"
    assert home.headers["x-content-type-options"] == "nosniff"
    assert home.headers["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in home.headers  # HTTPS_ONLY is off in tests
    static = client.get("/static/app.css")
    assert static.headers["cache-control"].startswith("public")
    export = client.get("/export.json")
    assert export.headers["cache-control"] == "no-store"


def test_session_cookie_is_strict_and_httponly(client: TestClient):
    response = _login(client, "chris", "pass1")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert f"max-age={config.SESSION_IDLE_SECONDS}" in cookie


def test_no_inline_script_on_topic_page(client: TestClient):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    page = a.get(f"/topics/{topic_id}").text
    assert "<script>" not in page
    assert 'data-topic-id="' in page


def test_cross_site_post_is_refused(client: TestClient):
    _login(client, "chris", "pass1")
    foreign = client.post(
        "/topics", data={"title": "x"}, headers={"origin": "https://evil.example"}, follow_redirects=False
    )
    assert foreign.status_code == 403
    null_origin = client.post("/topics", data={"title": "x"}, headers={"origin": "null"}, follow_redirects=False)
    assert null_origin.status_code == 403
    wrong_scheme = client.post(
        "/topics", data={"title": "x"}, headers={"origin": "https://testserver"}, follow_redirects=False
    )
    assert wrong_scheme.status_code == 403
    same = client.post("/topics", data={"title": "x"}, headers={"origin": "http://testserver"}, follow_redirects=False)
    assert same.status_code == 303


def test_cross_site_websocket_is_refused():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        a.websocket_connect(f"/ws/topics/{topic_id}", headers={"origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4403


def test_websocket_origin_must_match_request_scheme():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        a.websocket_connect(f"/ws/topics/{topic_id}", headers={"origin": "https://testserver"}) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4403


# --- login ---------------------------------------------------------------------


def test_login_throttles_after_repeated_failures(client: TestClient):
    codes = [
        client.post("/login", data={"username": "chris", "password": "bad"}, follow_redirects=False).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401]
    assert 429 in codes[3:]
    blocked = client.post("/login", data={"username": "chris", "password": "pass1"}, follow_redirects=False)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert "Too many tries" in blocked.text


def test_unknown_name_and_wrong_password_look_the_same(client: TestClient):
    a = client.post("/login", data={"username": "nobody", "password": "x"}, follow_redirects=False)
    b = client.post("/login", data={"username": "chris", "password": "x"}, follow_redirects=False)
    assert a.status_code == b.status_code == 401
    assert "did not match" in a.text and "did not match" in b.text


def test_password_hash_roundtrip():
    encoded = auth.hash_password("correct horse")
    assert encoded.startswith("scrypt$")
    assert auth.check_password("correct horse", encoded)
    assert not auth.check_password("correct horsf", encoded)
    assert not auth.check_password("anything", "not-a-hash")


def test_password_hash_cli_does_not_require_secret_key():
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)
    env.pop("BETWEEN_DEV", None)
    result = subprocess.run(
        [sys.executable, "-m", "app.auth"],
        input="correct horse battery staple\n",
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.startswith("scrypt$")


def test_duplicate_usernames_are_refused_in_subprocess():
    env = os.environ.copy()
    env["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-subprocess"
    env["USER1_NAME"] = env["USER2_NAME"] = "same-person"
    env["USER1_PASSWORD"] = "password-one"
    env["USER2_PASSWORD"] = "password-two"
    env.pop("BETWEEN_DEV", None)
    env.pop("USER1_PASSWORD_HASH", None)
    env.pop("USER2_PASSWORD_HASH", None)
    result = subprocess.run(
        [sys.executable, "-c", "from app.auth import load_people; load_people()"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "Between refused to start: USER1_NAME and USER2_NAME must be different people." in result.stderr
    assert "NameError" not in result.stderr


def test_session_validity_rules():
    person = auth.load_people()["chris"]
    session: dict = {}
    auth.start_session(session, person)
    assert auth.session_user(session) == "chris"

    stale = dict(session, iat=int(time.time()) - config.SESSION_ABSOLUTE_SECONDS - 1)
    assert auth.session_user(stale) is None and stale == {}

    rotated = dict(session, fp="0" * 24)  # password changed since this cookie was issued
    assert auth.session_user(rotated) is None and rotated == {}

    stranger = dict(session, user="ghost")
    assert auth.session_user(stranger) is None


def test_startup_refuses_sample_secret(monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    with pytest.raises(SystemExit):
        config._secret_key()
    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(SystemExit):
        config._secret_key()
    monkeypatch.setattr(config, "DEV", True)
    assert len(config._secret_key()) >= 32


# --- the counterpart's words survive a revoke -------------------------------------


def test_topic_revoke_returns_everything_to_its_author(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b, title="Together", prompt="creator-only-opening")
    a_wid = _writing(a, topic_id, "a-shared-body")
    b_wid = _writing(b, topic_id, "b-own-body")
    for who, wid in ((a, a_wid), (b, b_wid)):
        who.post(f"/writings/{wid}/offer", follow_redirects=False)
    a.post(f"/writings/{b_wid}/accept", follow_redirects=False)
    b.post(f"/writings/{a_wid}/accept", follow_redirects=False)
    shared = data_root() / "shared"
    assert list(shared.rglob(f"writing-{b_wid}.md"))

    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)

    # A no longer reads B's page; B no longer reads A's page.
    a_view = a.get(f"/topics/{topic_id}").text
    assert "b-own-body" not in a_view and "a-shared-body" in a_view
    b_page = b.get(f"/topics/{topic_id}", follow_redirects=False)
    assert b_page.status_code == 200
    assert "b-own-body" in b_page.text and "a-shared-body" not in b_page.text
    assert "creator-only-opening" not in b_page.text
    assert "pulled this topic back" in b_page.text

    # B's desk, archive and exports keep their own pages but not the creator's prompt.
    desk = b.get("/")
    assert "Together" in desk.text and "creator-only-opening" not in desk.text
    archive = b.get("/archive")
    assert "b-own-body" in archive.text and "creator-only-opening" not in archive.text
    exported = b.get("/export.json").json()
    assert any(w["id"] == b_wid for t in exported["topics"] for w in t["writings"])
    exported_topic = next(t for t in exported["topics"] if t["id"] == topic_id)
    assert exported_topic["prompt"] == ""
    assert "creator-only-opening" not in b.get("/export.md").text
    assert "creator-only-opening" not in agent.archive_excerpt(db_session.get(Topic, topic_id), "friend")
    assert "creator-only-opening" in a.get(f"/topics/{topic_id}").text
    assert not list(shared.rglob(f"{topic_id:04d}-*"))
    db_session.expire_all()
    assert {w.share_status for w in db_session.query(Writing).all()} == {"private"}


def test_creator_cannot_delete_a_topic_holding_the_other_persons_words(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    b_wid = _writing(b, topic_id, "keep-me")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    response = a.post(f"/topics/{topic_id}/delete", follow_redirects=False)
    assert response.headers["location"] == f"/topics/{topic_id}"
    db_session.expire_all()
    assert db_session.get(Writing, b_wid) is not None
    assert list((data_root() / "local" / "friend").rglob(f"writing-{b_wid}.md"))


def test_reoffer_after_revoke_shows_offer_band_not_consent_wall(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    _writing(b, topic_id, "b-page")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    page = b.get(f"/topics/{topic_id}").text
    assert "sent this topic again" in page
    assert "b-page" in page
    assert "Waiting for you" in b.get("/").text


def test_writing_revoke_removes_shared_copy(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "revoked-body")
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    shared = data_root() / "shared"
    assert list(shared.rglob(f"writing-{wid}.md"))
    folder = next(shared.glob(f"{topic_id:04d}-*"))
    unrelated_stable = folder / f"writing-{wid}0.md"
    unrelated_legacy = folder / f"writing-{wid}0-older.md"
    unrelated_stable.write_text("unrelated", encoding="utf-8")
    unrelated_legacy.write_text("unrelated legacy", encoding="utf-8")
    a.post(f"/writings/{wid}/revoke", follow_redirects=False)
    assert not list(shared.rglob(f"writing-{wid}.md"))
    assert unrelated_stable.exists() and unrelated_legacy.exists()
    assert list((data_root() / "local" / "chris").rglob(f"writing-{wid}.md"))


def test_comment_parent_must_exist_in_the_same_thread(db_session: Session):
    a, _ = _pair()
    t1 = _topic(a, "one")
    t2 = _topic(a, "two")
    a.post(f"/topics/{t2}/comments", data={"body": "root"}, follow_redirects=False)
    root = db_session.query(Comment).one()
    missing = a.post(f"/topics/{t1}/comments", data={"body": "x", "parent_id": "999999"}, follow_redirects=False)
    assert missing.status_code == 303
    cross = a.post(f"/topics/{t1}/comments", data={"body": "x", "parent_id": str(root.id)}, follow_redirects=False)
    assert cross.status_code == 303
    ok = a.post(f"/topics/{t2}/comments", data={"body": "reply", "parent_id": str(root.id)}, follow_redirects=False)
    assert ok.status_code == 303
    db_session.expire_all()
    assert db_session.query(Comment).count() == 2


# --- the live margin ----------------------------------------------------------------


def test_websocket_seat_is_released_on_bad_frames():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with pytest.raises(WebSocketDisconnect) as exc, a.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.receive_json()
    assert exc.value.code == 1003
    assert hub.rooms.get(topic_id) in (None, {})


def test_revoke_closes_the_margin_for_everyone_at_once():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        assert ws.receive_json()["type"] == "presence"
        a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4404
    assert topic_id not in hub.rooms


def test_websocket_seat_limit():
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    open_sockets = []
    try:
        for _ in range(4):
            ws = a.websocket_connect(f"/ws/topics/{topic_id}")
            ws.__enter__()
            ws.receive_json()
            open_sockets.append(ws)
        with pytest.raises(WebSocketDisconnect) as exc, a.websocket_connect(f"/ws/topics/{topic_id}") as extra:
            extra.receive_json()
        assert exc.value.code == 4429
    finally:
        for ws in open_sockets:
            ws.__exit__(None, None, None)


def test_revoke_during_websocket_join_is_rechecked(monkeypatch):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    original_join = hub.join

    async def revoke_before_seat(topic_id, websocket, user):
        with SessionLocal() as db:
            topic = db.get(Topic, topic_id)
            topic.share_status = "private"
            db.commit()
        await hub.close_room(topic_id)
        return await original_join(topic_id, websocket, user)

    monkeypatch.setattr(hub, "join", revoke_before_seat)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        assert ws.receive_json()["type"] == "presence"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4404
    assert topic_id not in hub.rooms


def test_typing_frames_consume_the_websocket_flood_limit(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        assert ws.receive_json()["type"] == "presence"
        for _ in range(10):
            ws.send_json({"type": "typing", "on": True})
            assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "typing", "on": False})
        ws.send_json({"type": "chat", "body": "must be rate limited"})
    db_session.expire_all()
    assert db_session.query(ChatMessage).count() == 0


# --- drafting help needs both people -------------------------------------------------


def test_drafting_needs_both_consents(db_session: Session, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "test-key")
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    assert agent.allowed(db_session) is False
    blocked = a.post(f"/topics/{topic_id}/draft", follow_redirects=False)
    assert "agent=consent" in blocked.headers["location"]
    assert "Drafting help" in a.get("/").text
    assert "both of you allow it" in a.get(f"/topics/{topic_id}").text

    a.post("/me/agent", data={"allow": "1"}, follow_redirects=False)
    db_session.expire_all()
    assert agent.allowed(db_session) is False
    b.post("/me/agent", data={"allow": "1"}, follow_redirects=False)
    db_session.expire_all()
    assert agent.allowed(db_session) is True
    assert "Draft a reply" in a.get(f"/topics/{topic_id}").text

    b.post("/me/agent", data={"allow": "0"}, follow_redirects=False)
    db_session.expire_all()
    assert agent.allowed(db_session) is False


@pytest.mark.parametrize("content", [None, {}, [], 42, "", "  "])
def test_drafting_rejects_non_string_or_empty_provider_content(monkeypatch, content):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return Response()

    topic = SimpleNamespace(
        share_status="shared",
        title="Letters",
        prompt="",
        writings=[],
        comments=[],
        messages=[],
    )
    monkeypatch.setattr(agent, "_settings", lambda: ("test-key", "https://api.example", "test-model"))
    monkeypatch.setattr(agent.httpx, "Client", lambda **kwargs: Client())

    with pytest.raises(RuntimeError, match="empty-reply"):
        agent.draft_reply(topic, "chris")


def test_drafting_ui_absent_without_a_key(client: TestClient, monkeypatch):
    for name in ("AGENT_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    _login(client, "chris", "pass1")
    assert "Drafting help" not in client.get("/").text


def test_drafting_excerpt_contains_only_shared_content():
    topic = SimpleNamespace(
        id=1,
        title="Letters",
        prompt="shared prompt",
        share_status="shared",
        writings=[
            SimpleNamespace(
                id=1,
                author="chris",
                share_status="shared",
                title="Shared page",
                body="shared writing",
                revision_status="private",
                revision_body="private revision",
            ),
            SimpleNamespace(
                id=2,
                author="chris",
                share_status="private",
                title="Private page",
                body="private writing",
            ),
        ],
        comments=[
            SimpleNamespace(writing_id=1, share_status="shared", author="friend", body="shared comment"),
            SimpleNamespace(writing_id=1, share_status="private", author="chris", body="private comment"),
            SimpleNamespace(writing_id=None, share_status="private", author="chris", body="private loose comment"),
        ],
        messages=[SimpleNamespace(author="friend", body="shared chat")],
    )
    excerpt = agent.archive_excerpt(topic, "chris")

    assert "shared prompt" in excerpt
    assert "shared writing" in excerpt
    assert "shared comment" in excerpt
    assert "shared chat" in excerpt
    assert "private writing" not in excerpt
    assert "private comment" not in excerpt
    assert "private loose comment" not in excerpt
    assert "private revision" not in excerpt
    topic.share_status = "private"
    assert agent.archive_excerpt(topic, "chris") == ""


def test_drafting_control_is_hidden_for_private_topics(client: TestClient, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "test-key")
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Private", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    page = client.get(f"/topics/{topic_id}").text
    assert "Draft a reply" not in page
    assert "Drafting help is off" not in page


# --- markdown ---------------------------------------------------------------------------


def test_author_supplied_rel_is_replaced():
    html = render_markdown('<a href="https://e.com" rel="opener" onclick="x()">y</a>')
    assert 'rel="noopener noreferrer"' in html
    assert 'opener"' not in html.replace('rel="noopener noreferrer"', "")
    assert "onclick" not in html


def test_dangerous_markup_is_removed():
    assert "javascript:" not in render_markdown("[x](javascript:alert(1))")
    assert "<script" not in render_markdown("<script>alert(1)</script>")
    assert "<iframe" not in render_markdown("<iframe src=https://x></iframe>")
    assert "onerror" not in render_markdown('<img src=x onerror=alert(1) alt="words">')
    assert "words" in render_markdown('<img src=x onerror=alert(1) alt="words">')


# --- what the other person sees before opening ------------------------------------------


def test_private_topic_stays_invisible_without_contribution(db_session: Session):
    a, b = _pair()
    topic_id = _topic(a, "Only mine", "hidden prompt")
    assert b.get(f"/topics/{topic_id}", follow_redirects=False).status_code == 303
    assert "Only mine" not in b.get("/").text
    db_session.expire_all()
    assert db_session.get(Topic, topic_id).share_status == "private"
