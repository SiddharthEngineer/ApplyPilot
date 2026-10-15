"""FastAPI app for the job dashboard. Every route lives under /app, which nginx proxies unchanged.

  /app/api/...   JSON API
  /app/...       the built UI (index.html + assets), with unknown paths falling back to index.html

Mutating requests need the header `X-Strelitzia: 1`. nginx puts basic auth in front, and browsers resend
basic-auth credentials on cross-site form posts, but a cross-site form can't set a custom header.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from strelitzia import __version__, intake, tracking, workspace
from strelitzia.config import env
from strelitzia.database import Connection, backend_name, backfill_job_keys, get_connection, init_db, job_company
from strelitzia.llm import LLMStopRun, check_budget, month_spend
from strelitzia.scoring import preferences as prefs_mod
from strelitzia.scoring import workbench
from strelitzia.scoring.assessment import RED_FLAGS, current_assessment_key, recompute_fit
from strelitzia.web import queries, tasks

PREFIX = "/app"
CSRF_HEADER = "X-Strelitzia"
LEGACY_CSRF_HEADER = "X-ApplyPilot"  # sent by dashboard bundles from before the rename (open tabs after a deploy)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

api = APIRouter(prefix=f"{PREFIX}/api")
log = logging.getLogger(__name__)


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
    backfill_job_keys(conn)
    filters = queries.JobFilters(
        status=list(status), role=role, work_mode=work_mode, location=location,
        found_from=found_from, found_to=found_to, due_from=due_from, due_to=due_to,
        score_min=score_min, score_max=score_max, q=q, hide_flagged=hide_flagged, source=list(source),
    )
    return queries.list_jobs(conn, filters, sort=sort, page=page, page_size=page_size, today=today(),
                             current_key=current_assessment_key(conn))


@api.get("/facets")
def facets(request: Request) -> dict:
    return queries.facets(db_conn(request), today())


# Large raw columns left out of the detail response (the description comes back once, as description_text).
_RAW_COLUMNS = ("full_description", "description", "details_json", "fit_assessment")


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
    out.update(queries.fit_fields(job, current_assessment_key(conn)))
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
        events=tracking.status_events(conn, job["url"]),
        source=job.get("source") or "discovered",
        contacts=workspace.job_contacts(conn, job["url"]),
        interviews=workspace.job_interviews(conn, job["url"]),
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


@api.get("/jobs/{key}/files/{kind}")
def get_job_file(key: str, kind: Literal["resume_pdf", "resume_docx", "cover_letter_pdf"], request: Request):
    job = job_by_key(db_conn(request), key)
    path = _local_file(job, kind)
    if path is None:
        raise HTTPException(status_code=404, detail="No such file on this server")
    return FileResponse(path, filename=path.name)


def _workbench_inputs() -> workbench.Inputs:
    try:
        return workbench.Inputs.load()
    except FileNotFoundError as e:
        raise HTTPException(status_code=409, detail=f"{e}. Add your content library first (strelitzia init).") from e


def _drive_client():
    """A Drive client when Drive is authorized, else None (the workbench falls back to the clipboard)."""
    from strelitzia.storage.drive import DriveClient, drive_status

    if drive_status()[0] != "authorized":
        return None
    return DriveClient.from_credentials()


@api.post("/jobs/{key}/packet")
def post_packet(key: str, request: Request) -> dict:
    """Write the job's Claude packet (a Drive Doc when Drive is set up) and return the prompt to paste in Claude."""
    conn = db_conn(request)
    job = job_by_key(conn, key)
    inputs = _workbench_inputs()
    current = workbench.current_result(job)
    drive_error = None
    try:
        client = _drive_client()
        if client is not None:
            workbench.publish_static_docs(client, conn, inputs)
            _, url = workbench.publish_packet(client, conn, job, inputs)
            packet = workbench.job_packet(job, inputs.library, inputs.base_resume_text, current, inline=False)
            return {"prompt": packet.prompt, "doc_url": url, "inline": False, "rules_version": workbench.RULES_VERSION}
    except Exception as e:  # noqa: BLE001 -- Drive trouble shouldn't block the clipboard flow
        log.warning("Drive packet failed for %s: %s", key, e)
        drive_error = f"Couldn't write the packet to Drive ({e}); the full packet was copied instead."
    packet = workbench.job_packet(job, inputs.library, inputs.base_resume_text, current, inline=True)
    return {"prompt": packet.prompt, "doc_url": None, "inline": True, "rules_version": workbench.RULES_VERSION,
            "drive_error": drive_error}


