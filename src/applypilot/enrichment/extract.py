"""`extract` stage: Gemini parses each enriched posting into a JobPosting, several jobs per request.

Jobs are taken best-scored first (then newest), `batch_size` per request, with structured output
(`batch_schema()`), and matched back by `job_id`. Each batch is committed as soon as its response
returns, so a stopped run keeps everything it already paid for. A job missing from a multi-job
response, or with an unusable object, gets `extract_attempts += 1` and is retried on its own later
in the same run. A daily-quota stop ends the stage cleanly, like scoring.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime

from applypilot.config import extract_batch_size
from applypilot.database import MAX_EXTRACT_ATTEMPTS, PENDING_EXTRACT_WHERE, get_connection, get_jobs_by_stage
from applypilot.enrichment.posting_model import EXTRACT_VERSION, JobPosting, batch_schema
from applypilot.enrichment.posting_prompt import build_extract_prompt, format_batch, job_id
from applypilot.llm import LLMStopRun, get_client

log = logging.getLogger(__name__)

# Output budget per job in a batch (the full JSON for a long posting is ~2-3k tokens).
_TOKENS_PER_JOB = 6000
_MAX_OUTPUT_TOKENS = 60_000
# Stop the run after this many requests in a row fail outright (HTTP errors, unparseable replies).
_MAX_CONSECUTIVE_FAILURES = 3


def _parse_batch(response: str) -> list[dict]:
    """The "jobs" array of a batch reply. Raises ValueError if the reply isn't that shape."""
    text = response.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON ({e.msg} at char {e.pos})") from e
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        raise ValueError("reply has no \"jobs\" array")  # noqa: TRY004 — ValueError = retryable bad reply
    return [j for j in jobs if isinstance(j, dict)]


def _save_posting(conn, url: str, posting: JobPosting) -> None:
    loc = posting.locations[0] if posting.locations else None
    conn.execute(
        "UPDATE jobs SET details_json = ?, role_category = ?, seniority = ?, employment_type = ?, work_mode = ?, "
        "location_city = ?, location_state = ?, location_country = ?, salary_min = ?, salary_max = ?, "
        "salary_currency = ?, salary_period = ?, posted_date = ?, deadline = ?, "
        "company = COALESCE(company, ?), extracted_at = ?, extract_version = ?, extract_error = NULL, "
        "category_source = 'llm' WHERE url = ?",
        (
            posting.to_json(), posting.role_category, posting.seniority, posting.employment_type,
            posting.work_mode, loc.city if loc else None, loc.state if loc else None,
            loc.country if loc else None, posting.salary_min, posting.salary_max, posting.salary_currency,
            posting.salary_period, posting.posted_date, posting.application_deadline, posting.company,
            datetime.now(UTC).isoformat(), EXTRACT_VERSION, url,
        ),
    )


def _save_failure(conn, url: str, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET extract_attempts = COALESCE(extract_attempts, 0) + 1, extract_error = ? WHERE url = ?",
        (error[:500], url),
    )


def extract_batch(jobs: list[dict], today: str | None = None) -> tuple[dict[str, JobPosting], dict[str, str]]:
    """One LLM request for `jobs`. Returns ({url: posting}, {url: error}) covering every job.

    Raises:
        LLMStopRun: the daily quota is used up, or the model stayed overloaded through every retry
            (nothing was extracted).
        Exception: the request itself failed (HTTP error, timeout) or the reply wasn't a batch.
    """
    today = today or datetime.now().astimezone().date().isoformat()
    messages = [
        {"role": "system", "content": build_extract_prompt(today)},
        {"role": "user", "content": format_batch(jobs)},
    ]
    client = get_client("extract")
    response = client.chat(
        messages, temperature=0.0, response_schema=batch_schema(),
        max_tokens=min(_MAX_OUTPUT_TOKENS, _TOKENS_PER_JOB * len(jobs)),
    )
    items = _parse_batch(response)

    by_id = {job_id(j): j for j in jobs}
    got: dict[str, JobPosting] = {}
    errors: dict[str, str] = {}
    for item in items:
        jid = str(item.get("job_id", "")).strip()
        if jid not in by_id and len(jobs) == 1 and len(items) == 1:
            jid = job_id(jobs[0])  # a single job can't be mismatched
        job = by_id.get(jid)
        if job is None or job["url"] in got:
            continue
        try:
            got[job["url"]] = JobPosting.from_dict(item)
        except ValueError as e:
            errors[job["url"]] = f"invalid object: {e}"
    for job in jobs:
        if job["url"] not in got and job["url"] not in errors:
            errors[job["url"]] = "missing from the LLM response"
    return got, errors


