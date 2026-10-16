"""Dashboard app fixtures: the FastAPI app on a temp SQLite DB (STRELITZIA_DATABASE_URL is unset by tests/conftest.py)."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from strelitzia.database import close_connection, get_connection
from strelitzia.web.app import create_app
from web_helpers import SESSION_SECRET

HEADERS = {"X-Strelitzia": "1"}


@pytest.fixture(autouse=True)
def _login_off(monkeypatch):
    """Login is off in every web test (a developer's STRELITZIA_GOOGLE_CLIENT_ID never leaks in); see `auth_client`."""
    for prefix in ("STRELITZIA_", "APPLYPILOT_"):
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "OWNER_EMAIL", "PUBLIC_URL"):
            monkeypatch.delenv(prefix + name, raising=False)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "jobs.db"
    yield path
    close_connection(path)


@pytest.fixture
def web_dir(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    (d / "assets" / "app.js").write_text("console.log('ok')")
    return d


@pytest.fixture
def app(db_path, web_dir):
    return create_app(static_dir=web_dir, db=db_path)


@pytest.fixture
def client(app):
    with TestClient(app, headers=HEADERS) as c:
        yield c


@pytest.fixture
def conn(app, db_path):
    return get_connection(db_path)


@pytest.fixture
def auth_app(db_path, web_dir, monkeypatch):
    """The app with Google sign-in on (no request leaves the process: these tests never reach Google)."""
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STRELITZIA_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("STRELITZIA_OWNER_EMAIL", "owner@example.com")
    return create_app(static_dir=web_dir, db=db_path)


@pytest.fixture
def auth_client(auth_app):
    """A client with login on and no session; `web_helpers.sign_in_as(client, user)` gives it one."""
    with TestClient(auth_app, headers=HEADERS) as c:
        yield c