class PastedResult(BaseModel):
    text: str = Field(max_length=200_000)


@api.post("/jobs/{key}/result")
def post_result(key: str, body: PastedResult, request: Request):
    """Check a pasted Claude reply. 422 with errors and a fix message, or 200 with the saved draft and the job."""
    conn = db_conn(request)
    job = job_by_key(conn, key)
    outcome = workbench.import_result(conn, job, body.text, _workbench_inputs())
    if not outcome.ok:
        return JSONResponse({"detail": outcome.errors[0] if outcome.errors else "The result didn't pass the checks",
                             "errors": outcome.errors, "warnings": outcome.warnings,
                             "fix_prompt": outcome.fix_prompt}, status_code=422)
    warnings = list(outcome.warnings)
    try:
        client = _drive_client()
        if client is not None:
            from strelitzia.storage.sync import sync_job

            sync_job(conn, client, job_by_key(conn, key))
    except Exception as e:  # noqa: BLE001 -- the files are saved locally
        log.warning("Drive upload failed for %s: %s", key, e)
        warnings.append(f"Saved, but the Drive upload failed: {e}")
    return {"outcome": {"draft_version": outcome.draft_version, "warnings": warnings,
                        "coverage": outcome.coverage, "unmet": outcome.unmet},
            "job": job_detail(conn, job_by_key(conn, key))}


@api.get("/claude/setup")
def claude_setup(request: Request) -> dict:
    """What the Claude setup page shows: the Project Docs, the rules version, Drive status, the instructions."""
    from strelitzia.storage.drive import drive_status

    state, detail = drive_status()
    docs = workbench.published_static_docs(db_conn(request))
    return {
        "drive": state == "authorized", "drive_detail": detail,
        "rules_version": workbench.RULES_VERSION,
        "docs": [{"kind": kind, "name": name, "url": (docs.get(kind) or {}).get("url"),
                  "changed_at": (docs.get(kind) or {}).get("changed_at")}
                 for kind, name in workbench.STATIC_DOCS.items()],
        "project_name": "Strelitzia Resumes",
        "project_instructions": workbench.PROJECT_INSTRUCTIONS,
    }


@api.post("/claude/docs")
def publish_claude_docs(request: Request) -> dict:
    """Write (or refresh) the rules and content-library Docs in Drive."""
    try:
        client = _drive_client()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(e)) from e
    if client is None:
        raise HTTPException(status_code=409, detail="Google Drive isn't set up. Run `strelitzia drive auth`.")
    workbench.publish_static_docs(client, db_conn(request), _workbench_inputs(), force=True)
    return claude_setup(request)


class GenerateRequest(BaseModel):
    resume: bool = True
    cover: bool = False


@api.post("/jobs/{key}/generate")
def generate(key: str, body: GenerateRequest, request: Request) -> dict:
    """Queue resume and/or cover-letter generation for one job. Poll GET /app/api/tasks/{id}."""
    if not (body.resume or body.cover):
        raise HTTPException(status_code=422, detail="Choose a resume, a cover letter, or both")
    conn = db_conn(request)
    job = job_by_key(conn, key)
    kind = "both" if body.resume and body.cover else ("resume" if body.resume else "cover")
    task = tasks.enqueue(conn, job["url"], kind)
    _notify(request)
    return {**task, "key": key}


def budget_stop(conn: Connection, estimated_usd: float = 0.0) -> None:
    """402 with the month's spend if `estimated_usd` more would pass LLM_MONTHLY_BUDGET_USD."""
    try:
        check_budget(estimated_usd, conn)
    except LLMStopRun as e:
        raise HTTPException(status_code=402, detail={
            "message": f"{e} {tasks.STOP_MESSAGES['budget']}", "month_spend": round(month_spend(conn), 2),
            "estimated_usd": round(estimated_usd, 4)}) from e


def _notify(request: Request) -> None:
    runner = request.app.state.runner
    if runner:
        runner.notify()


@api.post("/jobs/{key}/rescore")
def rescore(key: str, request: Request) -> dict:
    """Queue a sync assessment of one job (whatever its state). Poll GET /app/api/tasks/{id}."""
    conn = db_conn(request)
    job = job_by_key(conn, key)
    budget_stop(conn)
    task = tasks.enqueue(conn, job["url"], "rescore")
    _notify(request)
    return {**task, "key": key}


