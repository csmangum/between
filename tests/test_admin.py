from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.routes.auth import _safe_return

ADMIN_PASSWORD = "op-secret-value"


@pytest.fixture()
def admin_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
    auth._ADMIN_LOADED = False
    auth._ADMIN = None
    yield
    auth._ADMIN_LOADED = False
    auth._ADMIN = None


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_admin_login_is_absent_until_configured(client: TestClient):
    disabled = client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=False)
    unknown = client.post("/login", data={"username": "nobody", "password": "admin"}, follow_redirects=False)
    assert disabled.status_code == unknown.status_code == 401
    assert "did not match" in disabled.text and "did not match" in unknown.text

    _login(client, "chris", "pass1")
    page = client.get("/")
    assert "Acting as" not in page.text
    assert 'action="/switch"' not in page.text


def test_admin_login_acts_as_the_first_person_and_can_switch(admin_enabled, client: TestClient, caplog):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "only-chris", "prompt": ""}, follow_redirects=False)
    assert created.status_code == 303
    client.post("/logout", follow_redirects=False)

    _login(client, "admin", ADMIN_PASSWORD)
    as_chris = client.get("/")
    assert as_chris.status_code == 200
    assert "only-chris" in as_chris.text
    assert "Chris · Friend" in as_chris.text
    assert "Acting as" in as_chris.text
    assert 'value="chris"' in as_chris.text and 'aria-pressed="true"' in as_chris.text

    with caplog.at_level(logging.INFO, logger="between"):
        switched = client.post(
            "/switch",
            data={"username": "friend", "return_to": "/table"},
            follow_redirects=False,
        )
    assert switched.status_code == 303
    assert switched.headers["location"] == "/table"
    assert "admin switch from=chris to=friend ip=" in caplog.text
    as_friend = client.get("/table")
    assert "Friend · Chris" in as_friend.text
    assert 'aria-pressed="true"' in as_friend.text

    client.post("/switch", data={"username": "chris", "return_to": "/"}, follow_redirects=False)
    assert "only-chris" in client.get("/").text


def test_topic_written_while_acting_as_friend_belongs_to_friend(admin_enabled, client: TestClient):
    _login(client, "admin", ADMIN_PASSWORD)
    client.post("/switch", data={"username": "friend", "return_to": "/"}, follow_redirects=False)
    created = client.post("/topics", data={"title": "friend-desk", "prompt": "private pages"}, follow_redirects=False)
    assert created.status_code == 303
    client.post("/logout", follow_redirects=False)

    _login(client, "friend", "pass2")
    assert "friend-desk" in client.get("/").text
    client.post("/logout", follow_redirects=False)

    _login(client, "chris", "pass1")
    assert "friend-desk" not in client.get("/").text


