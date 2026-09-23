from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

TEST_ROOT = Path(tempfile.mkdtemp(prefix=f"between-tests-{os.getenv('PYTEST_XDIST_WORKER', 'main')}-"))
TEST_DB = TEST_ROOT / "between.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["USER1_NAME"] = "chris"
os.environ["USER1_DISPLAY"] = "Chris"
os.environ["USER1_PASSWORD"] = "pass1"
os.environ["USER2_NAME"] = "friend"
os.environ["USER2_DISPLAY"] = "Friend"
os.environ["USER2_PASSWORD"] = "pass2"

from app.db import Base, engine, get_db  # noqa: E402
from app.hub import hub  # noqa: E402
from app.main import app  # noqa: E402
from app.markdown_render import clear_markdown_cache  # noqa: E402

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db


@pytest.fixture(autouse=True)
def reset_state():
    clear_markdown_cache()
    hub.rooms.clear()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    for name in ("local", "shared"):
        shutil.rmtree(TEST_ROOT / name, ignore_errors=True)
    yield
    hub.rooms.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
