"""FastAPI app for the job dashboard. Every route lives under /app, which nginx proxies unchanged.

  /app/api/...   JSON API
  /app/...       the built UI (index.html + assets), with unknown paths falling back to index.html

Mutating requests need the header `X-Strelitzia: 1`. nginx puts basic auth in front, and browsers resend
basic-auth credentials (and the session cookie) on cross-site form posts, but a cross-site form can't set a custom header.

With Google sign-in on (STRELITZIA_GOOGLE_CLIENT_ID set, see web/auth.py) every /app/api route except health and
auth/* needs a session; the UI routes stay public so the SPA can show its login screen.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from strelitzia import __version__, intake, tracking, users, workspace
from strelitzia import profile as profile_store
from strelitzia.config import env
from strelitzia.database import Connection, backend_name, backfill_job_keys, get_connection, init_db, job_company
from strelitzia.llm import LLMStopRun, check_budget, month_spend, user_month_spend
from strelitzia.scoring import panel
from strelitzia.scoring import preferences as prefs_mod
from strelitzia.scoring import resume_parse, workbench
from strelitzia.scoring.assessment import current_assessment_key, recompute_fit
from strelitzia.scoring.content_library import parse_content_library_text
from strelitzia.web import admin, auth, queries, tasks

PREFIX = "/app"
CSRF_HEADER = "X-Strelitzia"
LEGACY_CSRF_HEADER = "X-ApplyPilot"  # sent by dashboard bundles from before the rename (open tabs after a deploy)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

api = APIRouter(prefix=f"{PREFIX}/api")
log = logging.getLogger(__name__)


def db_conn(request: Request) -> Connection:
    """This thread's connection to the app's database (each threadpool worker gets its own)."""
    return get_connection(request.app.state.db)


current_user = auth.current_user


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
    score_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    score_max: Annotated[int | None, Query(ge=0, le=100)] = None,
    hide_flagged: bool = True,
    source: Annotated[list[queries.SourceFilter], Query()] = [],  # noqa: B006
    q: str | None = None,
    sort: Annotated[str, Query(pattern=queries.SORT_PATTERN)] = "-discovered_at",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    backfill_job_keys(conn)
    filters = queries.JobFilters(
        status=list(status), role=role, work_mode=work_mode, location=location,
        found_from=found_from, found_to=found_to, due_from=due_from, due_to=due_to,
        score_min=score_min, score_max=score_max, q=q, hide_flagged=hide_flagged, source=list(source),
    )
    return queries.list_jobs(conn, user.id, filters, sort=sort, page=page, page_size=page_size, today=today(),
                             current_key=current_assessment_key(conn, user_id=user.id))


@api.get("/facets")
def facets(request: Request) -> dict:
    return queries.facets(db_conn(request), current_user(request).id, today())


# Large raw columns left out of the detail response (the description comes back once, as description_text).
_RAW_COLUMNS = ("full_description", "description", "details_json", "fit_assessment")


def job_by_key(conn: Connection, user_id: str, key: str) -> dict:
    """The user's job row for a dashboard key, or 404. Two users' rows for one posting share the key."""
    sql = "SELECT * FROM jobs WHERE user_id = ? AND job_key = ?"
    row = conn.execute(sql, (user_id, key)).fetchone()
    if row is None:
        backfill_job_keys(conn)  # a row discovered since the last list request
        row = conn.execute(sql, (user_id, key)).fetchone()
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
    out.update(queries.fit_fields(job, current_assessment_key(conn, user_id=job["user_id"])))
    out.update(
        key=job["job_key"],
        fit_assessment=queries._json(job.get("fit_assessment"), None),
        company=job_company(job),
        description_text=job.get("full_description") or job.get("description"),
        details=details,
        status=status,
        status_color=tracking.COLORS[status],
        days_since_submitted=tracking.days_since_submitted(job, day),
        submitted_date=tracking.submitted_date(job),
        events=tracking.status_events(conn, job["user_id"], job["url"]),
        source=job.get("source") or "discovered",
        contacts=workspace.job_contacts(conn, job["user_id"], job["url"]),
        interviews=workspace.job_interviews(conn, job["user_id"], job["url"]),
        links={
            "posting": job["url"],
            "apply": job.get("application_url"),
            "drive_folder": job.get("drive_folder_url"),
            "resume": job.get("resume_drive_url"),
            "cover_letter": job.get("cover_letter_drive_url"),
            "packet": job.get("packet_doc_url"),
            **_file_links(job),
        },
        **_draft_fields(job),
    )
    return out


# Local draft files served by GET /jobs/{key}/files/{kind}: kind -> jobs column.
_FILE_COLUMNS = {"resume_pdf": "tailored_resume_path", "resume_docx": "resume_docx_path",
                 "cover_letter_pdf": "cover_letter_path"}
_FILE_SUFFIX = {"resume_pdf": ".pdf", "resume_docx": ".docx", "cover_letter_pdf": ".pdf"}


def _local_file(job: dict, kind: str) -> Path | None:
    path = job.get(_FILE_COLUMNS[kind])
    if not path:
        return None
    p = Path(path).with_suffix(_FILE_SUFFIX[kind])
    return p if p.is_file() else None


def _file_links(job: dict) -> dict:
    """{kind: API URL} for each draft file still on disk (PDFs moved to Drive are linked as resume/cover_letter)."""
    return {kind: f"{PREFIX}/api/jobs/{job['job_key']}/files/{kind}"
            for kind in _FILE_COLUMNS if _local_file(job, kind)}


def _draft_fields(job: dict) -> dict:
    report = workbench.tailor_report(job)
    return {
        "draft_version": job.get("draft_version"),
        "draft_rules_version": job.get("draft_rules_version"),
        "coverage": report.get("coverage"),
        "unmet": report.get("unmet") or [],
        "warnings": report.get("warnings") or [],
        "notes_for_user": report.get("notes_for_user") or "",
        "draft_history": workbench.draft_history(job),
        "packet_doc_url": job.get("packet_doc_url"),
    }


@api.get("/jobs/{key}")
def get_job(key: str, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    return job_detail(conn, job_by_key(conn, user.id, key))


class StatusUpdate(BaseModel):
    status: Literal["in_progress", "submitted", "rejected", "heard_back"] | None
    submitted_at: date | None = None


@api.post("/jobs/{key}/status")
def post_status(key: str, body: StatusUpdate, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    submitted_at = body.submitted_at.isoformat() if body.submitted_at else None
    tracking.set_status(conn, user.id, job["url"], body.status, submitted_at=submitted_at)
    return job_detail(conn, job_by_key(conn, user.id, key))


class JobPatch(BaseModel):
    submitted_at: date | None = None
    notes: str | None = None


@api.patch("/jobs/{key}")
def patch_job(key: str, body: JobPatch, request: Request) -> dict:
    """Edit the submitted date and/or notes. Only the fields present in the body change."""
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    if "submitted_at" in body.model_fields_set:
        if body.submitted_at is None:
            raise HTTPException(status_code=422, detail="submitted_at can't be cleared; set a status instead")
        tracking.set_submitted_at(conn, user.id, job["url"], body.submitted_at.isoformat())
    if "notes" in body.model_fields_set:
        conn.execute("UPDATE jobs SET notes = ? WHERE user_id = ? AND url = ?", (body.notes, user.id, job["url"]))
        conn.commit()
    return job_detail(conn, job_by_key(conn, user.id, key))


@api.get("/jobs/{key}/files/{kind}")
def get_job_file(key: str, kind: Literal["resume_pdf", "resume_docx", "cover_letter_pdf"], request: Request):
    job = job_by_key(db_conn(request), current_user(request).id, key)
    path = _local_file(job, kind)
    if path is None:
        raise HTTPException(status_code=404, detail="No such file on this server")
    return FileResponse(path, filename=path.name)


def _owner_content(user: users.User) -> None:
    """The workbench reads the owner's content library and resume; other accounts get them in #48."""
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="The resume workbench isn't available for your account yet.")


