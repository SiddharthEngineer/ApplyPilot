"""Tests for `strelitzia users` (user-auth-8-cli Task 1)."""

import re

import typer

import pytest
from typer.testing import CliRunner

from strelitzia import config, database, users
from strelitzia.cli import app

runner = CliRunner()


def _plain(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "users.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setenv("STRELITZIA_OWNER_EMAIL", "Boss@Example.com")
    conn = database.init_db(db)
    yield conn
    database.close_connection(db)


def _invoke(*args):
    return runner.invoke(app, ["users", *args], env={"COLUMNS": "200"})


def test_help_lists_commands():
    out = _plain(_invoke("--help").output)
    for cmd in ("list", "invite", "disable", "enable", "cap"):
        assert cmd in out


def test_list_shows_the_owner_first(conn):
    users.invite(conn, "zed@example.com")
    users.invite(conn, "amy@example.com", cap=2)
    result = _invoke("list")
    assert result.exit_code == 0, result.output
    out = _plain(result.output)
    assert out.index("boss@example.com") < out.index("amy@example.com") < out.index("zed@example.com")
    assert "$2.00" in out and "none" in out


def test_invite_with_default_and_custom_cap(conn):
    result = _invoke("invite", "New@Example.com")
    assert result.exit_code == 0, result.output
    assert "new@example.com" in _plain(result.output) and "invited" in result.output
    assert users.by_email(conn, "new@example.com").monthly_cap_usd == users.DEFAULT_CAP_USD

    assert _invoke("invite", "two@example.com", "--cap", "12.5").exit_code == 0
    assert users.by_email(conn, "two@example.com").monthly_cap_usd == 12.5


def test_invite_twice_is_refused(conn):
    users.invite(conn, "dup@example.com", cap=3)
    result = _invoke("invite", "dup@example.com", "--cap", "9")
    assert result.exit_code == 1
    assert "already has an account" in result.output
    assert users.by_email(conn, "dup@example.com").monthly_cap_usd == 3


def test_negative_cap_is_refused(conn):
    assert _invoke("invite", "neg@example.com", "--cap", "-1").exit_code != 0
    assert users.by_email(conn, "neg@example.com") is None


def test_disable(conn):
    users.invite(conn, "bye@example.com")
    result = _invoke("disable", "BYE@example.com")
    assert result.exit_code == 0, result.output
    assert "disabled" in result.output
    assert users.by_email(conn, "bye@example.com").status == "disabled"


@pytest.mark.parametrize("email", ["Bob Smith", "bob@gmail", "@example.com"])
def test_invite_rejects_a_non_email(conn, email):
    result = _invoke("invite", email)
    assert result.exit_code == 1
    assert "isn't an email address" in result.output
    assert len(users.list_users(conn)) == 1  # only the owner


def test_enable(conn):
    users.invite(conn, "back@example.com")
    _invoke("disable", "back@example.com")
    result = _invoke("enable", "back@example.com")
    assert result.exit_code == 0, result.output
    assert users.by_email(conn, "back@example.com").status == "invited"  # never signed in: an invite again

    users.activate(conn, "back@example.com", "Back")
    _invoke("disable", "back@example.com")
    assert _invoke("enable", "back@example.com").exit_code == 0
    assert users.by_email(conn, "back@example.com").status == "active"
    assert "isn't disabled" in _invoke("enable", "back@example.com").output


def test_cap(conn):
    users.invite(conn, "cap@example.com")
    result = _invoke("cap", "cap@example.com", "20")
    assert result.exit_code == 0, result.output
    assert "$20.00" in result.output
    assert users.by_email(conn, "cap@example.com").monthly_cap_usd == 20


def test_owner_cannot_be_disabled_or_capped(conn):
    assert _invoke("disable", "boss@example.com").exit_code == 1
    assert _invoke("cap", "boss@example.com", "1").exit_code == 1
    owner = users.owner(conn)
    assert owner.status == "active" and owner.monthly_cap_usd is None


@pytest.mark.parametrize("args", [("disable", "nobody@example.com"), ("cap", "nobody@example.com", "1")])
def test_unknown_email(conn, args):
    result = _invoke(*args)
    assert result.exit_code == 1
    assert "No account for nobody@example.com" in _plain(result.output)


def test_cli_is_admin_only():
    """user-auth-8-cli: per-user commands (run, workbench, drive, dashboard...) live in the dashboard now."""
    commands = {c.name for c in typer.main.get_command(app).commands.values()}
    assert commands == {"serve", "init", "doctor", "db", "users"}
