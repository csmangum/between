from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app import auth, config, passwords
from app.markdown_render import render_markdown
from app.models import Comment, Topic, Writing
from app.store import data_root

from .conftest import CHRIS_PASSWORD, FRIEND_PASSWORD, make_client


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _create_topic(client: TestClient, title: str = "Letters") -> int:
    created = client.post("/topics", data={"title": title, "prompt": ""}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _shared_topic_with_shared_writing(client: TestClient, db_session: Session) -> tuple[int, int]:
    _login(client, "chris", CHRIS_PASSWORD)
    topic_id = _create_topic(client)
    client.post(f"/topics/{topic_id}/writings", data={"title": "First", "body": "opened words"}, follow_redirects=False)
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


# --- passwords and configuration -------------------------------------------------------------


def test_password_hash_roundtrip():
    stored = passwords.hash_password("a long enough passphrase", iterations=1000)
    assert passwords.is_hash(stored)
    assert passwords.verify_password("a long enough passphrase", stored)
    assert not passwords.verify_password("a long enough passphrasE", stored)
    assert not passwords.verify_password("anything", "pbkdf2_sha256$notanumber$00$00")
    assert not passwords.verify_password("anything", "garbage")


def test_plaintext_compare_is_length_independent():
    assert passwords.compare_plaintext("same-secret-value", "same-secret-value")
    assert not passwords.compare_plaintext("same-secret-value", "same-secret-valu")
    assert not passwords.compare_plaintext("short", "a much longer guess")


def test_hashed_person_verifies(monkeypatch):
    monkeypatch.setenv("USER1_NAME", "chris")
    monkeypatch.setenv("USER1_PASSWORD_HASH", passwords.hash_password("hashed-passphrase-123", iterations=1000))
    person = auth._person("USER1")
    assert person.hashed
    assert person.check("hashed-passphrase-123")
    assert not person.check("hashed-passphrase-124")


@pytest.mark.parametrize(
    "env",
    [
        {"USER1_PASSWORD": "change-me"},
        {"USER1_PASSWORD": ""},
        {"USER1_PASSWORD": "short"},
        {"USER1_PASSWORD_HASH": "md5$whatever"},
        {"USER1_NAME": "../escape", "USER1_PASSWORD": "long-enough-passphrase"},
        {"USER1_NAME": "Has Spaces", "USER1_PASSWORD": "long-enough-passphrase"},
    ],
)
def test_person_rejects_unsafe_configuration(monkeypatch, env):
    for key in ("USER1_NAME", "USER1_PASSWORD", "USER1_PASSWORD_HASH"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(config.ConfigError):
        auth._person("USER1")


@pytest.mark.parametrize("secret", ["", "dev-only-change-me", "change-me-to-a-long-random-string", "too-short"])
def test_settings_reject_weak_secret_key(monkeypatch, secret):
    monkeypatch.setenv("SECRET_KEY", secret)
    with pytest.raises(config.ConfigError):
        config.load_settings()


# --- login ------------------------------------------------------------------------------------


def test_login_throttles_repeated_failures(client: TestClient):
    for _ in range(5):
        response = client.post("/login", data={"username": "chris", "password": "wrong-guess"})
        assert response.status_code == 401
    blocked = client.post("/login", data={"username": "chris", "password": CHRIS_PASSWORD}, follow_redirects=False)
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text


def test_login_success_sets_hardened_cookie(client: TestClient):
    response = client.post(
        "/login", data={"username": "chris", "password": CHRIS_PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "max-age=" in cookie


# --- cross-site protections -------------------------------------------------------------------


def test_post_without_origin_is_refused():
    with TestClient(make_client().app) as bare:
        response = bare.post("/login", data={"username": "chris", "password": CHRIS_PASSWORD})
    assert response.status_code == 403


def test_post_from_foreign_origin_is_refused(client: TestClient):
    _login(client, "chris", CHRIS_PASSWORD)
    response = client.post(
        "/topics",
        data={"title": "Injected", "prompt": ""},
        headers={"origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    home = client.get("/")
    assert "Injected" not in home.text


def test_post_with_same_site_referer_is_accepted():
    with TestClient(make_client().app, headers={"referer": "http://testserver/login"}) as by_referer:
        response = by_referer.post(
            "/login", data={"username": "chris", "password": CHRIS_PASSWORD}, follow_redirects=False
        )
    assert response.status_code == 303


def test_websocket_from_foreign_origin_is_refused(client: TestClient, db_session: Session):
    topic_id, _ = _shared_topic_with_shared_writing(client, db_session)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/topics/{topic_id}", headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403


def test_websocket_ignores_malformed_frames(client: TestClient, db_session: Session):
    topic_id, _ = _shared_topic_with_shared_writing(client, db_session)
    with client.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        assert ws.receive_json()["type"] == "presence"
        ws.send_text("this is not json")
        ws.send_json(["not", "a", "dict"])
        ws.send_json({"type": "chat", "body": {"nested": "object"}})
        ws.send_json({"type": "chat", "body": "still listening"})
        message = ws.receive_json()
        assert message["type"] == "chat"
        assert message["body"] == "still listening"


# --- response headers -------------------------------------------------------------------------


def test_pages_carry_security_headers(client: TestClient):
    response = client.get("/login")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


def test_static_files_stay_cacheable(client: TestClient):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "max-age" in response.headers["cache-control"]
    assert "no-store" not in response.headers["cache-control"]


def test_no_third_party_or_inline_script_on_pages(client: TestClient, db_session: Session):
    topic_id, _ = _shared_topic_with_shared_writing(client, db_session)
    for path in ("/login", "/", f"/topics/{topic_id}"):
        page = client.get(path)
        assert page.status_code == 200
        assert "googleapis" not in page.text
        assert "gstatic" not in page.text
        assert "<script>" not in page.text
    topic = client.get(f"/topics/{topic_id}")
    assert 'data-topic-id="' in topic.text


def test_api_schema_endpoints_are_off(client: TestClient):
    _login(client, "chris", CHRIS_PASSWORD)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_oversized_request_body_is_refused(client: TestClient):
    _login(client, "chris", CHRIS_PASSWORD)
    huge = "x" * (3 * 1024 * 1024)
    response = client.post("/topics", data={"title": "Big", "prompt": huge}, follow_redirects=False)
    assert response.status_code == 413


# --- access control ---------------------------------------------------------------------------


def test_private_writing_ids_do_not_leak_topic_ids(client: TestClient, db_session: Session):
    _login(client, "chris", CHRIS_PASSWORD)
    topic_id = _create_topic(client, "Private desk")
    client.post(f"/topics/{topic_id}/writings", data={"title": "Draft", "body": "secret"}, follow_redirects=False)
    client.post(f"/topics/{topic_id}/comments", data={"body": "a private note"}, follow_redirects=False)
    writing_id = db_session.query(Writing).one().id
    comment_id = db_session.query(Comment).one().id
    client.post("/logout", follow_redirects=False)

    _login(client, "friend", FRIEND_PASSWORD)
    for path in ("offer", "accept", "decline", "revoke", "revision/offer", "revision/accept", "revision/discard"):
        response = client.post(f"/writings/{writing_id}/{path}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/", path
    for path in ("offer", "accept", "decline", "revoke"):
        response = client.post(f"/comments/{comment_id}/{path}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/", path


def test_comment_parent_must_be_readable_in_same_topic(client: TestClient, db_session: Session):
    _login(client, "chris", CHRIS_PASSWORD)
    first = _create_topic(client, "One")
    second = _create_topic(client, "Two")
    client.post(f"/topics/{second}/comments", data={"body": "elsewhere"}, follow_redirects=False)
    foreign_parent = db_session.query(Comment).one().id

    response = client.post(
        f"/topics/{first}/comments",
        data={"body": "reply", "parent_id": foreign_parent},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/topics/{first}"
    missing = client.post(
        f"/topics/{first}/comments",
        data={"body": "reply", "parent_id": 9999},
        follow_redirects=False,
    )
    assert missing.status_code == 303
    assert db_session.query(Comment).count() == 1


# --- storage ----------------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_mirrors_are_owner_only(client: TestClient, db_session: Session):
    _login(client, "chris", CHRIS_PASSWORD)
    topic_id = _create_topic(client, "Perms")
    client.post(f"/topics/{topic_id}/writings", data={"title": "Draft", "body": "secret"}, follow_redirects=False)
    root = data_root()
    for folder in (root / "local", root / "local" / "chris"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    files = list((root / "local" / "chris").rglob("*.md"))
    assert files
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "between.db").stat().st_mode) == 0o600


def test_revoking_a_writing_removes_its_shared_mirror(client: TestClient, db_session: Session):
    topic_id, writing_id = _shared_topic_with_shared_writing(client, db_session)
    shared_files = list((data_root() / "shared").rglob(f"writing-{writing_id}.md"))
    assert shared_files
    client.post(f"/writings/{writing_id}/revoke", follow_redirects=False)
    assert not shared_files[0].exists()
    assert list((data_root() / "local" / "chris").rglob(f"writing-{writing_id}.md"))


def test_revoking_a_topic_removes_and_reopening_restores_shared_mirror(client: TestClient, db_session: Session):
    topic_id, writing_id = _shared_topic_with_shared_writing(client, db_session)
    topic = db_session.get(Topic, topic_id)
    shared_dir = next(p for p in (data_root() / "shared").iterdir() if p.name.startswith(f"{topic.id:04d}-"))
    assert (shared_dir / "topic.md").exists()
    assert (shared_dir / f"writing-{writing_id}.md").exists()

    client.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    assert not shared_dir.exists()

    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", FRIEND_PASSWORD)
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    assert (shared_dir / "topic.md").exists()
    assert (shared_dir / f"writing-{writing_id}.md").exists()


# --- rendering --------------------------------------------------------------------------------


def test_markdown_strips_author_link_attributes_and_bad_protocols():
    html = render_markdown('<a href="https://example.com" target="_self" rel="opener">out</a>')
    assert 'target="_self"' not in html
    assert 'rel="opener"' not in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html

    scripted = render_markdown("[click](javascript:alert(1))")
    assert "javascript:" not in scripted
    raw = render_markdown("<script>alert(1)</script> <img src=x onerror=alert(1)>")
    assert "<script>" not in raw
    assert "<img" not in raw
