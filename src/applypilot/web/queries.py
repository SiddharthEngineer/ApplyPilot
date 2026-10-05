"""SQL for the dashboard's jobs list and filter facets.

Only whitelisted column names are ever formatted into SQL; every value is a bound parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from applypilot.database import Connection, job_company
from applypilot.tracking import COLORS, STATUSES, days_since_submitted, effective_status, status_sql

SORT_COLUMNS = {
    "discovered_at": "discovered_at",
    "deadline": "NULLIF(deadline, '')",
    "fit_score": "fit_score",
}
SORT_PATTERN = r"^-?(" + "|".join(SORT_COLUMNS) + r")$"
STATUS_FILTERS = (*STATUSES, "no_response", "all")
StatusFilter = Literal["active", "inactive", "in_progress", "submitted", "rejected", "heard_back", "no_response", "all"]
MAX_PAGE_SIZE = 200

_LIST_COLUMNS = (
    "url", "job_key", "title", "company", "site", "role_category", "location", "work_mode", "fit_score",
    "discovered_at", "deadline", "user_status", "apply_status", "applied_at", "submitted_at", "responded_at",
)


@dataclass
class JobFilters:
    status: list[str] = field(default_factory=list)
    role: list[str] = field(default_factory=list)
    work_mode: list[str] = field(default_factory=list)
    location: str | None = None
    found_from: date | None = None
    found_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    score_min: int | None = None
    score_max: int | None = None
    q: str | None = None


def _like(text: str) -> str:
    """A case-insensitive substring pattern for `LOWER(col) LIKE ? ESCAPE '!'`."""
    escaped = text.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _in(column: str, values: list[str], params: list) -> str:
    params.extend(values)
    return f"{column} IN ({', '.join('?' * len(values))})"


def where_clause(f: JobFilters, today: date) -> tuple[str, list]:
    """The WHERE condition and its parameters for a set of filters."""
    conds: list[str] = []
    params: list = []
    status_expr = status_sql(today.isoformat())

    statuses = [s for s in f.status if s != "all"]
    if statuses and "all" not in f.status:
        parts = []
        plain = [s for s in statuses if s != "no_response"]
        if plain:
            parts.append(_in(status_expr, plain, params))
        if "no_response" in statuses:
            parts.append(f"({status_expr} = 'submitted' AND responded_at IS NULL)")
        conds.append("(" + " OR ".join(parts) + ")")
    if f.role:
        conds.append(_in("role_category", f.role, params))
    if f.work_mode:
        conds.append(_in("work_mode", f.work_mode, params))
    if f.location and f.location.strip():
        loc = f.location.strip()
        cols = ("location", "location_city", "location_state")
        parts = [f"LOWER(COALESCE({c}, '')) LIKE ? ESCAPE '!'" for c in cols]
        params.extend([_like(loc)] * len(cols))
        if loc.lower() == "remote":
            parts.append("work_mode = 'remote'")
        conds.append("(" + " OR ".join(parts) + ")")
    if f.found_from:
        conds.append("discovered_at >= ?")
        params.append(f.found_from.isoformat())
    if f.found_to:
        conds.append("discovered_at < ?")  # through the end of found_to
        params.append((f.found_to + timedelta(days=1)).isoformat())
    if f.due_from:
        conds.append("NULLIF(deadline, '') >= ?")
        params.append(f.due_from.isoformat())
    if f.due_to:
        conds.append("NULLIF(deadline, '') <= ?")
        params.append(f.due_to.isoformat())
    if f.score_min is not None:
        conds.append("fit_score >= ?")
        params.append(f.score_min)
    if f.score_max is not None:
        conds.append("fit_score <= ?")
        params.append(f.score_max)
    if f.q and f.q.strip():
        cols = ("title", "company", "site")
        conds.append("(" + " OR ".join(f"LOWER(COALESCE({c}, '')) LIKE ? ESCAPE '!'" for c in cols) + ")")
        params.extend([_like(f.q.strip())] * len(cols))
    return (" AND ".join(conds) or "1=1"), params


def order_clause(sort: str) -> str:
    """ORDER BY for a whitelisted sort key (`-` prefix = descending). NULLs always sort last."""
    desc = sort.startswith("-")
    name = sort.lstrip("-")
    if name not in SORT_COLUMNS:
        raise ValueError(f"Unknown sort {sort!r}")
    return f"{SORT_COLUMNS[name]} {'DESC' if desc else 'ASC'} NULLS LAST, url ASC"


def list_item(row: dict, today: date) -> dict:
    status = effective_status(row, today)
    return {
        "key": row["job_key"],
        "title": row["title"],
        "company": job_company(row),
        "role_category": row["role_category"],
        "location": row["location"],
        "work_mode": row["work_mode"],
        "fit_score": row["fit_score"],
        "discovered_at": row["discovered_at"],
        "deadline": row["deadline"] or None,
        "status": status,
        "status_color": COLORS[status],
        "days_since_submitted": days_since_submitted(row, today),
    }


def list_jobs(conn: Connection, filters: JobFilters, *, sort: str = "-discovered_at", page: int = 1,
              page_size: int = 50, today: date) -> dict:
    where, params = where_clause(filters, today)
    total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params).fetchone()[0]
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    rows = conn.execute(
        f"SELECT {', '.join(_LIST_COLUMNS)} FROM jobs WHERE {where} ORDER BY {order_clause(sort)} LIMIT ? OFFSET ?",
        [*params, page_size, (max(page, 1) - 1) * page_size],
    ).fetchall()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [list_item(dict(r), today) for r in rows]}


def facets(conn: Connection, today: date, top_locations: int = 30) -> dict:
    """Counts for the filter dropdowns: per status (plus no_response), role_category, work_mode; top locations."""
    status_expr = status_sql(today.isoformat())
    by_status = {s: 0 for s in STATUSES}
    for st, n in conn.execute(f"SELECT {status_expr} AS st, COUNT(*) FROM jobs GROUP BY st").fetchall():
        by_status[st] = n
    by_status["no_response"] = conn.execute(
        f"SELECT COUNT(*) FROM jobs WHERE {status_expr} = 'submitted' AND responded_at IS NULL"
    ).fetchone()[0]

    def counts(column: str) -> list[dict]:
        rows = conn.execute(
            f"SELECT {column}, COUNT(*) AS n FROM jobs WHERE {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY n DESC, {column}"
        ).fetchall()
        return [{"value": r[0], "count": r[1]} for r in rows]

    locations = counts("location")[:top_locations]
    return {
        "total": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "status": [{"value": s, "count": by_status[s], "color": COLORS.get(s)} for s in (*STATUSES, "no_response")],
        "role_category": counts("role_category"),
        "work_mode": counts("work_mode"),
        "location": locations,
    }
