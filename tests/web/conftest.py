"""Dashboard app fixtures: the FastAPI app on a temp SQLite DB (APPLYPILOT_DATABASE_URL is unset by tests/conftest.py)."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from applypilot.database import close_connection, get_connection  # noqa: E402
from applypilot.web.app import create_app  # noqa: E402

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


def add_job(conn, url="https://example.com/jobs/1", **cols):
    """Insert a job row with sensible defaults (title, company, discovered_at, a description)."""
    row = {"url": url, "title": "Data Scientist", "company": "Acme", "site": "indeed",
           "discovered_at": "2026-10-01T12:00:00+00:00", "full_description": "Build models.", **cols}
    names = ", ".join(row)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return url