MIN_PASTED_CHARS = 200  # a pasted description shorter than this isn't a whole posting


class AddJobBody(BaseModel):
    url: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None


def _queue_intake(conn: Connection, request: Request, url: str) -> dict:
    """Queue the intake task for a job, or return the one already queued or running for it."""
    active = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE url = ? AND kind = 'intake' AND state IN ('queued', 'running') "
        "ORDER BY created_at DESC LIMIT 1", (url,)).fetchone()
    if active:
        return tasks.task_dict(active)
    conn.execute("UPDATE jobs SET intake_status = 'queued', intake_error = NULL WHERE url = ?", (url,))
    conn.commit()
    task = tasks.enqueue(conn, url, "intake")
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
    try:
        existing = intake.find_existing(conn, url) if url else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if existing is None or intake.needs_intake(existing):
        budget_stop(conn)
    manual = intake.ManualFields(title=body.title, company=body.company, location=body.location,
                                 description=body.description)
    row, created = intake.add_job(conn, url, manual)
    task = _queue_intake(conn, request, row["url"]) if created or intake.needs_intake(row) else None
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
    job = job_by_key(conn, key)
    budget_stop(conn)
    intake.save_pasted(conn, job["url"], intake.ManualFields(
        title=body.title, company=body.company, location=body.location, description=body.description))
    return {"task": _queue_intake(conn, request, job["url"]), "key": key}


# --- Contacts and interviews (strelitzia.workspace) ---

class ContactPatch(BaseModel):
    name: str | None = None
    company: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None


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
    return workspace.search_contacts(db_conn(request), q, limit=limit)


@api.post("/contacts", status_code=201)
def create_contact(body: ContactCreate, request: Request) -> dict:
    return _workspace_call(workspace.create_contact, db_conn(request), **body.model_dump(exclude_unset=True))


def _contact_out(conn: Connection, contact_id: str) -> dict:
    contact = _workspace_call(workspace.get_contact, conn, contact_id)
    return {**contact, "jobs": workspace.contact_jobs(conn, contact_id)}


@api.get("/contacts/{contact_id}")
def get_contact(contact_id: str, request: Request) -> dict:
    return _contact_out(db_conn(request), contact_id)


@api.patch("/contacts/{contact_id}")
def patch_contact(contact_id: str, body: ContactPatch, request: Request) -> dict:
    conn = db_conn(request)
    _workspace_call(workspace.update_contact, conn, contact_id, **body.model_dump(exclude_unset=True))
    return _contact_out(conn, contact_id)


@api.delete("/contacts/{contact_id}")
def delete_contact(contact_id: str, request: Request) -> dict:
    _workspace_call(workspace.delete_contact, db_conn(request), contact_id)
    return {"ok": True}


@api.post("/jobs/{key}/contacts")
def link_job_contact(key: str, body: JobContactLink, request: Request) -> dict:
    """Link a contact to the job: an existing one (`contact_id`) or a new one (`contact`). Returns the job."""
    conn = db_conn(request)
    job = job_by_key(conn, key)
    if (body.contact_id is None) == (body.contact is None):
        raise HTTPException(status_code=422, detail="Give either contact_id or a new contact")
    if body.contact is None:
        _workspace_call(workspace.link_contact, conn, job["url"], body.contact_id, body.relation)
    else:
        contact_id = _workspace_call(workspace.create_contact, conn, **body.contact.model_dump(exclude_unset=True))["id"]
        try:
            workspace.link_contact(conn, job["url"], contact_id, body.relation)
        except Exception:
            workspace.delete_contact(conn, contact_id)  # don't leave a contact behind that the user never saw
            raise
    return job_detail(conn, job_by_key(conn, key))


@api.delete("/jobs/{key}/contacts/{contact_id}")
def unlink_job_contact(key: str, contact_id: str, request: Request) -> dict:
    conn = db_conn(request)
    job = job_by_key(conn, key)
    workspace.unlink_contact(conn, job["url"], contact_id)
    return job_detail(conn, job_by_key(conn, key))


@api.post("/jobs/{key}/interviews", status_code=201)
def create_interview(key: str, body: InterviewPatch, request: Request) -> dict:
    conn = db_conn(request)
    job = job_by_key(conn, key)
    return _workspace_call(workspace.create_interview, conn, job["url"], **body.model_dump(exclude_unset=True))


