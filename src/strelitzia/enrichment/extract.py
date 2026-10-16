"""`extract` stage: Claude parses each enriched posting into a JobPosting, one posting per request.

Jobs are taken best-scored first (then newest). Every request shares the cached system prompt and asks for
structured output (`posting_schema()`). In `batch` mode (the default) the requests go through the Message Batches
API at half price, keyed by `job_id` and the user (`_custom_id`); `sync` mode sends them one by one (small runs, the
dashboard). Each result is committed as it arrives, so a stopped run keeps everything it already paid for. A failed job gets
`extract_attempts += 1` and is picked up by a later run until MAX_EXTRACT_ATTEMPTS. A budget stop ends the stage
cleanly, like scoring.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal

from strelitzia.database import PENDING_EXTRACT_WHERE, get_connection, get_jobs_by_stage
from strelitzia.enrichment.posting_model import EXTRACT_VERSION, JobPosting, posting_schema
from strelitzia.enrichment.posting_prompt import build_extract_prompt, format_posting, job_id
from strelitzia.llm import LLMStopRun, check_budget, estimate_cost, get_client
from strelitzia.llm_batch import resume_open_batches, run_batch

log = logging.getLogger(__name__)

# Output budget per posting, thinking included (the full JSON for a long posting is ~2-3k tokens).
_MAX_OUTPUT_TOKENS = 8000
# Stop a sync run after this many requests in a row fail outright (HTTP errors, unusable replies).
_MAX_CONSECUTIVE_FAILURES = 3
# Runs of at most this many jobs go sync: a batch's latency isn't worth it for a handful of requests.
SYNC_MAX_JOBS = 5


def parse_posting(response: str) -> JobPosting:
    """The JobPosting in a reply. Raises ValueError if the reply isn't one JSON object."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON ({e.msg} at char {e.pos})") from e
    return JobPosting.from_dict(data)


def _messages(job: dict, today: str) -> list[dict]:
    return [
        {"role": "system", "content": build_extract_prompt(today)},
        {"role": "user", "content": format_posting(job)},
    ]


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def extract_one(job: dict, today: str | None = None) -> JobPosting:
    """One synchronous request for `job`.

    Raises:
        LLMStopRun: the budget is used up, the key is bad, or the API stayed overloaded (nothing was extracted).
        Exception: the request failed or the reply wasn't a posting.
    """
    response = get_client("extract").chat(
        _messages(job, today or _today()), schema=posting_schema(), max_tokens=_MAX_OUTPUT_TOKENS, job_url=job["url"],
    )
    return parse_posting(response)


def _save_posting(conn, user_id: str, url: str, posting: JobPosting) -> None:
    loc = posting.locations[0] if posting.locations else None
    conn.execute(
        "UPDATE jobs SET details_json = ?, role_category = ?, seniority = ?, employment_type = ?, work_mode = ?, "
        "location_city = ?, location_state = ?, location_country = ?, salary_min = ?, salary_max = ?, "
        "salary_currency = ?, salary_period = ?, posted_date = ?, deadline = ?, "
        "company = COALESCE(company, ?), extracted_at = ?, extract_version = ?, extract_error = NULL, "
        "category_source = 'llm' WHERE user_id = ? AND url = ?",
        (
            posting.to_json(), posting.role_category, posting.seniority, posting.employment_type,
            posting.work_mode, loc.city if loc else None, loc.state if loc else None,
            loc.country if loc else None, posting.salary_min, posting.salary_max, posting.salary_currency,
            posting.salary_period, posting.posted_date, posting.application_deadline, posting.company,
            datetime.now(UTC).isoformat(), EXTRACT_VERSION, user_id, url,
        ),
    )


def _save_failure(conn, user_id: str, url: str, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET extract_attempts = COALESCE(extract_attempts, 0) + 1, extract_error = ? "
        "WHERE user_id = ? AND url = ?",
        (error[:500], user_id, url),
    )


def _custom_id(job: dict) -> str:
    """A job's batch custom_id: its job_id plus its user, since two users' rows for one posting share the job_id."""
    return f"{job_id(job)}-{job['user_id']}"


class _Results:
    """Saves each result as it arrives and keeps the counts."""

    def __init__(self, conn, jobs: list[dict]) -> None:
        self.conn = conn
        self.by_id = {_custom_id(j): (j["user_id"], j["url"]) for j in jobs}
        self.extracted = self.errors = 0

    def save(self, user_id: str, url: str, posting: JobPosting | None, error: str | None) -> None:
        if posting is not None:
            _save_posting(self.conn, user_id, url, posting)
            self.extracted += 1
        else:
            _save_failure(self.conn, user_id, url, error or "unknown error")
            self.errors += 1
        self.conn.commit()

    def on_batch_result(self, custom_id: str, text: str | None, error: str | None) -> None:
        """llm_batch callback. A batch resumed from an earlier run carries ids of jobs not in this run's list.

        Those are found by job_key and user, and skipped if the job has been extracted since (a newer result wins).
        A custom_id from before user accounts is the bare job_key.
        """
        target = self.by_id.get(custom_id)
        if target is None:
            key, _, user_id = custom_id.partition("-")
            sql = "SELECT user_id, url, extracted_at, extract_version FROM jobs WHERE job_key = ?"
            row = self.conn.execute(sql + (" AND user_id = ?" if user_id else ""),
                                    (key, user_id) if user_id else (key,)).fetchone()
            if row is None:
                log.warning("Batch result %s matches no job; skipped.", custom_id)
                return
            if row[2] and row[3] == EXTRACT_VERSION:
                log.info("Batch result for %s skipped: extracted since.", row[1])
                return
            target = (row[0], row[1])
        posting = None
        if text is not None:
            try:
                posting = parse_posting(text)
            except ValueError as e:
                error = f"invalid reply: {e}"
        self.save(*target, posting, None if posting else f"LLM error: {error}")


