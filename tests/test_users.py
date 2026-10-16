"""Accounts (strelitzia.users): the owner, invites, activation."""

import pytest

from strelitzia import users
from strelitzia.database import close_connection, init_db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("STRELITZIA_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("APPLYPILOT_OWNER_EMAIL", raising=False)
    path = tmp_path / "u.db"
    c = init_db(path)
    yield c
    close_connection(path)


def test_owner_falls_back_to_localhost_and_is_stable(conn):
    owner = users.owner(conn)
    assert owner.email == "owner@localhost"
    assert (owner.role, owner.status, owner.monthly_cap_usd) == ("owner", "active", None)
    assert users.owner(conn) == owner
    assert users.get(conn, owner.id) == owner


def test_owner_comes_from_env_lowercased(tmp_path, monkeypatch):
    monkeypatch.setenv("STRELITZIA_OWNER_EMAIL", " Me@Example.COM ")
    path = tmp_path / "o.db"
    c = init_db(path)
    try:
        assert users.owner(c).email == "me@example.com"
    finally:
        close_connection(path)


def test_invite_then_activate(conn):
    invited = users.invite(conn, "Friend@Example.com")
    assert (invited.email, invited.role, invited.status) == ("friend@example.com", "user", "invited")
    assert invited.monthly_cap_usd == users.DEFAULT_CAP_USD

    active = users.activate(conn, "friend@example.com", "Friend")
    assert (active.id, active.status, active.name) == (invited.id, "active", "Friend")
    # A second login returns the same account.
    assert users.activate(conn, "FRIEND@example.com", "Friend") == active


def test_activate_unknown_or_disabled_email_is_none(conn):
    assert users.activate(conn, "stranger@example.com", "X") is None
    u = users.invite(conn, "gone@example.com")
    users.set_status(conn, u.id, "disabled")
    assert users.activate(conn, "gone@example.com", "X") is None


def test_emails_are_case_insensitive(conn):
    a = users.invite(conn, "Case@Example.com")
    assert users.invite(conn, "case@EXAMPLE.com") == a
    assert users.by_email(conn, "CASE@example.COM") == a


def test_set_status_and_cap_and_list(conn):
    owner = users.owner(conn)
    b = users.invite(conn, "b@example.com", cap=2.5)
    a = users.invite(conn, "a@example.com")
    assert users.set_cap(conn, b.id, 10.0).monthly_cap_usd == 10.0
    assert users.set_cap(conn, b.id, None).monthly_cap_usd is None
    assert users.set_status(conn, a.id, "active").status == "active"
    with pytest.raises(ValueError):
        users.set_status(conn, a.id, "banned")
    assert [u.email for u in users.list_users(conn)] == [owner.email, "a@example.com", "b@example.com"]
