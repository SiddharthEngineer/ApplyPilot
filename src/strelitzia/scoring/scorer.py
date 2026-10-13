"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.

The rubric and the resume form the cached system prompt and the job is the user turn, so every request after the
first reuses the resume prefix. Bulk runs go through the Message Batches API (half price); small ones are sync.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime, timezone
from typing import Literal

import anthropic

from strelitzia.config import RESUME_PATH, load_profile, load_search_config
from strelitzia.database import get_connection, get_jobs_by_stage, job_key
from strelitzia.llm import LLMStopRun, check_budget, estimate_cost, get_client, json_object
from strelitzia.llm_batch import resume_open_batches, run_batch

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

ANSWER FIELDS:
- score: the fit score, an integer from 1 to 10
- keywords: comma-separated ATS keywords from the job description that match or could match the candidate
- reasoning: 2-3 sentences explaining the score"""

SCORE_SCHEMA = json_object({
    "score": {"type": "integer", "description": "Fit score from 1 to 10"},
    "keywords": {"type": "string", "description": "Comma-separated matching ATS keywords"},
    "reasoning": {"type": "string", "description": "2-3 sentences explaining the score"},
})

# Output budget per job, thinking included.
_MAX_OUTPUT_TOKENS = 2048
# Runs of at most this many jobs go sync: a batch's latency isn't worth it for a handful of requests.
SYNC_MAX_JOBS = 5


def _parse_score_json(response: str) -> dict | None:
    """Parse a structured-output reply; None if it isn't the expected JSON object."""
    try:
        data = json.loads(response)
        score = max(1, min(10, int(data["score"])))
    except (ValueError, TypeError, KeyError):
        return None
    return {"score": score, "keywords": str(data.get("keywords", "")), "reasoning": str(data.get("reasoning", ""))}


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's structured-output score reply.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}; score 0 means unparseable.
    """
    parsed = _parse_score_json(response.strip())
    return parsed if parsed is not None else {"score": 0, "keywords": "", "reasoning": response}


def score_messages(resume_text: str, job: dict) -> list[dict]:
    """Messages for one scoring request: rubric + resume (the cached system prompt), then the job."""
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )
    return [
        {"role": "system", "content": f"{SCORE_PROMPT}\n\nCANDIDATE RESUME:\n{resume_text}"},
        {"role": "user", "content": f"JOB POSTING:\n{job_text}"},
    ]


def _result_from_reply(response: str) -> dict:
    parsed = _parse_score_response(response)
    if parsed["score"] == 0:  # not the JSON object; real scores are clamped to 1-10
        result = _error_result(f"unparseable response: {response[:200]!r}")
        result["parse_error"] = True
        return result
    return parsed


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str} on success. On an LLM error or an unparseable
        reply, "score" is None and "error" holds the message, so the job stays unscored and retryable.
    """
    try:
        client = get_client("scoring")
        response = client.chat(score_messages(resume_text, job), max_tokens=_MAX_OUTPUT_TOKENS, schema=SCORE_SCHEMA,
                               job_url=job.get("url"))
        return _result_from_reply(response)
    except LLMStopRun:
        raise  # run_scoring stops the stage; this job stays untouched
    except anthropic.APIStatusError as e:
        log.error("LLM error scoring job '%s': HTTP %d: %s", job.get("title", "?"), e.status_code, e.message)
        return _error_result(f"HTTP {e.status_code}")
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return _error_result(str(e))


def _error_result(msg: str) -> dict:
    """Result for a job that couldn't be scored. `reasoning` keeps the 'LLM error' prefix --reset-errors matches."""
    return {"score": None, "keywords": "", "reasoning": f"LLM error: {msg}", "error": msg}


# ── Title pre-filter (no LLM call) ───────────────────────────────────────

PREFILTER_REASONING = "prefilter: title not relevant"

# Words that say nothing about the kind of role: articles, seniority, levels, work mode.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "to", "in", "at", "on", "with", "or", "by", "from",
    "senior", "sr", "junior", "jr", "lead", "staff", "principal", "mid", "entry", "level", "head", "chief",
    "i", "ii", "iii", "iv", "v", "remote", "hybrid", "onsite", "contract", "temporary", "full", "part", "time",
})


def _title_tokens(text: str) -> set[str]:
    """Significant lower-cased words of a title (keeps tech tokens like c++, c#, .net, ai)."""
    words = re.findall(r"[a-z0-9][a-z0-9+#.]*", (text or "").lower())
    return {w.rstrip(".") for w in words if w.rstrip(".") and w.rstrip(".") not in _TITLE_STOPWORDS}


