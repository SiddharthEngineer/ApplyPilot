"""App skeleton: health, the UI's SPA fallback, and the CSRF header guard (dashboard-api Task 2)."""

import re

import pytest
from fastapi.testclient import TestClient

from strelitzia.web import app as web_app
from strelitzia.web.app import create_app
from web_helpers import sign_in_as


def test_health(client):
    r = client.get("/app/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] == "sqlite"
    assert body["version"]


def test_ui_index_and_assets(client):
    assert client.get("/app/").text.startswith("<!doctype html>")
    assert client.get("/app/assets/app.js").text == "console.log('ok')"
    assert client.get("/app", follow_redirects=False).headers["location"] == "/app/"


def test_spa_fallback_for_unknown_ui_paths(client):
    r = client.get("/app/jobs/abc123")
    assert r.status_code == 200
    assert "<div id=root>" in r.text


def test_missing_asset_and_unknown_api_are_404(client):
    assert client.get("/app/assets/missing.js").status_code == 404
    r = client.get("/app/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_no_path_traversal(client, web_dir):
    (web_dir.parent / "secret.txt").write_text("secret")
    r = client.get("/app/..%2Fsecret.txt")
    assert "secret" != r.text


def test_api_only_without_ui(db_path, tmp_path, monkeypatch):
    monkeypatch.delenv("STRELITZIA_WEB_DIR", raising=False)
    monkeypatch.setattr(web_app, "PACKAGED_STATIC_DIR", tmp_path / "no-bundle")
    with TestClient(create_app(db=db_path)) as c:
        assert c.get("/app/api/health").status_code == 200
        assert c.get("/app/").status_code == 404


def test_packaged_bundle_is_the_default_ui(db_path, tmp_path, monkeypatch):
    monkeypatch.delenv("STRELITZIA_WEB_DIR", raising=False)
    bundle = tmp_path / "static"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>packaged</title>")
    monkeypatch.setattr(web_app, "PACKAGED_STATIC_DIR", bundle)
    with TestClient(create_app(db=db_path)) as c:
        assert "packaged" in c.get("/app/").text


def test_mutations_need_the_header(app):
    with TestClient(app) as bare:
        for method in ("post", "patch", "delete", "put"):
            r = getattr(bare, method)("/app/api/health")
            assert r.status_code == 403, method
        assert bare.get("/app/api/health").status_code == 200
        r = bare.post("/app/api/health", headers={"X-Strelitzia": "0"})
        assert r.status_code == 403
        # With the header the guard lets it through (health has no POST, so the router answers 405).
        assert bare.post("/app/api/health", headers={"X-Strelitzia": "1"}).status_code == 405


def test_legacy_csrf_header_is_accepted(app):
    """Dashboard tabs opened before the rename still send the old header until they reload."""
    with TestClient(app) as bare:
        assert bare.post("/app/api/health", headers={"X-ApplyPilot": "1"}).status_code == 405
        assert bare.post("/app/api/health", headers={"X-ApplyPilot": "0"}).status_code == 403


def test_serve_command_is_registered():
    from typer.testing import CliRunner

    from strelitzia.cli import app as cli

    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    # Typer's Rich help forces colour under GITHUB_ACTIONS/FORCE_COLOR, splitting "--port" with ANSI codes.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--port" in plain


def test_auth_me_with_login_off(client):
    me = client.get("/app/api/auth/me").json()
    assert me["enabled"] is False and me["user"]["role"] == "owner"


def test_login_on_needs_a_session_for_the_api(auth_client):
    assert auth_client.get("/app/api/jobs").status_code == 401
    assert auth_client.get("/app/api/openapi.json").status_code == 401
    assert auth_client.post("/app/api/auth/logout").status_code == 200
    assert auth_client.get("/app/api/health").status_code == 200
    assert auth_client.get("/app/api/auth/me").json() == {"enabled": True, "user": None}
    # the UI stays public so the SPA can show its login screen
    assert auth_client.get("/app/").status_code == 200
    assert auth_client.get("/app/assets/app.js").status_code == 200


def test_login_on_with_a_session(auth_client, db_path):
    from strelitzia import users
    from strelitzia.database import get_connection

    conn = get_connection(db_path)
    friend = users.activate(conn, users.invite(conn, "friend@example.com").email, "Friend")
    sign_in_as(auth_client, friend)
    assert auth_client.get("/app/api/jobs").status_code == 200
    assert auth_client.get("/app/api/auth/me").json()["user"]["email"] == "friend@example.com"
    users.set_status(conn, friend.id, "disabled")
    assert auth_client.get("/app/api/jobs").status_code == 401


def test_a_forged_session_is_refused(auth_client, db_path):
    from strelitzia import users
    from strelitzia.database import get_connection

    sign_in_as(auth_client, users.owner(get_connection(db_path)), secret="not-the-secret")
    assert auth_client.get("/app/api/jobs").status_code == 401


def test_session_cookie_flags(auth_app, db_path, monkeypatch, web_dir):
    monkeypatch.setenv("STRELITZIA_PUBLIC_URL", "https://strelitzia.example.net")
    app = create_app(static_dir=web_dir, db=db_path)
    with TestClient(app, base_url="https://testserver") as c:
        cookie = c.get("/app/api/auth/login", follow_redirects=False).headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert f"max-age={30 * 24 * 3600}" in cookie


def test_login_on_without_a_session_secret_refuses_to_start(db_path, monkeypatch):
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_ID", "client-123")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(db=db_path)
