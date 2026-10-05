"""Background resume / cover-letter generation for the dashboard.

Tasks live in the `dashboard_tasks` table and one daemon thread runs them one at a time, because the LLM
quota and Chromium (PDF rendering) are shared. A task left `running` by a stopped server is marked
`error: interrupted` on the next start.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from applypilot import tracking
from applypilot.database import Connection, get_connection
from applypilot.llm import LLMQuotaExhausted

log = logging.getLogger(__name__)

KINDS = ("resume", "cover", "both")
STATES = ("queued", "running", "done", "error")
QUOTA_MESSAGE = "Daily Gemini quota reached; try again tomorrow."

# generate(url, kind) does the work for one task and raises on failure.
Generator = Callable[[str, str], None]


class GenerationFailed(RuntimeError):
    """The stage ran but produced nothing for this job (the message says why)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def task_dict(row) -> dict:
    return {k: row[k] for k in ("id", "url", "kind", "state", "created_at", "started_at", "finished_at", "error")}


def get_task(conn: Connection, task_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dashboard_tasks WHERE id = ?", (task_id,)).fetchone()
    return task_dict(row) if row else None


def job_tasks(conn: Connection, url: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE url = ? ORDER BY created_at DESC, id", (url,)
    ).fetchall()
    return [task_dict(r) for r in rows]


def enqueue(conn: Connection, url: str, kind: str) -> dict:
    """Queue a generation task (or return the identical one already waiting) and mark the job In progress.

    The status only changes when the user hasn't set one (Active/Inactive jobs).
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown task kind {kind!r}")
    row = conn.execute(
        "SELECT * FROM dashboard_tasks WHERE url = ? AND kind = ? AND state = 'queued'", (url, kind)
    ).fetchone()
    if row:
        return task_dict(row)
    task_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO dashboard_tasks (id, url, kind, state, created_at) VALUES (?, ?, ?, 'queued', ?)",
        (task_id, url, kind, _now()),
    )
    conn.commit()
    job = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    if job is not None and tracking.effective_status(dict(job), datetime.now(UTC).date()) in ("active", "inactive"):
        tracking.set_status(conn, url, "in_progress", source="generate")
    return get_task(conn, task_id)


def recover_interrupted(conn: Connection) -> int:
    """Mark tasks a previous server left `running` as failed. Returns how many."""
    cur = conn.execute(
        "UPDATE dashboard_tasks SET state = 'error', error = 'interrupted', finished_at = ? WHERE state = 'running'",
        (_now(),),
    )
    conn.commit()
    return cur.rowcount


def _drive_configured() -> bool:
    from applypilot.storage.drive import drive_status

    return drive_status()[0] == "authorized"


def generate_for_job(url: str, kind: str) -> None:
    """The real work: tailor and/or write the cover letter for one job, then move its PDFs to Drive.

    Uses the same settings as `applypilot run` (content-library tailoring when the library exists, lenient
    validation for it, normal for cover letters). Runs against the configured database.
    """
    from applypilot.config import CONTENT_LIBRARY_PATH
    from applypilot.pipeline import tailor_validation_mode

    if kind in ("resume", "both"):
        from applypilot.scoring.tailor import run_tailoring

        source = "content-library" if CONTENT_LIBRARY_PATH.exists() else "resume"
        stats = run_tailoring(urls=[url], source=source, validation_mode=tailor_validation_mode(None, source))
        _check(stats, "approved", "resume")
    if kind in ("cover", "both"):
        from applypilot.scoring.cover_letter import run_cover_letters

        stats = run_cover_letters(urls=[url], validation_mode="normal")
        _check(stats, "generated", "cover letter")
    if _drive_configured():
        from applypilot.storage.drive import DriveClient
        from applypilot.storage.sync import sync_job

        conn = get_connection()
        cur = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,))
        names = [d[0] for d in cur.description]
        job = dict(zip(names, cur.fetchone()))
        try:
            sync_job(conn, DriveClient.from_credentials(), job)
        except Exception as e:  # the files exist locally; say what failed
            raise GenerationFailed(f"Generated, but the Drive upload failed: {e}") from e


def _check(stats: dict, ok_key: str, what: str) -> None:
    if stats.get("stopped") == "daily_quota":
        raise LLMQuotaExhausted("gemini", "daily")
    if not stats.get(ok_key):
        if stats.get("errors"):
            raise GenerationFailed(f"The {what} failed with an error (see the server log).")
        if stats.get("failed"):
            raise GenerationFailed(f"The {what} didn't pass validation; try again.")
        raise GenerationFailed(f"No {what} was generated (the job has no description).")


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
        self._thread = threading.Thread(target=self._loop, name="applypilot-tasks", daemon=True)
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
            self.generate(task["url"], task["kind"])
        except LLMQuotaExhausted:
            state, error = "error", QUOTA_MESSAGE
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