def run_extraction(limit: int | None = None, batch_size: int | None = None, conn=None) -> dict:
    """Extract pending jobs (PENDING_EXTRACT_WHERE), best fit_score first, then newest.

    Args:
        limit: Max jobs to extract this run (None/0 = all pending).
        batch_size: Jobs per LLM request (default EXTRACT_BATCH_SIZE, 5).
        conn: DB connection (default: the configured job store).

    Returns:
        {"extracted", "errors", "requests", "pending_left", "elapsed"} plus "stopped" ("daily_quota",
        "overloaded" or "errors") when the run ended early.
    """
    conn = conn or get_connection()
    batch_size = max(1, batch_size or extract_batch_size())
    jobs = get_jobs_by_stage(conn=conn, stage="pending_extract", limit=limit or 0)
    if not jobs:
        log.info("No jobs pending extraction.")
        return {"extracted": 0, "errors": 0, "requests": 0, "pending_left": 0, "elapsed": 0.0}

    log.info("Extracting %d job(s), %d per request...", len(jobs), batch_size)
    queue: list[list[dict]] = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]
    retried: set[str] = set()
    extracted = errors = requests = consecutive_failures = 0
    stopped = ""
    t0 = time.time()

    while queue:
        batch = queue.pop(0)
        requests += 1
        try:
            got, failed = extract_batch(batch)
            consecutive_failures = 0
        except LLMStopRun as e:
            requests -= 1
            stopped = e.reason
            log.warning("Extraction stopped: %s. The rest stay pending for the next run.", e)
            break
        except Exception as e:  # noqa: BLE001 -- one bad request must not lose the run
            consecutive_failures += 1
            got, failed = {}, {j["url"]: f"LLM error: {e}" for j in batch}
            log.error("Extraction request failed for %d job(s): %s", len(batch), e)

        for url, posting in got.items():
            _save_posting(conn, url, posting)
        for url, err in failed.items():
            _save_failure(conn, url, err)
        conn.commit()
        extracted += len(got)

        for job in batch:
            if job["url"] not in failed:
                continue
            attempts = (job.get("extract_attempts") or 0) + 1
            job["extract_attempts"] = attempts
            # Retry once on its own, unless it just failed alone or has used up its attempts.
            if len(batch) > 1 and job["url"] not in retried and attempts < MAX_EXTRACT_ATTEMPTS:
                retried.add(job["url"])
                queue.append([job])
            else:
                errors += 1
        log.info("[%d/%d] extracted %d, failed %d in this request", extracted, len(jobs), len(got), len(failed))

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            stopped = "errors"
            log.error("Extraction stopped after %d failed requests in a row.", consecutive_failures)
            break

    pending_left = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {PENDING_EXTRACT_WHERE}").fetchone()[0]
    elapsed = time.time() - t0
    log.info("Extraction done: %d extracted, %d errors, %d request(s) in %.1fs; %d still pending.",
             extracted, errors, requests, elapsed, pending_left)
    stats = {"extracted": extracted, "errors": errors, "requests": requests,
             "pending_left": pending_left, "elapsed": elapsed}
    if stopped:
        stats["stopped"] = stopped
    return stats

