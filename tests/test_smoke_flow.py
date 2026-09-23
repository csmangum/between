from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

# Isolate the app DB before importing the application module.
TEST_DB = Path("/tmp/between-smoke.db")
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["USER1_NAME"] = "chris"
os.environ["USER1_DISPLAY"] = "Chris"
os.environ["USER1_PASSWORD"] = "pass1"
os.environ["USER2_NAME"] = "friend"
os.environ["USER2_DISPLAY"] = "Friend"
os.environ["USER2_PASSWORD"] = "pass2"

from app.db import Base, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_offer_accept_flow(client: TestClient):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Letters", "prompt": "private note"}, follow_redirects=False)
    assert created.status_code == 303
    topic_url = created.headers["location"]
    topic_id = topic_url.rsplit("/", 1)[-1]

    offered = client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    assert offered.status_code == 303

    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")

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


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
