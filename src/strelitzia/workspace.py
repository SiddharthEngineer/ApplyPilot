"""A user's workspace around a job: contacts (shared across that user's jobs) and interviews.

Plain functions over a `Connection` that return dicts. Every function takes the `user_id` it acts for and only sees
that user's rows: another user's contact, interview or job is `NotFound`, like a missing one. Field names are
allow-listed, so callers can pass request bodies straight through. All SQL runs on SQLite and Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from strelitzia.database import Connection

CONTACT_FIELDS = ("name", "company", "role", "email", "phone", "linkedin_url", "notes")
RELATIONS = ("recruiter", "hiring_manager", "referral", "interviewer", "other")
INTERVIEW_FIELDS = ("scheduled_at", "stage", "format", "interviewers", "prep_notes", "debrief_notes", "outcome")
OUTCOMES = ("pending", "passed", "rejected", "withdrew")


class NotFound(LookupError):
    """No contact, interview or job with that id for this user."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pick(fields: dict, allowed: tuple[str, ...]) -> dict:
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    return dict(fields)


def _row(conn: Connection, user_id: str, table: str, id: str) -> dict:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (id, user_id)).fetchone()
    if row is None:
        raise NotFound(id)
    return dict(row)


def _insert(conn: Connection, table: str, values: dict) -> None:
    names = ", ".join(values)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))


def _check_job(conn: Connection, user_id: str, job_url: str) -> None:
    if not conn.execute("SELECT 1 FROM jobs WHERE user_id = ? AND url = ?", (user_id, job_url)).fetchone():
        raise NotFound(job_url)


def _update(conn: Connection, user_id: str, table: str, id: str, fields: dict) -> dict:
    _row(conn, user_id, table, id)
    values = {**fields, "updated_at": _now()}
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ? AND user_id = ?", (*values.values(), id, user_id))
    conn.commit()
    return _row(conn, user_id, table, id)


# --- Contacts ---

def create_contact(conn: Connection, user_id: str, **fields) -> dict:
    fields = _pick(fields, CONTACT_FIELDS)
    if not (fields.get("name") or "").strip():
        raise ValueError("A contact needs a name")
    now = _now()
    contact_id = uuid.uuid4().hex
    _insert(conn, "contacts", {**fields, "id": contact_id, "user_id": user_id, "created_at": now, "updated_at": now})
    conn.commit()
    return get_contact(conn, user_id, contact_id)


def get_contact(conn: Connection, user_id: str, id: str) -> dict:
    return _row(conn, user_id, "contacts", id)


def update_contact(conn: Connection, user_id: str, id: str, **fields) -> dict:
    fields = _pick(fields, CONTACT_FIELDS)
    if "name" in fields and not (fields["name"] or "").strip():
        raise ValueError("A contact needs a name")
    return _update(conn, user_id, "contacts", id, fields)


def delete_contact(conn: Connection, user_id: str, id: str) -> None:
    """Delete a contact and its links to jobs."""
    _row(conn, user_id, "contacts", id)
    conn.execute("DELETE FROM job_contacts WHERE contact_id = ? AND user_id = ?", (id, user_id))
    conn.execute("DELETE FROM contacts WHERE id = ? AND user_id = ?", (id, user_id))
    conn.commit()


def search_contacts(conn: Connection, user_id: str, q: str | None, limit: int = 20) -> list[dict]:
    """Contacts whose name, company or email contains `q` (case-insensitive), by name. Each has `job_count`."""
    sql = ("SELECT c.*, (SELECT COUNT(*) FROM job_contacts jc WHERE jc.contact_id = c.id "
           "AND jc.user_id = c.user_id) AS job_count FROM contacts c WHERE c.user_id = ?")
    params: list = [user_id]
    if q and q.strip():
        escaped = q.strip().lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        like = f"%{escaped}%"
        sql += (" AND (LOWER(c.name) LIKE ? ESCAPE '!' OR LOWER(COALESCE(c.company, '')) LIKE ? ESCAPE '!' "
                "OR LOWER(COALESCE(c.email, '')) LIKE ? ESCAPE '!')")
        params += [like, like, like]
    sql += " ORDER BY LOWER(c.name), c.id LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def link_contact(conn: Connection, user_id: str, job_url: str, contact_id: str, relation: str) -> None:
    """Link a contact to a job (or change the relation of an existing link). Both must be this user's."""
    if relation not in RELATIONS:
        raise ValueError(f"Unknown relation {relation!r}")
    _row(conn, user_id, "contacts", contact_id)
    _check_job(conn, user_id, job_url)
    conn.execute("DELETE FROM job_contacts WHERE job_url = ? AND contact_id = ?", (job_url, contact_id))
    conn.execute("INSERT INTO job_contacts (job_url, contact_id, relation, user_id) VALUES (?, ?, ?, ?)",
                 (job_url, contact_id, relation, user_id))
    conn.commit()


