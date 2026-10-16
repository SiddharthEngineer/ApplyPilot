"""drive_status, the legacy token check the dashboard's owner fallback uses (the `strelitzia drive` commands are gone)."""

import pytest

from strelitzia import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", tmp_path / "google_token.json")
    return tmp_path


def test_drive_status(env, monkeypatch):
    from strelitzia.storage import drive
    monkeypatch.setattr(drive, "drive_libraries_installed", lambda: True)
    assert drive.drive_status()[0] == "not_authorized"
    (env / "google_token.json").write_text("{}")
    assert drive.drive_status()[0] == "authorized"
    monkeypatch.setattr(drive, "drive_libraries_installed", lambda: False)
    assert drive.drive_status()[0] == "not_installed"
