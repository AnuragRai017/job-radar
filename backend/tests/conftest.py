from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

# Configure the application before any app module imports the cached settings.
_TEST_DIR = Path(tempfile.gettempdir()) / "roleradar-tests"
_TEST_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB = _TEST_DIR / f"test-{uuid.uuid4().hex}.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SCRAPE_ON_STARTUP"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Job, ScrapeRun  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean_database():
    init_db()
    with SessionLocal() as session:
        session.query(ScrapeRun).delete()
        session.query(Job).delete()
        session.commit()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
