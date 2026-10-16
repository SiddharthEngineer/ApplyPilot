"""The owner's Users page API: invite emails, see each account's spend this month, change caps, disable or re-enable.

Owner-only (403 for everyone else). Removing a user disables the account and keeps its data, so it can be undone.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from strelitzia import users
from strelitzia.database import Connection, get_connection
from strelitzia.llm import user_month_spend
from strelitzia.web import auth

PREFIX = "/app/api/admin"

router = APIRouter(prefix=PREFIX)


def _conn(request: Request) -> Connection:
    return get_connection(request.app.state.db)


def _owner(request: Request) -> users.User:
    user = auth.current_user(request)
    if user.role != "owner":
        raise HTTPException(403, "Only the owner can manage users")
    return user


def _user_out(conn: Connection, user: users.User) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role, "status": user.status,
        "monthly_cap_usd": user.monthly_cap_usd, "month_spend_usd": round(user_month_spend(conn, user.id), 4),
        "last_login_at": user.last_login_at,
    }


def _target(conn: Connection, user_id: str) -> users.User:
    user = users.get(conn, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


class Invite(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    monthly_cap_usd: float | None = Field(default=users.DEFAULT_CAP_USD, ge=0)

    @field_validator("email")
    @classmethod
    def _looks_like_an_email(cls, v: str) -> str:
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or "." not in domain or any(c.isspace() for c in v):
            raise ValueError("Enter an email address")
        return v


class UserPatch(BaseModel):
    status: Literal["active", "disabled"] | None = None
    monthly_cap_usd: float | None = Field(default=None, ge=0)


@router.get("/users")
def list_users(request: Request) -> dict:
    _owner(request)
    conn = _conn(request)
    return {"users": [_user_out(conn, u) for u in users.list_users(conn)]}


@router.post("/users", status_code=201)
def invite_user(body: Invite, request: Request) -> dict:
    _owner(request)
    conn = _conn(request)
    if users.by_email(conn, body.email) is not None:
        raise HTTPException(409, f"{body.email} already has an account")
    return _user_out(conn, users.invite(conn, body.email, body.monthly_cap_usd))


def _set_status(conn: Connection, me: users.User, target: users.User, status: str) -> users.User:
    if target.role == "owner" and status != "active":
        raise HTTPException(400, "You can't disable the owner account" if target.id != me.id
                            else "You can't disable your own account")
    if status == "active" and target.status == "disabled":
        # Re-enabling: an account that never signed in goes back to being an invite.
        status = "active" if target.last_login_at else "invited"
    elif status == "active":
        return target  # already active or invited: nothing to do
    return users.set_status(conn, target.id, status)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, request: Request) -> dict:
    me = _owner(request)
    conn = _conn(request)
    target = _target(conn, user_id)
    if body.status is not None:
        target = _set_status(conn, me, target, body.status)
    if "monthly_cap_usd" in body.model_fields_set:  # an explicit null means no cap
        if target.role == "owner":
            raise HTTPException(400, "The owner has no cap (LLM_MONTHLY_BUDGET_USD limits the whole server)")
        target = users.set_cap(conn, target.id, body.monthly_cap_usd)
    return _user_out(conn, target)


@router.delete("/users/{user_id}")
def disable_user(user_id: str, request: Request) -> dict:
    """Disable the account (its jobs, documents and spend history are kept)."""
    me = _owner(request)
    conn = _conn(request)
    return _user_out(conn, _set_status(conn, me, _target(conn, user_id), "disabled"))
