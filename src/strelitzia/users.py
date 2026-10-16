"""User accounts: the owner plus invited emails (table `users`, created by `database._ensure_tables`).

role is owner | user, status is invited | active | disabled. Emails are stored lowercased. Every per-user row
(jobs, workspace tables, preferences, llm_usage) carries a `user_id` pointing here.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from strelitzia.config import env

# Monthly Claude spend cap for an invited user; the owner has none.
DEFAULT_CAP_USD = 5.0
# The owner's email when STRELITZIA_OWNER_EMAIL is unset (tests, a fresh CLI install).
FALLBACK_OWNER_EMAIL = "owner@localhost"

ROLES = ("owner", "user")
STATUSES = ("invited", "active", "disabled")
_COLUMNS = "id, email, name, role, status, monthly_cap_usd, last_login_at"


@dataclass
class User:
    id: str
    email: str
    name: str | None
    role: str
    status: str
    monthly_cap_usd: float | None
    last_login_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(email: str) -> str:
    return email.strip().lower()


def _row(row) -> User | None:
    return User(*tuple(row)) if row else None


def get(conn, user_id: str) -> User | None:
    return _row(conn.execute(f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone())


def by_email(conn, email: str) -> User | None:
    return _row(conn.execute(f"SELECT {_COLUMNS} FROM users WHERE email = ?", (_norm(email),)).fetchone())


def list_users(conn) -> list[User]:
    """The owner first, then everyone else by email."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM users ORDER BY CASE WHEN role = 'owner' THEN 0 ELSE 1 END, email"
    ).fetchall()
    return [User(*tuple(r)) for r in rows]


def _owner_row(conn):
    return conn.execute(
        f"SELECT {_COLUMNS} FROM users WHERE role = 'owner' ORDER BY created_at LIMIT 1"
    ).fetchone()


def owner(conn) -> User:
    """The owner account, created on first call from STRELITZIA_OWNER_EMAIL (else owner@localhost)."""
    row = _owner_row(conn)
    if row:
        return User(*tuple(row))
    email = _norm(env("OWNER_EMAIL") or FALLBACK_OWNER_EMAIL)
    # Two processes starting on a fresh database both get here: the second insert is a no-op.
    conn.execute(
        "INSERT INTO users (id, email, role, status, monthly_cap_usd, created_at) "
        "VALUES (?, ?, 'owner', 'active', NULL, ?) ON CONFLICT (email) DO NOTHING",
        (uuid.uuid4().hex, email, _now()),
    )
    # An invited address that turns out to be the owner's is promoted.
    conn.execute("UPDATE users SET role = 'owner', status = 'active', monthly_cap_usd = NULL WHERE email = ?",
                 (email,))
    conn.commit()
    return User(*tuple(_owner_row(conn)))


def invite(conn, email: str, cap: float | None = DEFAULT_CAP_USD) -> User:
    """Invite an email (idempotent: an existing account is returned unchanged)."""
    existing = by_email(conn, email)
    if existing:
        return existing
    # Two invites of one email at once: the second insert is a no-op and both return the same account.
    conn.execute(
        "INSERT INTO users (id, email, name, role, status, monthly_cap_usd, created_at) "
        "VALUES (?, ?, NULL, 'user', 'invited', ?, ?) ON CONFLICT (email) DO NOTHING",
        (uuid.uuid4().hex, _norm(email), cap, _now()),
    )
    conn.commit()
    return by_email(conn, email)


def activate(conn, email: str, name: str | None) -> User | None:
    """Turn an invite into an active account (an already active one is returned as is).

    None for an email that was never invited, or a disabled account.
    """
    user = by_email(conn, email)
    if user is None or user.status == "disabled":
        return None
    if user.status == "active":
        return user
    conn.execute("UPDATE users SET status = 'active', name = ?, last_login_at = ? WHERE id = ?",
                 (name, _now(), user.id))
    conn.commit()
    return get(conn, user.id)


def set_status(conn, user_id: str, status: str) -> User | None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    conn.commit()
    return get(conn, user_id)


def set_cap(conn, user_id: str, cap: float | None) -> User | None:
    """The monthly cap in USD; None = no cap."""
    conn.execute("UPDATE users SET monthly_cap_usd = ? WHERE id = ?", (cap, user_id))
    conn.commit()
    return get(conn, user_id)
