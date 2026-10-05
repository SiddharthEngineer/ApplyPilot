"""FastAPI app for the job dashboard. Every route lives under /app, which nginx proxies unchanged.

  /app/api/...   JSON API
  /app/...       the built UI (index.html + assets), with unknown paths falling back to index.html

Mutating requests need the header `X-ApplyPilot: 1`. nginx puts basic auth in front, and browsers resend
basic-auth credentials on cross-site form posts, but a cross-site form can't set a custom header.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from applypilot import __version__, tracking
from applypilot.database import Connection, backend_name, backfill_job_keys, get_connection, init_db, job_company
from applypilot.web import queries

PREFIX = "/app"
CSRF_HEADER = "X-ApplyPilot"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

api = APIRouter(prefix=f"{PREFIX}/api")


def db_conn(request: Request) -> Connection:
    """This thread's connection to the app's database (each threadpool worker gets its own)."""
    return get_connection(request.app.state.db)


@api.get("/health")
def health(request: Request) -> dict:
    conn = db_conn(request)
    conn.execute("SELECT 1").fetchone()
    return {"ok": True, "db": backend_name(conn), "version": __version__}


def today() -> date:
    """The dashboard's "today" for deadlines and day counts (UTC, matching the stored timestamps)."""
    return datetime.now(UTC).date()


@api.get("/jobs")
def list_jobs(
    request: Request,
    status: Annotated[list[queries.StatusFilter], Query()] = [],  # noqa: B006 -- FastAPI copies defaults
    role: Annotated[list[str], Query()] = [],  # noqa: B006
    work_mode: Annotated[list[str], Query()] = [],  # noqa: B006
    location: str | None = None,
    found_from: date | None = None,
    found_to: date | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    score_min: Annotated[int | None, Query(ge=0, le=10)] = None,
    score_max: Annotated[int | None, Query(ge=0, le=10)] = None,
    q: str | None = None,
    sort: Annotated[str, Query(pattern=queries.SORT_PATTERN)] = "-discovered_at",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
) -> dict:
    conn = db_conn(request)
    backfill_job_keys(conn)
    filters = queries.JobFilters(
        status=list(status), role=role, work_mode=work_mode, location=location,
        found_from=found_from, found_to=found_to, due_from=due_from, due_to=due_to,
        score_min=score_min, score_max=score_max, q=q,
    )
    return queries.list_jobs(conn, filters, sort=sort, page=page, page_size=page_size, today=today())


@api.get("/facets")
def facets(request: Request) -> dict:
    return queries.facets(db_conn(request), today())


# Large raw columns left out of the detail response (the description comes back once, as description_text).
_RAW_COLUMNS = ("full_description", "description", "details_json")


def job_by_key(conn: Connection, key: str) -> dict:
    """The job row for a dashboard key, or 404."""
    row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()
    if row is None:
        backfill_job_keys(conn)  # a row discovered since the last list request
        row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row)


def job_detail(conn: Connection, job: dict) -> dict:
    day = today()
    status = tracking.effective_status(job, day)
    try:
        details = json.loads(job["details_json"]) if job.get("details_json") else None
    except ValueError:
        details = None
    out = {k: v for k, v in job.items() if k not in _RAW_COLUMNS}
    out.update(
        key=job["job_key"],
        company=job_company(job),
        description_text=job.get("full_description") or job.get("description"),
        details=details,
        status=status,
        status_color=tracking.COLORS[status],
        days_since_submitted=tracking.days_since_submitted(job, day),
        submitted_date=tracking.submitted_date(job),
        events=tracking.status_events(conn, job["url"]),
        links={
            "posting": job["url"],
            "apply": job.get("application_url"),
            "drive_folder": job.get("drive_folder_url"),
            "resume": job.get("resume_drive_url"),
            "cover_letter": job.get("cover_letter_drive_url"),
        },
    )
    return out


@api.get("/jobs/{key}")
def get_job(key: str, request: Request) -> dict:
    conn = db_conn(request)
    return job_detail(conn, job_by_key(conn, key))


class StatusUpdate(BaseModel):
    status: Literal["in_progress", "submitted", "rejected", "heard_back"] | None
    submitted_at: date | None = None


@api.post("/jobs/{key}/status")
def post_status(key: str, body: StatusUpdate, request: Request) -> dict:
    conn = db_conn(request)
    job = job_by_key(conn, key)
    submitted_at = body.submitted_at.isoformat() if body.submitted_at else None
    tracking.set_status(conn, job["url"], body.status, submitted_at=submitted_at)
    return job_detail(conn, job_by_key(conn, key))


class JobPatch(BaseModel):
    submitted_at: date | None = None
    notes: str | None = None


@api.patch("/jobs/{key}")
def patch_job(key: str, body: JobPatch, request: Request) -> dict:
    """Edit the submitted date and/or notes. Only the fields present in the body change."""
    conn = db_conn(request)
    job = job_by_key(conn, key)
    if "submitted_at" in body.model_fields_set:
        if body.submitted_at is None:
            raise HTTPException(status_code=422, detail="submitted_at can't be cleared; set a status instead")
        tracking.set_submitted_at(conn, job["url"], body.submitted_at.isoformat())
    if "notes" in body.model_fields_set:
        conn.execute("UPDATE jobs SET notes = ? WHERE url = ?", (body.notes, job["url"]))
        conn.commit()
    return job_detail(conn, job_by_key(conn, key))


def _default_static_dir() -> Path | None:
    value = os.environ.get("APPLYPILOT_WEB_DIR")
    return Path(value) if value else None


def create_app(static_dir: Path | str | None = None, db: Path | str | None = None) -> FastAPI:
    """The dashboard app.

    static_dir: the built UI (default: $APPLYPILOT_WEB_DIR); without it only the API is served.
    db: a SQLite path or database URL (default: APPLYPILOT_DATABASE_URL, else the SQLite DB_PATH).
    """
    app = FastAPI(title="ApplyPilot dashboard", version=__version__,
                  docs_url=f"{PREFIX}/api/docs", openapi_url=f"{PREFIX}/api/openapi.json", redoc_url=None)
    app.state.db = db
    init_db(db)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Missing {CSRF_HEADER}: 1 header"}, status_code=403)
        return await call_next(request)

    app.include_router(api)
    _mount_ui(app, Path(static_dir) if static_dir else _default_static_dir())
    return app


def _mount_ui(app: FastAPI, static_dir: Path | None) -> None:
    root = static_dir.resolve() if static_dir else None

    @app.get(PREFIX, include_in_schema=False)
    def ui_root_redirect():
        return RedirectResponse(f"{PREFIX}/")

    @app.get(PREFIX + "/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if root is None or not (root / "index.html").is_file():
            return JSONResponse({"detail": "Dashboard UI not built (set APPLYPILOT_WEB_DIR)"}, status_code=404)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            if path.startswith("assets/"):  # a missing asset is a real 404, not the SPA page
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")