def _workbench_inputs(request: Request) -> workbench.Inputs:
    try:
        return workbench.Inputs.for_user(db_conn(request), current_user(request).id)
    except workbench.NoContentLibrary as e:
        raise HTTPException(status_code=409, detail=f"{e}. Add your content library on the Profile page.") from e


def _drive_state(conn, user: users.User) -> tuple[str, str, str | None]:
    """(state, detail, source) of the user's Drive: source "user" (their connected token), "legacy" (the owner, while
    this server can't connect Drive from the dashboard: the CLI's token file) or None when it isn't authorized."""
    from strelitzia.storage.drive import drive_status, user_drive_status

    state, detail = user_drive_status(conn, user.id)
    if state == "authorized":
        return state, detail, "user"
    if user.role == "owner" and state != "not_installed" and not auth.drive_connect_available():
        legacy_state, legacy_detail = drive_status()
        if legacy_state == "authorized":
            return legacy_state, legacy_detail, "legacy"
    return state, detail, None


def _drive_client(request: Request):
    """A client on the current user's Drive when they've connected it, else None (the workbench uses the clipboard)."""
    from strelitzia.storage.drive import DriveClient

    conn = db_conn(request)
    user = current_user(request)
    source = _drive_state(conn, user)[2]
    if source == "user":
        return DriveClient.for_user(conn, user.id)
    if source == "legacy":
        return DriveClient.from_credentials()
    return None


@api.post("/jobs/{key}/packet")
def post_packet(key: str, request: Request) -> dict:
    """Write the job's Claude packet (a Drive Doc when Drive is set up) and return the prompt to paste in Claude."""
    conn = db_conn(request)
    user = current_user(request)
    _owner_content(user)
    job = job_by_key(conn, user.id, key)
    inputs = _workbench_inputs(request)
    drive_error = None
    try:
        client = _drive_client(request)
        if client is not None:
            workbench.publish_static_docs(client, conn, user.id, inputs)
            _, url = workbench.publish_packet(client, conn, job, inputs)
            packet = workbench.packet_for(job, inputs, inline=False)
            return {"prompt": packet.prompt, "doc_url": url, "inline": False, "rules_version": workbench.RULES_VERSION}
    except Exception as e:  # noqa: BLE001 -- Drive trouble shouldn't block the clipboard flow
        log.warning("Drive packet failed for %s: %s", key, e)
        drive_error = f"Couldn't write the packet to Drive ({e}); the full packet was copied instead."
    packet = workbench.packet_for(job, inputs, inline=True)
    return {"prompt": packet.prompt, "doc_url": None, "inline": True, "rules_version": workbench.RULES_VERSION,
            "drive_error": drive_error}


