"""Dashboard app fixtures: the FastAPI app on a temp SQLite DB (APPLYPILOT_DATABASE_URL is unset by tests/conftest.py)."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from applypilot.database import close_connection, get_connection
from applypilot.web.app import create_app

HEADERS = {"X-ApplyPilot": "1"}


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

