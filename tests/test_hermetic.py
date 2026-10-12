"""Tests for the hermetic guard in tests/conftest.py (test-tiers-and-qc Task 2)."""

import os
import socket
from pathlib import Path

import pytest

from conftest import NetworkAccessBlocked


def test_non_local_connect_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(NetworkAccessBlocked, match="203.0.113.1"):
            s.connect(("203.0.113.1", 80))


def test_non_local_connect_ex_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(NetworkAccessBlocked):
            s.connect_ex(("203.0.113.1", 443))


def test_create_connection_blocked():
    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(("192.0.2.1", 80), timeout=1)


def test_localhost_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


@pytest.mark.live
def test_live_test_not_blocked():
    """With --run-live, the guard is not applied (default run skips this test)."""
    assert socket.socket.connect.__name__ != "guarded"


def test_unit_test_is_guarded():
    assert socket.socket.connect.__name__ == "guarded"


def test_app_dir_isolated():
    from strelitzia import config

    assert os.environ["STRELITZIA_DIR"] == str(config.APP_DIR)
    assert config.APP_DIR not in (Path.home() / ".strelitzia", Path.home() / config.LEGACY_DIR_NAME)
    assert config.APP_DIR.name.startswith("strelitzia-test-")
    assert config.DB_PATH.parent == config.APP_DIR


def test_load_env_ignores_repo_root_dotenv(tmp_path, monkeypatch):
    """load_env() must not read a .env from the CWD (the developer's real keys) during unit tests."""
    import os

    from strelitzia import pipeline
    from strelitzia.config import load_env

    (tmp_path / ".env").write_text("STRELITZIA_HERMETIC_PROBE=leaked\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STRELITZIA_HERMETIC_PROBE", raising=False)
    load_env()
    pipeline.load_env()
    assert "STRELITZIA_HERMETIC_PROBE" not in os.environ
