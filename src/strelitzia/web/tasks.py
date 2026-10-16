"""Background work for the dashboard: fit re-assessment and job intake.

Tasks live in the `dashboard_tasks` table and one daemon thread runs them one at a time, because the LLM
budget is shared. Resumes and cover letters aren't tasks: they are written in Claude and pasted back
(strelitzia.scoring.workbench). A task left `running` by a stopped server is marked
`error: interrupted` on the next start.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from strelitzia.database import Connection, get_connection
from strelitzia.llm import LLMStopRun, acting_for
from strelitzia.tracking import _now

log = logging.getLogger(__name__)

# rescore: assess one job now (sync). reassess_all: re-assess every stale job in a batch (url is NULL). rescore_all:
# re-assess every job that isn't rejected, current assessments included, in a batch (url is NULL). Both are only
# queued after the user confirmed the cost estimate. intake: read, extract and score a job the user added
# (strelitzia.intake).
KINDS = ("rescore", "reassess_all", "rescore_all", "intake")
ALL_JOBS_KINDS = ("reassess_all", "rescore_all")  # url is NULL
STATES = ("queued", "running", "done", "error")
# What the dashboard shows when the LLM stops a task (LLMStopRun.reason).
STOP_MESSAGES = {
    "budget": "This month's Claude budget is used up (your monthly cap, or LLM_MONTHLY_BUDGET_USD for everyone); "
              "wait for next month or ask the owner to raise it.",
    "auth": "The Claude API key is missing or was rejected; check ANTHROPIC_API_KEY.",
    "overloaded": "The Claude API is overloaded right now; try again in a few minutes.",
    "no_resume": "Add your resume on the Profile page first: jobs are assessed against it.",
}

# generate(user_id, url, kind) does the work for one of the user's tasks and raises on failure (url is None for
# ALL_JOBS_KINDS).
Generator = Callable[[str, str | None, str], None]


class GenerationFailed(RuntimeError):
    """The stage ran but produced nothing for this job (the message says why)."""


def task_dict(row) -> dict:
    return {k: row[k] for k in ("id", "user_id", "url", "kind", "state", "created_at", "started_at", "finished_at",
                                "error")}


def get_task(conn: Connection, task_id: str, user_id: str | None = None) -> dict | None:
    """A task by id; with `user_id`, only if it's that user's."""
    sql = "SELECT * FROM dashboard_tasks WHERE id = ?" + (" AND user_id = ?" if user_id else "")
    row = conn.execute(sql, (task_id, user_id) if user_id else (task_id,)).fetchone()
    return task_dict(row) if row else None


def job_tasks(conn: Connection, user_id: str, url: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE user_id = ? AND url = ? ORDER BY created_at DESC, id", (user_id, url)
    ).fetchall()
    return [task_dict(r) for r in rows]


def enqueue(conn: Connection, user_id: str, url: str | None, kind: str) -> dict:
    """Queue a task for the user (or return the identical one already waiting for them)."""
    if kind not in KINDS:
        raise ValueError(f"Unknown task kind {kind!r}")
    if (url is None) != (kind in ALL_JOBS_KINDS):
        raise ValueError(f"Task kind {kind!r} {'takes no' if url is None else 'needs a'} job")
    if url is None:
        row = conn.execute(
            "SELECT * FROM dashboard_tasks WHERE user_id = ? AND url IS NULL AND kind = ? AND state = 'queued'",
            (user_id, kind),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM dashboard_tasks WHERE user_id = ? AND url = ? AND kind = ? AND state = 'queued'",
            (user_id, url, kind),
        ).fetchone()
    if row:
        return task_dict(row)
    task_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO dashboard_tasks (id, user_id, url, kind, state, created_at) VALUES (?, ?, ?, ?, 'queued', ?)",
        (task_id, user_id, url, kind, _now()),
    )
    conn.commit()
    return get_task(conn, task_id)


def active_task(conn: Connection, user_id: str, kind: str) -> dict | None:
    """The user's newest queued or running task of `kind` that isn't about one job, if any."""
    row = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE user_id = ? AND url IS NULL AND kind = ? AND state IN ('queued', 'running') "
        "ORDER BY created_at DESC, id LIMIT 1",
        (user_id, kind),
    ).fetchone()
    return task_dict(row) if row else None


def recover_interrupted(conn: Connection) -> int:
    """Mark tasks a previous server left `running` as failed. Returns how many."""
    cur = conn.execute(
        "UPDATE dashboard_tasks SET state = 'error', error = 'interrupted', finished_at = ? WHERE state = 'running'",
        (_now(),),
    )
    conn.commit()
    return cur.rowcount


def assess_task(user_id: str, url: str | None, kind: str) -> None:
    """rescore: assess one job now. reassess_all / rescore_all: re-assess every stale / non-rejected job in a
    Message Batch (can take an hour)."""
    from strelitzia.scoring.scorer import run_scoring

    if kind == "rescore":
        stats = run_scoring(urls=[url], mode="sync", prefilter=False, user_id=user_id)
    elif kind == "rescore_all":
        stats = run_scoring(mode="batch", scope="active", user_id=user_id)
    else:
        stats = run_scoring(mode="batch", reassess=True, user_id=user_id)
    if stats.get("stopped") in STOP_MESSAGES:
        raise LLMStopRun(stats["stopped"])
    if stats.get("stopped"):
        raise GenerationFailed(f"The assessment stopped: {stats['stopped']}.")
    # A re-assessment that saved most results succeeded: the failed jobs keep their old assessment.
    if stats.get("errors") and (kind == "rescore" or not stats.get("scored")):
        raise GenerationFailed(f"{stats['errors']} assessment(s) failed with an error (see the server log).")
    if kind == "rescore" and not stats.get("scored"):
        raise GenerationFailed("The job can't be assessed until its posting has been extracted.")


# Kinds queued by older versions (resume / cover-letter generation), still in the table after an upgrade.
RETIRED_KINDS = ("resume", "cover", "both")


def generate_for_job(user_id: str, url: str | None, kind: str) -> None:
    """The real work for one task, against the configured database: `intake` goes to `intake.run_intake`,
    the assessment kinds to `assess_task`. Any other kind fails without running anything. Claude calls are billed to
    the user and held to their cap."""
    with acting_for(user_id):
        _generate(user_id, url, kind)


def _generate(user_id: str, url: str | None, kind: str) -> None:
    if kind == "intake":
        from strelitzia.intake import run_intake

        run_intake(user_id, url)
    elif kind in ("rescore", *ALL_JOBS_KINDS):
        assess_task(user_id, url, kind)
    elif kind in RETIRED_KINDS:
        raise GenerationFailed("Resumes are written in Claude now: use Open in Claude on the job page.")
    else:
        raise GenerationFailed(f"Unknown task kind {kind!r}.")


class TaskRunner:
    """One daemon thread that takes queued tasks oldest first."""

    def __init__(self, db: Path | str | None = None, generate: Generator | None = None, poll_seconds: float = 2.0):
        self.db = db
        self.generate = generate or generate_for_job
        self.poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        n = recover_interrupted(get_connection(self.db))
        if n:
            log.warning("Marked %d interrupted generation task(s) as failed.", n)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="strelitzia-tasks", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def notify(self) -> None:
        """A task was queued: check now instead of at the next poll."""
        self._wake.set()

    def _next(self, conn: Connection) -> dict | None:
        row = conn.execute(
            "SELECT * FROM dashboard_tasks WHERE state = 'queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        return task_dict(row) if row else None

    def _loop(self) -> None:
        conn = get_connection(self.db)  # this thread's own connection
        while not self._stop.is_set():
            try:
                task = self._next(conn)
            except Exception:  # e.g. the DB restarted; keep polling
                log.exception("Could not read the task queue")
                task = None
            if task is None:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
                continue
            self.run_one(conn, task)

    def run_one(self, conn: Connection, task: dict) -> None:
        conn.execute("UPDATE dashboard_tasks SET state = 'running', started_at = ? WHERE id = ?", (_now(), task["id"]))
        conn.commit()
        state, error = "done", None
        try:
            self.generate(task["user_id"], task["url"], task["kind"])
        except LLMStopRun as e:
            state, error = "error", STOP_MESSAGES.get(e.reason, str(e))
        except GenerationFailed as e:
            state, error = "error", str(e)
        except Exception as e:  # reported on the task
            log.exception("Generation task %s failed", task["id"])
            state, error = "error", f"{type(e).__name__}: {e}"
        conn.execute(
            "UPDATE dashboard_tasks SET state = ?, error = ?, finished_at = ? WHERE id = ?",
            (state, error, _now(), task["id"]),
        )
        conn.commit()