def load_target_tokens() -> set[str]:
    """Words from searches.yaml queries and the profile's target role(s)."""
    titles: list[str] = []
    try:
        titles += [q.get("query", "") for q in load_search_config().get("queries", []) if isinstance(q, dict)]
    except Exception as e:  # noqa: BLE001 - a missing/broken config just disables the filter
        log.debug("prefilter: no search queries (%s)", e)
    try:
        exp = load_profile().get("experience", {})
        titles += re.split(r"[,;/|]", exp.get("target_role", "") or "")
        titles += exp.get("target_titles", []) or []
    except Exception as e:  # noqa: BLE001
        log.debug("prefilter: no profile target role (%s)", e)
    tokens: set[str] = set()
    for t in titles:
        tokens |= _title_tokens(t)
    return tokens


def prefilter_jobs(jobs: list[dict], target_tokens: set[str]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (to_score, skipped). A title passes if it shares a significant word with any target.

    With no targets configured, nothing is skipped.
    """
    if not target_tokens:
        return jobs, []
    keep, skipped = [], []
    for job in jobs:
        (keep if _title_tokens(job.get("title", "")) & target_tokens else skipped).append(job)
    return keep, skipped


def _save_score(conn, r: dict) -> None:
    """Write one scoring result and commit it right away."""
    if r["score"] is None:
        # Leave fit_score NULL so the job is retried, up to MAX_SCORE_ATTEMPTS runs.
        conn.execute(
            "UPDATE jobs SET fit_score = NULL, score_reasoning = ?, scored_at = NULL, "
            "score_attempts = COALESCE(score_attempts, 0) + 1 WHERE url = ?",
            (r["reasoning"], r["url"]),
        )
    else:
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (r["score"], f"{r['keywords']}\n{r['reasoning']}", datetime.now(timezone.utc).isoformat(), r["url"]),
        )
    conn.commit()


class _Tally:
    """Saves each result as it arrives and keeps the counts."""

    def __init__(self, conn, total: int) -> None:
        self.conn = conn
        self.total = total
        self.results: list[dict] = []
        self.errors = self.parse_errors = 0
        self.first_error = ""

    def add(self, url: str, result: dict, title: str = "?") -> None:
        result["url"] = url
        if result.get("parse_error"):
            self.parse_errors += 1
        if result["score"] is None:
            self.errors += 1
            self.first_error = self.first_error or result.get("reasoning", "")
        self.results.append(result)
        _save_score(self.conn, result)
        log.info("[%d/%d] score=%s  %s", len(self.results), self.total,
                 result["score"] if result["score"] is not None else "error", title[:60])


def _score_sync(tally: _Tally, resume_text: str, jobs: list[dict]) -> str:
    """One request per job. Returns the stop reason ("" if it ran to the end)."""
    for job in jobs:
        try:
            result = score_job(resume_text, job)
        except LLMStopRun as e:
            log.warning("Scoring stopped: %s. %d jobs left unscored for the next run.", e, len(jobs) - len(tally.results))
            return e.reason
        tally.add(job["url"], result, job.get("title") or "?")
    return ""


def _score_batch(tally: _Tally, resume_text: str, jobs: list[dict], poll_seconds: float, extra: dict) -> str:
    """Every job in Message Batches, after a cost estimate and budget check. Returns the stop reason."""
    client = get_client("scoring")
    by_key = {job_key(j["url"]): j for j in jobs}
    items = [(key, client.request_params(score_messages(resume_text, j), max_tokens=_MAX_OUTPUT_TOKENS,
                                         schema=SCORE_SCHEMA)) for key, j in by_key.items()]

    def on_result(custom_id: str, text: str | None, error: str | None) -> None:
        job = by_key.get(custom_id)
        if job is None:
            log.warning("Batch result %s matches no job in this run; skipped.", custom_id)
            return
        tally.add(job["url"], _result_from_reply(text) if text is not None else _error_result(error or "?"),
                  job.get("title") or "?")

    try:
        estimate = estimate_cost("scoring", [params for _, params in items], batch=True)
        extra["estimate_usd"] = estimate
        log.info("Scoring %d job(s) in a batch: estimated cost $%.2f (%s).", len(items), estimate, client.model)
        check_budget(estimate)
        run_batch("scoring", items, on_result, poll_seconds=poll_seconds)
    except LLMStopRun as e:
        log.warning("Scoring stopped: %s. Unscored jobs stay pending for the next run.", e)
        return e.reason
    return ""


def _collect_open_batches(conn) -> set[str]:
    """Save the results of scoring batches a killed run left open (matched by job_key). Returns their urls."""
    tally = _Tally(conn, 0)

    def on_result(custom_id: str, text: str | None, error: str | None) -> None:
        row = conn.execute("SELECT url, title FROM jobs WHERE job_key = ?", (custom_id,)).fetchone()
        if row is None:
            log.warning("Batch result %s matches no job; skipped.", custom_id)
            return
        tally.add(row[0], _result_from_reply(text) if text is not None else _error_result(error or "?"),
                  row[1] or "?")

    try:
        resume_open_batches("scoring", on_result)
    except LLMStopRun as e:
        log.warning("Could not collect open scoring batches: %s", e)
    return {r["url"] for r in tally.results}


def run_scoring(limit: int = 0, rescore: bool = False, prefilter: bool = True,
                mode: Literal["batch", "sync"] = "batch", poll_seconds: float = 30) -> dict:
    """Score unscored jobs that have full descriptions.

    Each result is written to the DB as soon as it arrives, so a run that is stopped (Ctrl-C, budget, crash)
    keeps every score it already paid for; a batch left open is collected by the next batch run.

    Args:
        limit: Maximum number of jobs sent to the LLM in this run (0 = all). Applied after
            the prefilter, which costs no LLM calls and always covers every pending job.
        rescore: If True, re-score all jobs (not just unscored ones).
        prefilter: If True, jobs whose title shares no word with the search queries / target role
            get fit_score 1 without an LLM call.
        mode: "batch" (Message Batches, half price, default) or "sync" (one request at a time). Runs of at most
            SYNC_MAX_JOBS jobs are always sync.
        poll_seconds: How often to check a submitted batch.

    Returns:
        {"scored", "errors", "parse_errors", "prefiltered", "elapsed", "distribution", "mode"}, plus
        "estimate_usd" for a batch and "stopped" when the budget, key or API stopped the run.
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    extra: dict = {}

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        # No SQL limit: the prefilter must see every pending job, and `limit` caps LLM calls below.
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=0)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    prefiltered = 0
    if prefilter:
        total = len(jobs)
        jobs, skipped = prefilter_jobs(jobs, load_target_tokens())
        prefiltered = len(skipped)
        now = datetime.now(UTC).isoformat()
        conn.executemany(
            "UPDATE jobs SET fit_score = 1, score_reasoning = ?, scored_at = ? WHERE url = ?",
            [(PREFILTER_REASONING, now, j["url"]) for j in skipped],
        )
        conn.commit()
        log.info("prefilter skipped %d/%d jobs (title not relevant; --no-prefilter to score them)",
                 prefiltered, total)
        if not jobs:
            return {"scored": 0, "errors": 0, "parse_errors": 0, "prefiltered": prefiltered,
                    "elapsed": 0.0, "distribution": []}

    if limit > 0 and len(jobs) > limit:
        log.info("--limit %d: scoring %d of %d jobs; the rest stay pending.", limit, limit, len(jobs))
        jobs = jobs[:limit]

    if mode == "batch" and len(jobs) <= SYNC_MAX_JOBS:
        mode = "sync"
    if mode == "batch":
        # Results of a batch a killed run left open come first, so those jobs aren't paid for twice.
        resumed = _collect_open_batches(conn)
        extra["resumed"] = len(resumed)
        jobs = [j for j in jobs if j["url"] not in resumed]
    log.info("Scoring %d jobs (%s)...", len(jobs), mode)
    t0 = time.time()
    tally = _Tally(conn, len(jobs))
    if mode == "sync":
        stopped = _score_sync(tally, resume_text, jobs)
    else:
        stopped = _score_batch(tally, resume_text, jobs, poll_seconds, extra)
    results, errors, parse_errors = tally.results, tally.errors, tally.parse_errors

    if not stopped and errors == len(jobs) and errors > 0:
        log.error("ALL %d jobs failed to score; check the Claude setup with `strelitzia doctor`. First error: %s",
                  errors, tally.first_error)

    elapsed = time.time() - t0
    log.info("Done: %d scored, %d errors in %.1fs (%.1f jobs/sec)", len(results) - errors, errors, elapsed,
             len(results) / elapsed if elapsed > 0 else 0)
    # Each unparseable reply costs a retry on a later run (another paid request).
    log.info("JSON parse retries (scoring): %d", parse_errors)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    stats = {
        "scored": len(results) - errors,
        "errors": errors,
        "parse_errors": parse_errors,
        "prefiltered": prefiltered,
        "elapsed": elapsed,
        "distribution": distribution,
    }
    stats.update(extra, mode=mode)
    if stopped:
        stats["stopped"] = stopped
    return stats