class PastedResult(BaseModel):
    text: str = Field(max_length=200_000)


@api.post("/jobs/{key}/result")
def post_result(key: str, body: PastedResult, request: Request):
    """Check a pasted Claude reply. 422 with errors and a fix message, or 200 with the saved draft and the job."""
    conn = db_conn(request)
    user = current_user(request)
    _owner_content(user)
    job = job_by_key(conn, user.id, key)
    outcome = workbench.import_result(conn, job, body.text, _workbench_inputs(request))
    if not outcome.ok:
        return JSONResponse({"detail": outcome.errors[0] if outcome.errors else "The result didn't pass the checks",
                             "errors": outcome.errors, "warnings": outcome.warnings,
                             "fix_prompt": outcome.fix_prompt}, status_code=422)
    warnings = list(outcome.warnings)
    try:
        client = _drive_client(request)
        if client is not None:
            from strelitzia.storage.sync import sync_job

            sync_job(conn, client, job_by_key(conn, user.id, key))
    except Exception as e:  # noqa: BLE001 -- the files are saved locally
        log.warning("Drive upload failed for %s: %s", key, e)
        warnings.append(f"Saved, but the Drive upload failed: {e}")
    return {"outcome": {"draft_version": outcome.draft_version, "warnings": warnings,
                        "coverage": outcome.coverage, "unmet": outcome.unmet},
            "job": job_detail(conn, job_by_key(conn, user.id, key))}


@api.get("/claude/setup")
def claude_setup(request: Request) -> dict:
    """What the Claude setup page shows: the Project Docs, the rules version, Drive status, the instructions."""
    user = current_user(request)
    _owner_content(user)
    conn = db_conn(request)
    state, detail, source = _drive_state(conn, user)
    docs = workbench.published_static_docs(conn, user.id)
    return {
        "drive": state == "authorized", "drive_detail": detail, "drive_source": source,
        "connect_url": f"{auth.DRIVE_PREFIX}/connect" if auth.drive_connect_available() else None,
        "rules_version": workbench.RULES_VERSION,
        "docs": [{"kind": kind, "name": name, "url": (docs.get(kind) or {}).get("url"),
                  "changed_at": (docs.get(kind) or {}).get("changed_at")}
                 for kind, name in workbench.STATIC_DOCS.items() if kind in workbench.RESUME_DOCS],
        "project_name": "Strelitzia Resumes",
        "project_instructions": workbench.PROJECT_INSTRUCTIONS,
    }


@api.post("/claude/docs")
def publish_claude_docs(request: Request) -> dict:
    """Write (or refresh) the rules and content-library Docs in Drive."""
    user = current_user(request)
    _owner_content(user)
    try:
        client = _drive_client(request)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(e)) from e
    if client is None:
        raise HTTPException(status_code=409, detail="Google Drive isn't set up. Connect Google Drive on the Claude setup page.")
    workbench.publish_static_docs(client, db_conn(request), user.id, _workbench_inputs(request), force=True)
    return claude_setup(request)


# --- Resume panel (strelitzia.scoring.panel) ---

@api.get("/panel")
def get_panel(request: Request) -> dict:
    """The Panel page: whether a panel is due, how many jobs can be compared, the runs, and the project setup."""
    conn = db_conn(request)
    user = current_user(request)
    rubric = workbench.published_static_docs(conn, user.id).get("panel_rubric") or {}
    return {
        "due": panel.panel_due(conn, user.id),
        "needed": panel.DEFAULT_ITEMS,
        "comparable": {mode: panel.comparable_count(conn, user.id, mode) for mode in panel.MODES},
        "runs": panel.list_runs(conn, user.id),
        "project_name": panel.PROJECT_NAME,
        "project_instructions": panel.PROJECT_INSTRUCTIONS,
        "rubric": {"name": workbench.STATIC_DOCS["panel_rubric"], "url": rubric.get("url"),
                   "text": panel.rubric_text()},
    }


class PanelRunCreate(BaseModel):
    n: int = Field(default=panel.DEFAULT_ITEMS, ge=1, le=20)
    mode: Literal["latest_vs_previous", "rules_a_vs_b"] = "latest_vs_previous"