def test_a_profile_cannot_switch_or_see_the_control(admin_enabled, client: TestClient):
    _login(client, "chris", "pass1")
    page = client.get("/")
    assert 'action="/switch"' not in page.text
    assert "Acting as" not in page.text
    response = client.post("/switch", data={"username": "friend", "return_to": "/table"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "Chris · Friend" in client.get("/").text


def test_switch_rejects_unknown_names_and_unsafe_returns(admin_enabled, client: TestClient):
    _login(client, "admin", ADMIN_PASSWORD)
    refused = client.post(
        "/switch",
        data={"username": "ghost", "return_to": "/table"},
        headers={"referer": "https://evil.example/phish"},
        follow_redirects=False,
    )
    assert refused.status_code == 303
    assert refused.headers["location"] == "/"
    assert "Chris · Friend" in client.get("/").text

    for bad in ("https://evil.example/phish", "//evil.example", "/\\evil.example"):
        response = client.post(
            "/switch",
            data={"username": "friend", "return_to": bad},
            headers={"referer": "https://evil.example/out"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    kept = client.post(
        "/switch",
        data={"username": "chris", "return_to": "/archive?kept=1"},
        headers={"referer": "https://evil.example/out"},
        follow_redirects=False,
    )
    assert kept.headers["location"] == "/archive?kept=1"
    archive = client.get("/archive").text
    assert "What you keep" in archive
    chris_form = archive.split('value="chris"', 1)[1].split("</form>", 1)[0]
    assert 'aria-pressed="true"' in chris_form


def test_admin_wrong_password_matches_unknown_and_throttles(admin_enabled, client: TestClient):
    unknown = client.post("/login", data={"username": "nobody", "password": "x"}, follow_redirects=False)
    wrong = client.post("/login", data={"username": "admin", "password": "nope"}, follow_redirects=False)
    assert unknown.status_code == wrong.status_code == 401
    assert "did not match" in unknown.text and "did not match" in wrong.text

    codes = [
        client.post("/login", data={"username": "admin", "password": "nope"}, follow_redirects=False).status_code
        for _ in range(4)
    ]
    assert 429 in codes
    blocked = client.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD}, follow_redirects=False)
    assert blocked.status_code == 429
    assert "Too many tries" in blocked.text


def test_safe_return_keeps_local_paths_only():
    assert _safe_return("/archive?kept=1") == "/archive?kept=1"
    assert _safe_return("") == "/"
    for bad in ("https://evil.example/phish", "//evil.example", "/\\evil.example", "/ok\nid"):
        assert _safe_return(bad) == "/", bad


def test_admin_session_drops_a_forged_marker(admin_enabled):
    person = auth.admin_person()
    assert person is not None
    session: dict = {}
    auth.start_session(session, person)
    assert auth.session_user(session) == "chris"
    assert auth.is_admin_session(session)

    forged = dict(session, adm="0" * len(session["adm"]))
    assert auth.session_user(forged) is None and forged == {}


def test_admin_session_follows_admin_secret_not_the_person(admin_enabled):
    people = auth.load_people()
    saved = people["chris"]
    admin = auth.admin_person()
    assert admin is not None
    session: dict = {}
    auth.start_session(session, admin)
    assert auth.session_user(session) == "chris"
    people["chris"] = auth.Person(saved.username, saved.display, auth.hash_password("brand-new-password"))
    try:
        assert auth.session_user(session) == "chris"
        assert auth.is_admin_session(session)
        auth._ADMIN = auth.Person(admin.username, admin.display, auth.hash_password("other-admin-secret"))
        assert auth.session_user(session) is None and session == {}
    finally:
        people["chris"] = saved


def test_admin_hash_is_what_login_checks(monkeypatch: pytest.MonkeyPatch):
    encoded = auth.hash_password("hash-secret")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", encoded)
    monkeypatch.setenv("ADMIN_PASSWORD", "ignored-plaintext")
    auth._ADMIN_LOADED = False
    auth._ADMIN = None
    try:
        assert auth.verify("admin", "hash-secret") is not None
        assert auth.verify("admin", "ignored-plaintext") is None
    finally:
        auth._ADMIN_LOADED = False
        auth._ADMIN = None


def test_sample_admin_password_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
    auth._ADMIN_LOADED = False
    auth._ADMIN = None
    try:
        with pytest.raises(SystemExit, match="ADMIN_PASSWORD"):
            auth.admin_person()
    finally:
        auth._ADMIN_LOADED = False
        auth._ADMIN = None


@pytest.mark.parametrize(
    "encoded",
    [
        "not-a-hash",
        "scrypt$16384$8$1$valid-salt$a",
        "scrypt$bad$8$1$MDEyMzQ1Njc4OWFiY2RlZg$" + auth._b64(b"x" * auth.SCRYPT_LEN),
    ],
)
def test_bad_admin_hash_is_refused(monkeypatch: pytest.MonkeyPatch, encoded: str):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", encoded)
    auth._ADMIN_LOADED = False
    auth._ADMIN = None
    try:
        with pytest.raises(SystemExit, match="ADMIN_PASSWORD_HASH"):
            auth.admin_person()
    finally:
        auth._ADMIN_LOADED = False
        auth._ADMIN = None


@pytest.mark.parametrize("prefix", ["USER1", "USER2"])
def test_configured_name_cannot_be_admin(monkeypatch: pytest.MonkeyPatch, prefix: str):
    saved = dict(auth.PEOPLE)
    saved_primary = auth._PRIMARY_USER
    auth.PEOPLE.clear()
    monkeypatch.setenv(f"{prefix}_NAME", "Admin")
    try:
        with pytest.raises(SystemExit, match="cannot be admin"):
            auth.load_people()
    finally:
        auth.PEOPLE.clear()
        auth.PEOPLE.update(saved)
        auth._PRIMARY_USER = saved_primary