def _run_sync(results: _Results, jobs: list[dict], today: str) -> tuple[int, str]:
    """One request per job. Returns (requests, stopped)."""
    requests = consecutive_failures = 0
    for i, job in enumerate(jobs, 1):
        requests += 1
        try:
            posting = extract_one(job, today)
        except LLMStopRun as e:
            log.warning("Extraction stopped: %s. The rest stay pending for the next run.", e)
            return requests - 1, e.reason
        except Exception as e:  # noqa: BLE001 -- one bad request must not lose the run
            consecutive_failures += 1
            log.error("Extraction failed for %s: %s", job["url"], e)
            results.save(job["user_id"], job["url"], None, f"LLM error: {e}")
        else:
            consecutive_failures = 0
            results.save(job["user_id"], job["url"], posting, None)
        log.info("[%d/%d] extracted %d, failed %d", i, len(jobs), results.extracted, results.errors)
        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            log.error("Extraction stopped after %d failed requests in a row.", consecutive_failures)
            return requests, "errors"
    return requests, ""


def _run_batch(results: _Results, jobs: list[dict], today: str, poll_seconds: float,
               stats: dict) -> tuple[int, str]:
    """All jobs in Message Batches, after a cost estimate and budget check. Returns (requests, stopped)."""
    client = get_client("extract")
    items = [(_custom_id(j), client.request_params(_messages(j, today), max_tokens=_MAX_OUTPUT_TOKENS,
                                               schema=posting_schema())) for j in jobs]
    try:
        estimate = estimate_cost("extract", [params for _, params in items], batch=True)
        stats["estimate_usd"] = estimate
        log.info("Extracting %d job(s) in a batch: estimated cost $%.2f (%s).", len(jobs), estimate, client.model)
        check_budget(estimate, results.conn)
        batch = run_batch("extract", items, results.on_batch_result, poll_seconds=poll_seconds,
                          estimate_usd=estimate, conn=results.conn)
    except LLMStopRun as e:
        log.warning("Extraction stopped: %s. The rest stay pending for the next run.", e)
        return 0, e.reason
    if batch["succeeded"] == 0 and batch["errored"] >= _MAX_CONSECUTIVE_FAILURES:
        log.error("Every request in the extraction batch failed.")
        return len(items), "errors"
    return len(items), ""


def run_extraction(limit: int | None = None, conn=None, mode: Literal["batch", "sync"] = "batch",
                   poll_seconds: float = 30) -> dict:
    """Extract pending jobs (PENDING_EXTRACT_WHERE), best fit_score first, then newest.

    Args:
        limit: Max jobs to extract this run (None/0 = all pending).
        conn: DB connection (default: the configured job store).
        mode: "batch" (Message Batches, half price, default) or "sync" (one request at a time). Runs of at most
            SYNC_MAX_JOBS jobs are always sync.
        poll_seconds: How often to check a submitted batch.

    Returns:
        {"extracted", "errors", "requests", "pending_left", "elapsed", "mode"} plus "estimate_usd" for a batch, and
        "stopped" ("budget", "auth", "overloaded" or "errors") when the run ended early.
    """
    conn = conn or get_connection()
    t0 = time.time()
    today = _today()
    stats: dict = {"mode": mode}
    # Results of batches a killed run left open come first, so those jobs aren't paid for twice. A batch run waits
    # for them; a sync or small run only takes the ones that have already ended.
    wait = mode == "batch" and not (limit and limit <= SYNC_MAX_JOBS)
    resumed = _Results(conn, [])
    try:
        open_left = resume_open_batches("extract", resumed.on_batch_result, poll_seconds=poll_seconds, wait=wait,
                                        conn=conn)["still_open"]
    except LLMStopRun as e:  # submitting now could pay again for jobs in the uncollected batch
        log.warning("Extraction stopped: could not collect open batches (%s).", e)
        return {**stats, "extracted": 0, "errors": 0, "requests": 0, "pending_left": 0, "elapsed": 0.0,
                "stopped": e.reason}
    stats["resumed"] = resumed.extracted + resumed.errors
    if open_left:
        log.warning("%d extraction batch(es) from an earlier run are still processing; a later batch run collects "
                    "them.", open_left)
    jobs = get_jobs_by_stage(conn=conn, stage="pending_extract", limit=limit or 0)
    if not jobs:
        log.info("No jobs pending extraction.")
        return {**stats, "extracted": 0, "errors": 0, "requests": 0, "pending_left": 0, "elapsed": 0.0}

    if mode == "batch" and len(jobs) <= SYNC_MAX_JOBS:
        mode = "sync"
    log.info("Extracting %d job(s) (%s)...", len(jobs), mode)
    results = _Results(conn, jobs)
    if mode == "sync":
        requests, stopped = _run_sync(results, jobs, today)
    else:
        requests, stopped = _run_batch(results, jobs, today, poll_seconds, stats)

    pending_left = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {PENDING_EXTRACT_WHERE}").fetchone()[0]
    elapsed = time.time() - t0
    log.info("Extraction done: %d extracted, %d errors, %d request(s) in %.1fs; %d still pending.",
             results.extracted, results.errors, requests, elapsed, pending_left)
    stats.update({"extracted": results.extracted, "errors": results.errors, "requests": requests,
                  "pending_left": pending_left, "elapsed": elapsed, "mode": mode})
    if stopped:
        stats["stopped"] = stopped
    return stats
