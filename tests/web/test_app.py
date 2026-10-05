"""App skeleton: health, the UI's SPA fallback, and the CSRF header guard (dashboard-api Task 2)."""

import re

from fastapi.testclient import TestClient

from applypilot.web import app as web_app
from applypilot.web.app import create_app


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
    monkeypatch.delenv("APPLYPILOT_WEB_DIR", raising=False)
    monkeypatch.setattr(web_app, "PACKAGED_STATIC_DIR", tmp_path / "no-bundle")
    with TestClient(create_app(db=db_path)) as c:
        assert c.get("/app/api/health").status_code == 200
        assert c.get("/app/").status_code == 404


def test_packaged_bundle_is_the_default_ui(db_path, tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_WEB_DIR", raising=False)
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
        r = bare.post("/app/api/health", headers={"X-ApplyPilot": "0"})
        assert r.status_code == 403
        # With the header the guard lets it through (health has no POST, so the router answers 405).
        assert bare.post("/app/api/health", headers={"X-ApplyPilot": "1"}).status_code == 405


def test_serve_command_is_registered():
    from typer.testing import CliRunner

    from applypilot.cli import app as cli

    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    # Typer's Rich help forces colour under GITHUB_ACTIONS/FORCE_COLOR, splitting "--port" with ANSI codes.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--port" in plain