def _panel_run(conn: Connection, user_id: str, run_id: str) -> dict:
    """The user's run for the page. The report (which un-blinds the items) is only sent once you've picked every item."""
    try:
        run = panel.get_run(conn, user_id, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Panel run not found") from None
    items = panel.run_items(conn, user_id, run_id)
    graded = panel.grades(conn, user_id, run_id)
    yours = graded.get("you") or {}
    shown = []
    for item in items:
        first, second = (item["draft_a"], item["draft_b"]) if item["shown_first"] == "a" else \
            (item["draft_b"], item["draft_a"])
        shown.append({"item": item["item_no"], "title": item["job"].get("title"), "company": job_company(item["job"]),
                      "first_text": first.get("resume_text") or "", "second_text": second.get("resume_text") or "",
                      "your_pick": (yours.get(item["item_no"]) or {}).get("winner")})
    picked_all = len(yours) == len(items)
    return {
        "id": run["id"], "created_at": run["created_at"], "mode": run["mode"], "status": run["status"],
        "packets": panel.run_packets(run), "items": shown,
        "graded": {g: len(graded.get(g) or {}) for g in panel.GRADERS},
        "report": panel.panel_report(conn, user_id, run_id) if picked_all else None,
    }


@api.post("/panel/runs", status_code=201)
def create_panel_run(body: PanelRunCreate, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    try:
        run_id = panel.create_run(conn, user.id, body.n, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _panel_run(conn, user.id, run_id)


@api.get("/panel/runs/{run_id}")
def get_panel_run(run_id: str, request: Request) -> dict:
    return _panel_run(db_conn(request), current_user(request).id, run_id)


class PanelGrade(BaseModel):
    """A model's pasted reply (grader sonnet | opus, with text), or your pick for one item (grader you)."""
    grader: Literal["sonnet", "opus", "you"]
    text: str | None = Field(default=None, max_length=500_000)
    item: int | None = None
    winner: Literal["first", "second", "tie"] | None = None


@api.post("/panel/runs/{run_id}/grades")
def post_panel_grades(run_id: str, body: PanelGrade, request: Request):
    """422 with the errors when a pasted reply doesn't fit the panel; otherwise the updated run."""
    conn = db_conn(request)
    user = current_user(request)
    try:
        panel.get_run(conn, user.id, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Panel run not found") from None
    if body.grader == "you":
        if body.item is None or body.winner is None:
            raise HTTPException(status_code=422, detail="Your pick needs an item and a winner.")
        try:
            panel.record_pick(conn, user.id, run_id, body.item, body.winner)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Item {body.item} isn't in this panel") from None
        except panel.PicksLocked as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"graded": None, "run": _panel_run(conn, user.id, run_id)}
    outcome = panel.import_grades(conn, user.id, run_id, body.grader, body.text or "")
    if not outcome.ok:
        return JSONResponse({"detail": outcome.errors[0], "errors": outcome.errors}, status_code=422)
    return {"graded": outcome.graded, "run": _panel_run(conn, user.id, run_id)}


def budget_stop(conn: Connection, user_id: str, estimated_usd: float = 0.0) -> None:
    """402 with the month's spend if `estimated_usd` more would pass LLM_MONTHLY_BUDGET_USD or the user's cap."""
    try:
        check_budget(estimated_usd, conn, user_id)
    except LLMStopRun as e:
        # A capped user sees their own spend, not everyone's.
        spend = user_month_spend(conn, user_id) if e.user_id else month_spend(conn)
        raise HTTPException(status_code=402, detail={
            "message": f"{e} {tasks.STOP_MESSAGES['budget']}", "month_spend": round(spend, 2),
            "estimated_usd": round(estimated_usd, 4)}) from e


def _notify(request: Request) -> None:
    runner = request.app.state.runner
    if runner:
        runner.notify()


@api.post("/jobs/{key}/rescore")
def rescore(key: str, request: Request) -> dict:
    """Queue a sync assessment of one job (whatever its state). Poll GET /app/api/tasks/{id}."""
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    budget_stop(conn, user.id)
    task = tasks.enqueue(conn, user.id, job["url"], "rescore")
    _notify(request)
    return {**task, "key": key}


class RescoreAll(BaseModel):
    confirm: bool = False


@api.get("/rescore/preview")
def rescore_all_preview(request: Request) -> dict:
    """How many active (not rejected) jobs Rescore all would re-assess, how many it skips because they aren't
    extracted yet, and the estimated Batch cost. Changes nothing."""
    from strelitzia.scoring.scorer import rescore_preview

    return rescore_preview(db_conn(request), current_user(request).id, "active")


@api.post("/rescore")
def rescore_all(body: RescoreAll, request: Request) -> dict:
    """After the user confirmed the cost, queue re-assessment of every active job, current assessments included.
    Poll GET /app/api/tasks/{id}. A budget stop (402) queues nothing."""
    from strelitzia.scoring.scorer import rescore_preview

    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirm the estimated cost first")
    conn = db_conn(request)
    user = current_user(request)
    preview = rescore_preview(conn, user.id, "active")
    if not preview["jobs"]:
        return {"task": None, **preview}
    # A second Rescore all while one is waiting or running would pay again for every job.
    if running := tasks.active_task(conn, user.id, "rescore_all"):
        return {"task": running, **preview}
    budget_stop(conn, user.id, preview["estimated_usd"])
    task = tasks.enqueue(conn, user.id, None, "rescore_all")
    _notify(request)
    return {"task": task, **preview}


MIN_PASTED_CHARS = 200  # a pasted description shorter than this isn't a whole posting


class AddJobBody(BaseModel):
    url: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None


def _queue_intake(conn: Connection, request: Request, user_id: str, url: str) -> dict:
    """Queue the intake task for the user's job, or return the one already queued or running for it."""
    active = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE user_id = ? AND url = ? AND kind = 'intake' "
        "AND state IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1", (user_id, url)).fetchone()
    if active:
        return tasks.task_dict(active)
    conn.execute("UPDATE jobs SET intake_status = 'queued', intake_error = NULL WHERE user_id = ? AND url = ?",
                 (user_id, url))
    conn.commit()
    task = tasks.enqueue(conn, user_id, url, "intake")
    _notify(request)
    return task


@api.post("/jobs")
def add_job(body: AddJobBody, request: Request) -> JSONResponse:
    """Add a job by link, or by pasting its description. 201 with a queued `intake` task for a new job.

    A link the database already has returns 200 with the same key; it's queued for intake only if it hasn't been
    extracted and scored yet.
    """
    url = (body.url or "").strip() or None
    if not url and not (body.description or "").strip():
        raise HTTPException(status_code=422, detail="Paste a link to the posting, or its description")
    if not url and len(body.description.strip()) < MIN_PASTED_CHARS:
        raise HTTPException(status_code=422, detail=f"Paste the whole description (at least {MIN_PASTED_CHARS} characters)")
    conn = db_conn(request)
    user = current_user(request)
    try:
        existing = intake.find_existing(conn, user.id, url) if url else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if existing is None or intake.needs_intake(existing):
        budget_stop(conn, user.id)
    manual = intake.ManualFields(title=body.title, company=body.company, location=body.location,
                                 description=body.description)
    row, created = intake.add_job(conn, user.id, url, manual)
    task = _queue_intake(conn, request, user.id, row["url"]) if created or intake.needs_intake(row) else None
    return JSONResponse({"key": row["job_key"], "created": created, "task": task},
                        status_code=201 if created else 200)


class PastedDescription(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str = Field(min_length=MIN_PASTED_CHARS)


@api.put("/jobs/{key}/description")
def put_description(key: str, body: PastedDescription, request: Request) -> dict:
    """Save a pasted description (when the posting couldn't be read), then extract and score it."""
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    budget_stop(conn, user.id)
    intake.save_pasted(conn, user.id, job["url"], intake.ManualFields(
        title=body.title, company=body.company, location=body.location, description=body.description))
    return {"task": _queue_intake(conn, request, user.id, job["url"]), "key": key}


# --- Contacts and interviews (strelitzia.workspace) ---

def _check_email(value: str | None) -> str | None:
    if value and "@" not in value:
        raise ValueError("Email must contain @")
    return value


def _check_link(value: str | None, domain: str, label: str) -> str | None:
    """`value` if it's an http(s) link on `domain` (or a subdomain), else a ValueError."""
    if value:
        # Strict on the shape (scheme, "//", no backslash or space) so browsers parse the same host as urlparse.
        url = urlparse(value)
        host = (url.hostname or "").lower()
        shaped = re.match(r"https?://[^\\\s]+$", value, re.IGNORECASE) is not None
        if not shaped or not (host == domain or host.endswith("." + domain)):
            raise ValueError(f"{label} must be an http(s) link on {domain}")
    return value


class ContactPatch(BaseModel):
    name: str | None = None
    company: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None

    @field_validator("company", "role", "email", "phone", "linkedin_url", "notes")
    @classmethod
    def _blank_is_null(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _check_email(value)

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return _check_link(value, "linkedin.com", "LinkedIn")


class ContactCreate(ContactPatch):
    name: str = Field(min_length=1)


Relation = Literal["recruiter", "hiring_manager", "referral", "interviewer", "other"]


class JobContactLink(BaseModel):
    contact_id: str | None = None
    contact: ContactCreate | None = None
    relation: Relation = "other"


class InterviewPatch(BaseModel):
    scheduled_at: str | None = None
    stage: str | None = None
    format: str | None = None
    interviewers: str | None = None
    prep_notes: str | None = None
    debrief_notes: str | None = None
    outcome: Literal["pending", "passed", "rejected", "withdrew"] | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _iso(cls, value: str | None) -> str | None:
        if value:
            try:
                datetime.fromisoformat(value)
            except ValueError as e:
                raise ValueError("scheduled_at must be an ISO date-time") from e
        return value or None


def _workspace_call(fn, *args, **kwargs):
    """Run a workspace function, mapping its errors to 404 / 422."""
    try:
        return fn(*args, **kwargs)
    except workspace.NotFound as e:
        raise HTTPException(status_code=404, detail="Not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@api.get("/contacts")
def list_contacts(request: Request, q: str | None = None,
                  limit: Annotated[int, Query(ge=1, le=500)] = 200) -> list[dict]:
    return workspace.search_contacts(db_conn(request), current_user(request).id, q, limit=limit)


@api.post("/contacts", status_code=201)
def create_contact(body: ContactCreate, request: Request) -> dict:
    return _workspace_call(workspace.create_contact, db_conn(request), current_user(request).id,
                           **body.model_dump(exclude_unset=True))


def _contact_out(conn: Connection, user_id: str, contact_id: str) -> dict:
    contact = _workspace_call(workspace.get_contact, conn, user_id, contact_id)
    return {**contact, "jobs": workspace.contact_jobs(conn, user_id, contact_id)}


@api.get("/contacts/{contact_id}")
def get_contact(contact_id: str, request: Request) -> dict:
    return _contact_out(db_conn(request), current_user(request).id, contact_id)


@api.patch("/contacts/{contact_id}")
def patch_contact(contact_id: str, body: ContactPatch, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    _workspace_call(workspace.update_contact, conn, user.id, contact_id, **body.model_dump(exclude_unset=True))
    return _contact_out(conn, user.id, contact_id)


@api.delete("/contacts/{contact_id}")
def delete_contact(contact_id: str, request: Request) -> dict:
    _workspace_call(workspace.delete_contact, db_conn(request), current_user(request).id, contact_id)
    return {"ok": True}


@api.post("/jobs/{key}/contacts")
def link_job_contact(key: str, body: JobContactLink, request: Request) -> dict:
    """Link a contact to the job: an existing one (`contact_id`) or a new one (`contact`). Returns the job."""
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    if (body.contact_id is None) == (body.contact is None):
        raise HTTPException(status_code=422, detail="Give either contact_id or a new contact")
    if body.contact is None:
        _workspace_call(workspace.link_contact, conn, user.id, job["url"], body.contact_id, body.relation)
    else:
        new = body.contact.model_dump(exclude_unset=True)
        contact_id = _workspace_call(workspace.create_contact, conn, user.id, **new)["id"]
        try:
            workspace.link_contact(conn, user.id, job["url"], contact_id, body.relation)
        except Exception:
            workspace.delete_contact(conn, user.id, contact_id)  # don't leave a contact behind that the user never saw
            raise
    return job_detail(conn, job_by_key(conn, user.id, key))


@api.delete("/jobs/{key}/contacts/{contact_id}")
def unlink_job_contact(key: str, contact_id: str, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    workspace.unlink_contact(conn, user.id, job["url"], contact_id)
    return job_detail(conn, job_by_key(conn, user.id, key))


@api.post("/jobs/{key}/interviews", status_code=201)
def create_interview(key: str, body: InterviewPatch, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    return _workspace_call(workspace.create_interview, conn, user.id, job["url"], **body.model_dump(exclude_unset=True))


@api.patch("/interviews/{interview_id}")
def patch_interview(interview_id: str, body: InterviewPatch, request: Request) -> dict:
    return _workspace_call(workspace.update_interview, db_conn(request), current_user(request).id, interview_id,
                           **body.model_dump(exclude_unset=True))


@api.delete("/interviews/{interview_id}")
def delete_interview(interview_id: str, request: Request) -> dict:
    _workspace_call(workspace.delete_interview, db_conn(request), current_user(request).id, interview_id)
    return {"ok": True}


@api.get("/preferences")
def get_preferences(request: Request) -> dict:
    """The saved preferences, plus the labels and value lists the Preferences page offers."""
    return {
        "prefs": prefs_mod.load_preferences(db_conn(request), current_user(request).id).to_dict(),
        "options": prefs_mod.OPTIONS,
        "labels": prefs_mod.LABELS,
    }


class PrefsBody(BaseModel):
    prefs: dict
    # "active": re-assess every job that isn't rejected, not only the ones these preferences make stale.
    reassess_scope: Literal["stale", "active"] = "stale"


class PrefsSave(PrefsBody):
    confirm_reassess: bool = False


def _preview(conn: Connection, user_id: str, prefs: prefs_mod.Preferences, scope: str = "stale") -> dict:
    from strelitzia.scoring.scorer import reassess_preview, rescore_preview

    if scope == "active":
        active = rescore_preview(conn, user_id, "active", prefs)
        return {"reassess_needed": bool(active["jobs"]), "jobs_to_reassess": active["jobs"],
                "estimated_usd": active["estimated_usd"], "skipped_unextracted": active["skipped_unextracted"]}
    return reassess_preview(conn, user_id, prefs)


@api.post("/preferences/preview")
def preview_preferences(body: PrefsBody, request: Request) -> dict:
    """What saving these preferences would re-assess and roughly cost (Batch pricing), for `reassess_scope`.
    Changes nothing."""
    return _preview(db_conn(request), current_user(request).id, prefs_mod.Preferences.from_dict(body.prefs),
                    body.reassess_scope)


@api.put("/preferences")
def put_preferences(body: PrefsSave, request: Request) -> dict:
    """Save the user's preferences, then re-rank their jobs now (no LLM). With confirm_reassess, also queue
    re-assessment of their stale jobs, or with reassess_scope "active" of every job that isn't rejected.

    Without the confirmation, jobs keep their old assessment and show as stale. A budget stop (402) saves nothing.
    """
    conn = db_conn(request)
    user = current_user(request)
    prefs = prefs_mod.Preferences.from_dict(body.prefs)
    preview = _preview(conn, user.id, prefs, body.reassess_scope) if body.confirm_reassess else None
    reassess = bool(preview and preview["reassess_needed"] and preview["jobs_to_reassess"])
    if reassess:
        budget_stop(conn, user.id, preview["estimated_usd"])
    prefs_mod.save_preferences(conn, prefs, user_id=user.id)
    rescored = recompute_fit(conn, user.id, prefs=prefs)
    task = None
    if reassess:
        kind = "rescore_all" if body.reassess_scope == "active" else "reassess_all"
        task = tasks.enqueue(conn, user.id, None, kind)
        _notify(request)
    return {"prefs": prefs.to_dict(), "rescored": rescored, "task": task}


# --- Profile (strelitzia.profile): who the user is, their resume and content library ---

MAX_RESUME_BYTES = 5 * 1024 * 1024
PROFILE_FIELDS = ("first_name", "last_name", "phone", "email", "linkedin_url", "github_url", "location",
                  "current_job_title", "years_experience", "education_level", "skills")


def _content_library_error(text: str) -> str | None:
    """Why the content library can't be used by the workbench, or None (also None when it's empty)."""
    if not text.strip():
        return None
    try:
        library = parse_content_library_text(text)
    except Exception as e:  # the parser is lenient; anything it still trips on is shown, not a 500
        return f"The content library couldn't be read: {e}"
    if not library.roles:
        return "No roles found: each role starts with a \"## CURRENT ROLE\" or \"## PRIOR ROLE\" heading."
    return None


def _profile_out(profile: profile_store.UserProfile) -> dict:
    return {
        "profile": {name: getattr(profile, name) for name in PROFILE_FIELDS},
        "has_resume": bool(profile.resume_text.strip()),
        "resume_file_name": profile.resume_file_name,
        "content_library_md": profile.content_library_md,
        "content_library_error": _content_library_error(profile.content_library_md),
        "options": {"education_level": list(profile_store.PROFILE_EDUCATION_LEVELS)},
    }


@api.get("/profile")
def get_profile(request: Request) -> dict:
    return _profile_out(profile_store.load_profile(db_conn(request), current_user(request).id))


class ProfileBody(BaseModel):
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    email: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    location: str = ""
    current_job_title: str = ""
    years_experience: float | None = Field(default=None, ge=0, le=70)
    education_level: Literal[profile_store.PROFILE_EDUCATION_LEVELS] | None = None
    skills: list[str] = Field(default_factory=list, max_length=200)
    content_library_md: str = Field(default="", max_length=500_000)

    @field_validator("first_name", "last_name", "phone", "email", "linkedin_url", "github_url", "location",
                     "current_job_title", mode="before")
    @classmethod
    def _trim(cls, value):
        return (value or "").strip() if value is None or isinstance(value, str) else value  # others: a 422

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _check_email(value) or ""

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, value: str) -> str:
        return _check_link(value, "linkedin.com", "LinkedIn") or ""

    @field_validator("github_url")
    @classmethod
    def _github(cls, value: str) -> str:
        return _check_link(value, "github.com", "GitHub") or ""

    @field_validator("skills")
    @classmethod
    def _skills(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for s in (s.strip() for s in value):
            if s and s.lower() not in {x.lower() for x in out}:
                out.append(s)
        return out


@api.put("/profile")
def put_profile(body: ProfileBody, request: Request) -> dict:
    """Save the profile fields and the content library that are sent (the others stay as they are). The resume and
    the profile's extras are never changed here."""
    conn = db_conn(request)
    user = current_user(request)
    profile = profile_store.load_profile(conn, user.id)
    for name, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, name, value)
    profile_store.save_profile(conn, user.id, profile, with_resume=False)
    return get_profile(request)


def _is_empty(value) -> bool:
    return value is None or value == "" or value == []


def _valid_draft_value(name: str, value) -> bool:
    """Whether PUT /profile would accept the drafted value (a bad link in the draft would block the user's Save)."""
    try:
        if name == "email":
            _check_email(value)
        elif name == "linkedin_url":
            _check_link(value, "linkedin.com", "LinkedIn")
        elif name == "github_url":
            _check_link(value, "github.com", "GitHub")
    except ValueError:
        return False
    return True


@api.post("/profile/resume")
def post_profile_resume(file: UploadFile, request: Request) -> dict:
    """Store an uploaded PDF or DOCX resume and its text (scoring reads it; jobs assessed with the old one show as
    stale), then return Claude's draft for the profile fields that are still empty. The draft isn't saved: the
    user reviews it and saves the profile.

    422 for a file that's too large, not a PDF or DOCX, or has no text. 402 when the user's cap (or the monthly
    budget) is reached: the resume is stored, but not parsed. Without an API key (or on another LLM error) the
    draft is empty and `parse_error` says why.
    """
    conn = db_conn(request)
    user = current_user(request)
    data = file.file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(status_code=422, detail="The resume is larger than 5 MB")
    name = Path(file.filename or "resume").name
    try:
        text = resume_parse.extract_text(name, file.content_type, data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    profile_store.save_resume_file(conn, user.id, name, resume_parse.file_mime(name, file.content_type), data, text)
    profile = profile_store.load_profile(conn, user.id)
    budget_stop(conn, user.id)
    parse_error = None
    try:
        parsed = resume_parse.parse_resume(text, user.id, conn=conn)
    except LLMStopRun as e:
        if e.reason == "budget":
            budget_stop(conn, user.id)  # raced past the check above: the same 402
        parse_error = f"The resume was saved, but it couldn't be parsed: {e}"
        parsed = {}
    except Exception as e:  # an unusable answer or an API error: the upload itself still worked
        log.warning("resume parse failed for user %s: %s", user.id, type(e).__name__)
        parse_error = f"The resume was saved, but it couldn't be parsed ({type(e).__name__})."
        parsed = {}
    draft = {k: v for k, v in parsed.items()
             if k in PROFILE_FIELDS and _is_empty(getattr(profile, k)) and _valid_draft_value(k, v)}
    return {**_profile_out(profile), "draft": draft, "parse_error": parse_error}


@api.get("/profile/resume")
def get_profile_resume(request: Request) -> Response:
    """The uploaded resume file."""
    profile = profile_store.load_profile(db_conn(request), current_user(request).id, with_file=True)
    if profile.resume_file is None:
        raise HTTPException(status_code=404, detail="No resume file uploaded")
    name = profile.resume_file_name or "resume"
    return Response(profile.resume_file, media_type=profile.resume_mime or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@api.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    conn = db_conn(request)
    user = current_user(request)
    task = tasks.get_task(conn, task_id, user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    row = conn.execute("SELECT job_key FROM jobs WHERE user_id = ? AND url = ?", (user.id, task["url"])).fetchone()
    return {**task, "key": row[0] if row else None}


@api.get("/jobs/{key}/tasks")
def list_job_tasks(key: str, request: Request) -> list[dict]:
    conn = db_conn(request)
    user = current_user(request)
    job = job_by_key(conn, user.id, key)
    return [{**t, "key": key} for t in tasks.job_tasks(conn, user.id, job["url"])]


# The built UI ships inside the package (dashboard/ builds into it), so pip installs need no Node.
PACKAGED_STATIC_DIR = Path(__file__).parent / "static"


def _default_static_dir() -> Path | None:
    value = env("WEB_DIR")
    if value:
        return Path(value)
    return PACKAGED_STATIC_DIR if (PACKAGED_STATIC_DIR / "index.html").is_file() else None


def _session_secret() -> str:
    """STRELITZIA_SESSION_SECRET; with login off and no secret, a per-process one (sessions then hold nothing)."""
    return env("SESSION_SECRET") or secrets.token_urlsafe(32)


def _import_legacy_drive_token(conn) -> None:
    """Give the owner the CLI's Drive token once TOKEN_KEY is set (strelitzia.storage.drive.import_legacy_token)."""
    from strelitzia.storage.drive import DriveNotConfigured, import_legacy_token

    try:
        if import_legacy_token(conn):
            log.info("Imported the CLI's Google Drive token for the owner")
    except DriveNotConfigured as e:
        log.warning("Couldn't import the CLI's Google Drive token: %s", e)


def create_app(static_dir: Path | str | None = None, db: Path | str | None = None,
               generate: tasks.Generator | None = None, run_tasks: bool = True) -> FastAPI:
    """The dashboard app.

    static_dir: the built UI (default: $STRELITZIA_WEB_DIR, else the bundle in web/static); without it only the API is served.
    db: a SQLite path or database URL (default: STRELITZIA_DATABASE_URL, else the SQLite DB_PATH).
    generate: the function that runs queued tasks (default: real intake and assessment, which uses the
        configured database, so pass db=None with it). run_tasks=False queues tasks without running them.
    """
    auth.check_config()
    runner = tasks.TaskRunner(db, generate) if run_tasks else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runner:
            runner.start()
        try:
            yield
        finally:
            if runner:
                runner.stop()

    app = FastAPI(title="Strelitzia dashboard", version=__version__, lifespan=lifespan,
                  docs_url=f"{PREFIX}/api/docs", openapi_url=f"{PREFIX}/api/openapi.json", redoc_url=None)
    app.state.db = db
    app.state.runner = runner
    _import_legacy_drive_token(init_db(db))

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        token = request.headers.get(CSRF_HEADER) or request.headers.get(LEGACY_CSRF_HEADER)
        if request.method not in _SAFE_METHODS and token != "1":
            return JSONResponse({"detail": f"Missing {CSRF_HEADER}: 1 header"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        path = request.url.path
        if auth.auth_enabled() and path.startswith(f"{PREFIX}/api") and not auth.is_public_path(path):
            try:
                await run_in_threadpool(auth.current_user, request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)

    # Added last so it wraps the middlewares above (they read request.session).
    app.add_middleware(SessionMiddleware, secret_key=_session_secret(), session_cookie=auth.SESSION_COOKIE,
                       max_age=auth.SESSION_MAX_AGE, same_site="lax", https_only=auth.secure_cookie())
    app.include_router(auth.router)
    app.include_router(auth.drive_router)
    app.include_router(admin.router)
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
            return JSONResponse({"detail": "Dashboard UI not built (cd dashboard && npm ci && npm run build)"}, status_code=404)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            if path.startswith("assets/"):  # a missing asset is a real 404, not the SPA page
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")
