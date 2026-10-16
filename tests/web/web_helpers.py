"""Helpers shared by the dashboard tests."""

import base64
import json

# The session secret of conftest's `auth_app`.
SESSION_SECRET = "test-session-secret"


def add_job(conn, url="https://example.com/jobs/1", **cols):
    """Insert a job row with sensible defaults (title, company, discovered_at, a description)."""
    row = {"url": url, "title": "Data Scientist", "company": "Acme", "site": "indeed",
           "discovered_at": "2026-10-01T12:00:00+00:00", "full_description": "Build models.", **cols}
    names = ", ".join(row)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return url


def second_user(conn, email="b@example.com"):
    """Seed a second (invited) user and return it."""
    from strelitzia import users

    return users.invite(conn, email)


def act_as(monkeypatch, user):
    """Make every API request act as `user` (login is off in tests, so otherwise they act as the owner)."""
    from strelitzia.web import app as app_mod

    monkeypatch.setattr(app_mod, "current_user", lambda request: user)


def sign_in_as(client, user, secret=SESSION_SECRET):
    """Give a client the session cookie a real Google sign-in as `user` would set."""
    from itsdangerous import TimestampSigner

    from strelitzia.web import auth

    data = base64.b64encode(json.dumps({"uid": user.id}).encode())
    client.cookies.set(auth.SESSION_COOKIE, TimestampSigner(secret).sign(data).decode())