@api.patch("/interviews/{interview_id}")
def patch_interview(interview_id: str, body: InterviewPatch, request: Request) -> dict:
    return _workspace_call(workspace.update_interview, db_conn(request), interview_id,
                           **body.model_dump(exclude_unset=True))


@api.delete("/interviews/{interview_id}")
def delete_interview(interview_id: str, request: Request) -> dict:
    _workspace_call(workspace.delete_interview, db_conn(request), interview_id)
    return {"ok": True}


@api.get("/preferences")
def get_preferences(request: Request) -> dict:
    """The saved preferences, plus the labels and value lists the Preferences page offers."""
    return {
        "prefs": prefs_mod.load_preferences(db_conn(request)).to_dict(),
        "options": {**prefs_mod.OPTIONS, "red_flags": list(RED_FLAGS)},
        "labels": prefs_mod.LABELS,
    }


class PrefsBody(BaseModel):
    prefs: dict


class PrefsSave(PrefsBody):
    confirm_reassess: bool = False


def _preview(conn: Connection, prefs: prefs_mod.Preferences) -> dict:
    from strelitzia.scoring.scorer import reassess_preview

    return reassess_preview(conn, prefs)


@api.post("/preferences/preview")
def preview_preferences(body: PrefsBody, request: Request) -> dict:
    """What saving these preferences would re-assess and roughly cost (Batch pricing). Changes nothing."""
    return _preview(db_conn(request), prefs_mod.Preferences.from_dict(body.prefs))


@api.put("/preferences")
def put_preferences(body: PrefsSave, request: Request) -> dict:
    """Save, then re-rank every job now (no LLM). With confirm_reassess, also queue re-assessment of stale jobs.

    Without the confirmation, jobs keep their old assessment and show as stale. A budget stop (402) saves nothing.
    """
    conn = db_conn(request)
    prefs = prefs_mod.Preferences.from_dict(body.prefs)
    preview = _preview(conn, prefs) if body.confirm_reassess else None
    reassess = bool(preview and preview["reassess_needed"] and preview["jobs_to_reassess"])
    if reassess:
        budget_stop(conn, preview["estimated_usd"])
    prefs_mod.save_preferences(conn, prefs)
    rescored = recompute_fit(conn, prefs=prefs)
    task = None
    if reassess:
        task = tasks.enqueue(conn, None, "reassess_all")
        _notify(request)
    return {"prefs": prefs.to_dict(), "rescored": rescored, "task": task}


@api.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    conn = db_conn(request)
    task = tasks.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    row = conn.execute("SELECT job_key FROM jobs WHERE url = ?", (task["url"],)).fetchone()
    return {**task, "key": row[0] if row else None}


@api.get("/jobs/{key}/tasks")
def list_job_tasks(key: str, request: Request) -> list[dict]:
    conn = db_conn(request)
    job = job_by_key(conn, key)
    return [{**t, "key": key} for t in tasks.job_tasks(conn, job["url"])]


# The built UI ships inside the package (dashboard/ builds into it), so pip installs need no Node.
PACKAGED_STATIC_DIR = Path(__file__).parent / "static"


def _default_static_dir() -> Path | None:
    value = env("WEB_DIR")
    if value:
        return Path(value)
    return PACKAGED_STATIC_DIR if (PACKAGED_STATIC_DIR / "index.html").is_file() else None


def create_app(static_dir: Path | str | None = None, db: Path | str | None = None,
               generate: tasks.Generator | None = None, run_tasks: bool = True) -> FastAPI:
    """The dashboard app.

    static_dir: the built UI (default: $STRELITZIA_WEB_DIR, else the bundle in web/static); without it only the API is served.
    db: a SQLite path or database URL (default: STRELITZIA_DATABASE_URL, else the SQLite DB_PATH).
    generate: the generation function for queued tasks (default: real tailoring/cover/Drive, which uses the
        configured database, so pass db=None with it). run_tasks=False queues tasks without running them.
    """
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
    init_db(db)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        token = request.headers.get(CSRF_HEADER) or request.headers.get(LEGACY_CSRF_HEADER)
        if request.method not in _SAFE_METHODS and token != "1":
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
            return JSONResponse({"detail": "Dashboard UI not built (cd dashboard && npm ci && npm run build)"}, status_code=404)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            if path.startswith("assets/"):  # a missing asset is a real 404, not the SPA page
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")
