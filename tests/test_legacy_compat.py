"""Deprecated fallbacks from before the rename (ApplyPilot → Strelitzia, 0.4.x): env vars, data dir, DB file, CLI name."""

import sys
from importlib.metadata import distribution

import pytest

from strelitzia import config


@pytest.fixture
def no_dir_env(monkeypatch):
    monkeypatch.delenv("STRELITZIA_DIR", raising=False)
    monkeypatch.delenv("APPLYPILOT_DIR", raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch, no_dir_env):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# -- env() --------------------------------------------------------------------

def test_env_prefers_the_new_name(monkeypatch):
    monkeypatch.setenv("STRELITZIA_PROBE", "new")
    monkeypatch.setenv("APPLYPILOT_PROBE", "legacy")
    assert config.env("PROBE") == "new"


def test_env_falls_back_to_the_legacy_name(monkeypatch):
    monkeypatch.delenv("STRELITZIA_PROBE", raising=False)
    monkeypatch.setenv("APPLYPILOT_PROBE", "legacy")
    assert config.env("PROBE") == "legacy"


def test_env_default_and_empty_values(monkeypatch):
    monkeypatch.setenv("STRELITZIA_PROBE", "")
    monkeypatch.delenv("APPLYPILOT_PROBE", raising=False)
    assert config.env("PROBE") is None
    assert config.env("PROBE", "fallback") == "fallback"
    monkeypatch.setenv("APPLYPILOT_PROBE", "legacy")
    assert config.env("PROBE", "fallback") == "legacy"


def test_process_env_beats_a_dotenv_value_under_the_new_name(monkeypatch):
    """compose sets APPLYPILOT_DATABASE_URL; a renamed line in the mounted .env must not override it."""
    monkeypatch.setattr(config, "_PROCESS_ENV", frozenset({"APPLYPILOT_PROBE"}))
    monkeypatch.setenv("APPLYPILOT_PROBE", "from-compose")
    monkeypatch.setenv("STRELITZIA_PROBE", "from-dotenv")
    assert config.env("PROBE") == "from-compose"
    monkeypatch.setattr(config, "_PROCESS_ENV", frozenset({"APPLYPILOT_PROBE", "STRELITZIA_PROBE"}))
    assert config.env("PROBE") == "from-dotenv"  # both real: the new name wins


def test_database_url_reads_the_legacy_name(monkeypatch):
    monkeypatch.delenv("STRELITZIA_DATABASE_URL", raising=False)
    monkeypatch.setenv("APPLYPILOT_DATABASE_URL", "postgresql://u:p@127.0.0.1/db")
    assert config.database_url() == "postgresql://u:p@127.0.0.1/db"
    monkeypatch.setenv("STRELITZIA_DATABASE_URL", "postgresql://u:p@127.0.0.1/new")
    assert config.database_url() == "postgresql://u:p@127.0.0.1/new"


# -- resolve_app_dir() --------------------------------------------------------

def test_app_dir_env_override(home, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(home / "legacy-env"))
    assert config.resolve_app_dir() == home / "legacy-env"
    monkeypatch.setenv("STRELITZIA_DIR", str(home / "new-env"))
    assert config.resolve_app_dir() == home / "new-env"
    assert not (home / ".strelitzia").exists()


def test_app_dir_prefers_existing_strelitzia(home):
    (home / ".strelitzia").mkdir()
    (home / ".applypilot").mkdir()
    assert config.resolve_app_dir() == home / ".strelitzia"


def test_app_dir_uses_legacy_applypilot_dir(home):
    (home / ".applypilot").mkdir()
    assert config.resolve_app_dir() == home / ".applypilot"
    assert not (home / ".strelitzia").exists()


def test_app_dir_created_when_neither_exists(home):
    assert config.resolve_app_dir() == home / ".strelitzia"
    assert (home / ".strelitzia").is_dir()


# -- SQLite file --------------------------------------------------------------

def test_legacy_db_file_is_picked_up(tmp_path):
    assert config.default_db_path(tmp_path) == tmp_path / "strelitzia.db"
    (tmp_path / "applypilot.db").touch()
    assert config.default_db_path(tmp_path) == tmp_path / "applypilot.db"
    (tmp_path / "strelitzia.db").touch()
    assert config.default_db_path(tmp_path) == tmp_path / "strelitzia.db"


# -- console scripts ----------------------------------------------------------

def _script(name: str):
    (ep,) = [ep for ep in distribution("strelitzia").entry_points if ep.group == "console_scripts" and ep.name == name]
    return ep.load()


def test_both_console_scripts_run_the_same_typer_app(monkeypatch, capsys):
    from strelitzia import __version__, cli

    assert _script("strelitzia") is cli.app
    legacy = _script("applypilot")
    assert legacy is cli.legacy_main

    monkeypatch.setattr(sys, "argv", ["applypilot", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        legacy()
    out = capsys.readouterr()
    assert exit_info.value.code == 0
    assert "deprecated" in out.err and "strelitzia" in out.err
    assert f"strelitzia {__version__}" in out.out
