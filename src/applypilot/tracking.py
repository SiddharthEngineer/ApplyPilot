"""Application status for the dashboard: derived (Active/Inactive) plus what the user records.

The effective status of a job, highest precedence first:
  rejected / heard_back  (user_status)
  submitted              (user_status, or `applypilot apply` recorded apply_status='applied')
  in_progress            (user_status)
  inactive               (no user status and the deadline is before today)
  active                 (everything else, including jobs with no deadline)

`effective_status` (Python) and `status_sql` (a SQL CASE, for filtering and counting in the DB) must agree;
tests/web/test_tracking.py checks them against each other.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime

STATUSES = ("active", "inactive", "in_progress", "submitted", "rejected", "heard_back")
COLORS = {"active": "grey", "inactive": "orange", "in_progress": "yellow",
          "submitted": "blue", "rejected": "red", "heard_back": "green"}
USER_STATUSES = ("in_progress", "submitted", "rejected", "heard_back")
RESPONSE_STATUSES = ("rejected", "heard_back")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def effective_status(job: Mapping, today: date) -> str:
    """The status the dashboard shows for a job row."""
    user = job.get("user_status")
    if user in RESPONSE_STATUSES:
        return user
    if user == "submitted" or job.get("apply_status") == "applied":
        return "submitted"
    if user == "in_progress":
        return "in_progress"
    deadline = job.get("deadline")
    if deadline and deadline < today.isoformat():
        return "inactive"
    return "active"


def status_sql(today: str) -> str:
    """A SQL CASE expression equal to effective_status() for each row. `today` is YYYY-MM-DD.

    `today` is validated and inlined (not a bound parameter), so the expression can be used anywhere
    in a query, including GROUP BY, without parameter-order bookkeeping.
    """
    day = date.fromisoformat(today).isoformat()
    return (
        "(CASE"
        " WHEN user_status IN ('rejected', 'heard_back') THEN user_status"
        " WHEN user_status = 'submitted' OR apply_status = 'applied' THEN 'submitted'"
        " WHEN user_status = 'in_progress' THEN 'in_progress'"
        f" WHEN deadline IS NOT NULL AND deadline != '' AND deadline < '{day}' THEN 'inactive'"
        " ELSE 'active' END)"
    )


def submitted_date(job: Mapping) -> str | None:
    """When the application went in: submitted_at, else applied_at for jobs `applypilot apply` submitted."""
    value = job.get("submitted_at") or (job.get("applied_at") if job.get("apply_status") == "applied" else None)
    return value or None


def days_since_submitted(job: Mapping, today: date) -> int | None:
    """Whole days since the application was submitted; None unless the effective status is Submitted."""
    if effective_status(job, today) != "submitted":
        return None
    value = submitted_date(job)
    if not value:
        return None
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return (today - day).days


def no_response(job: Mapping, today: date) -> bool:
    """Submitted and nothing heard yet."""
    return effective_status(job, today) == "submitted" and not job.get("responded_at")


def _normalize_date(value: str) -> str:
    """A YYYY-MM-DD date or a full ISO timestamp, as stored text. Raises ValueError otherwise."""
    value = value.strip()
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    return datetime.fromisoformat(value).isoformat()


def set_status(conn, url: str, status: str | None, *, submitted_at: str | None = None,
               source: str = "dashboard") -> dict:
    """Record a user status for a job (None resets to the derived Active/Inactive) and log the change.

    submitted: sets submitted_at (default now, UTC ISO) and clears responded_at.
    rejected / heard_back: sets responded_at to now.
    Returns {"url", "from_status", "to_status", "at"}. Raises KeyError for an unknown job, ValueError for a bad status.
    """
    if status is not None and status not in USER_STATUSES:
        raise ValueError(f"Unknown status {status!r}; expected one of {USER_STATUSES} or None")
    row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    if row is None:
        raise KeyError(url)
    job = dict(row)
    today = datetime.now(UTC).date()
    before = effective_status(job, today)
    now = _now()

    sets = ["user_status = ?", "status_updated_at = ?"]
    params: list = [status, now]
    if status == "submitted":
        sets += ["submitted_at = ?", "responded_at = NULL"]
        params.append(_normalize_date(submitted_at) if submitted_at else now)
    elif status in RESPONSE_STATUSES:
        sets.append("responded_at = ?")
        params.append(now)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE url = ?", (*params, url))
    job.update(user_status=status)
    after = effective_status(job, today)
    conn.execute(
        "INSERT INTO status_events (id, url, from_status, to_status, at, source) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, url, before, status if status is not None else after, now, source),
    )
    conn.commit()
    return {"url": url, "from_status": before, "to_status": after, "at": now}


def set_submitted_at(conn, url: str, submitted_at: str) -> str:
    """Edit the submitted date only (no status change, no event). Returns the stored value."""
    value = _normalize_date(submitted_at)
    conn.execute("UPDATE jobs SET submitted_at = ? WHERE url = ?", (value, url))
    conn.commit()
    return value


def status_events(conn, url: str) -> list[dict]:
    """The job's status history, newest first."""
    rows = conn.execute(
        "SELECT from_status, to_status, at, source FROM status_events WHERE url = ? ORDER BY at DESC, id",
        (url,),
    ).fetchall()
    return [dict(r) for r in rows]