def unlink_contact(conn: Connection, user_id: str, job_url: str, contact_id: str) -> None:
    conn.execute("DELETE FROM job_contacts WHERE job_url = ? AND contact_id = ? AND user_id = ?",
                 (job_url, contact_id, user_id))
    conn.commit()


def job_contacts(conn: Connection, user_id: str, job_url: str) -> list[dict]:
    """The contacts linked to a job, each with its `relation`, by name."""
    rows = conn.execute(
        "SELECT c.*, jc.relation FROM job_contacts jc JOIN contacts c ON c.id = jc.contact_id "
        "AND c.user_id = jc.user_id WHERE jc.job_url = ? AND jc.user_id = ? ORDER BY LOWER(c.name), c.id",
        (job_url, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


def contact_jobs(conn: Connection, user_id: str, contact_id: str) -> list[dict]:
    """The jobs a contact is linked to: key, title, company, relation."""
    rows = conn.execute(
        "SELECT j.job_key AS key, j.title, j.company, j.site, jc.relation FROM job_contacts jc "
        "JOIN jobs j ON j.url = jc.job_url AND j.user_id = jc.user_id "
        "WHERE jc.contact_id = ? AND jc.user_id = ? ORDER BY j.title, j.url",
        (contact_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Interviews ---

def _check_interview(fields: dict) -> dict:
    fields = _pick(fields, INTERVIEW_FIELDS)
    if fields.get("outcome") not in (None, *OUTCOMES):
        raise ValueError(f"Unknown outcome {fields['outcome']!r}")
    return fields


def create_interview(conn: Connection, user_id: str, job_url: str, **fields) -> dict:
    fields = _check_interview(fields)
    _check_job(conn, user_id, job_url)
    now = _now()
    interview_id = uuid.uuid4().hex
    _insert(conn, "interviews", {"outcome": "pending", **fields, "id": interview_id, "job_url": job_url,
                                 "user_id": user_id, "created_at": now, "updated_at": now})
    conn.commit()
    return get_interview(conn, user_id, interview_id)


def get_interview(conn: Connection, user_id: str, id: str) -> dict:
    return _row(conn, user_id, "interviews", id)


def update_interview(conn: Connection, user_id: str, id: str, **fields) -> dict:
    return _update(conn, user_id, "interviews", id, _check_interview(fields))


def delete_interview(conn: Connection, user_id: str, id: str) -> None:
    _row(conn, user_id, "interviews", id)
    conn.execute("DELETE FROM interviews WHERE id = ? AND user_id = ?", (id, user_id))
    conn.commit()


def job_interviews(conn: Connection, user_id: str, job_url: str) -> list[dict]:
    """A job's interviews by date (undated ones last)."""
    rows = conn.execute(
        "SELECT * FROM interviews WHERE job_url = ? AND user_id = ? "
        "ORDER BY CASE WHEN scheduled_at IS NULL THEN 1 ELSE 0 END, scheduled_at, created_at",
        (job_url, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


def next_interview_sql(now: datetime | None = None) -> str:
    """A correlated subquery for a `jobs` row: its user's earliest interview scheduled at or after now (else NULL).

    `scheduled_at` is an ISO string (UTC from the browser), so the comparison is on text; `now` is inlined
    because it is generated here, never user input.
    """
    cutoff = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return (f"(SELECT MIN(i.scheduled_at) FROM interviews i WHERE i.job_url = jobs.url "
            f"AND i.user_id = jobs.user_id AND i.scheduled_at >= '{cutoff}')")
