"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime, timezone

import httpx

from applypilot.config import RESUME_PATH, load_profile, load_search_config
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import LLMQuotaExhausted, get_client

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

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score]"""


# Gemini structured output (responseSchema format). Other providers get the
# text format from SCORE_PROMPT; _parse_score_response accepts both.
SCORE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER", "description": "Fit score from 1 to 10"},
        "keywords": {"type": "STRING", "description": "Comma-separated matching ATS keywords"},
        "reasoning": {"type": "STRING", "description": "2-3 sentences explaining the score"},
    },
    "required": ["score", "keywords", "reasoning"],
    "propertyOrdering": ["score", "keywords", "reasoning"],
}


def _parse_score_json(response: str) -> dict | None:
    """Parse a structured-output reply; None if it isn't the expected JSON object."""
    try:
        data = json.loads(response)
        score = max(1, min(10, int(data["score"])))
    except (ValueError, TypeError, KeyError):
        return None
    return {"score": score, "keywords": str(data.get("keywords", "")), "reasoning": str(data.get("reasoning", ""))}


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text (structured JSON or the SCORE/KEYWORDS/REASONING format).

    Returns:
        {"score": int, "keywords": str, "reasoning": str}; score 0 means unparseable.
    """
    parsed = _parse_score_json(response.strip())
    if parsed is not None:
        return parsed

    score = 0
    keywords = ""
    reasoning = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning}


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str} on success. On an LLM error or an unparseable
        reply, "score" is None and "error" holds the message, so the job stays unscored and retryable.
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client("scoring")
        response = client.chat(messages, max_tokens=512, temperature=0.2, response_schema=SCORE_SCHEMA)
        parsed = _parse_score_response(response)
        if parsed["score"] == 0:  # no usable SCORE line; real scores are clamped to 1-10
            result = _error_result(f"unparseable response: {response[:200]!r}")
            result["parse_error"] = True
            return result
        return parsed
    except LLMQuotaExhausted:
        raise  # run_scoring stops the stage; this job stays untouched
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        body = e.response.text[:300]
        hint = ""
        if status in (400, 403, 404):
            hint = (
                " — check GEMINI_API_KEY, LLM_MODEL (default gemini-3.6-flash), "
                "and that model exists on https://ai.google.dev/gemini-api/docs/models"
            )
        log.error(
            "LLM error scoring job '%s': HTTP %d%s — %s",
            job.get("title", "?"), status, hint, body,
        )
        return _error_result(f"HTTP {status}")
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


def run_scoring(limit: int = 0, rescore: bool = False, prefilter: bool = True) -> dict:
    """Score unscored jobs that have full descriptions.

    Each result is written to the DB as soon as its LLM call returns, so a run that is
    stopped (Ctrl-C, quota, crash) keeps every score it already paid for.

    Args:
        limit: Maximum number of jobs sent to the LLM in this run (0 = all). Applied after
            the prefilter, which costs no LLM calls and always covers every pending job.
        rescore: If True, re-score all jobs (not just unscored ones).
        prefilter: If True, jobs whose title shares no word with the search queries / target role
            get fit_score 1 without an LLM call.

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

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

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    first_error_msg = ""
    parse_errors = 0
    results: list[dict] = []
    stopped = ""

    for job in jobs:
        try:
            result = score_job(resume_text, job)
        except LLMQuotaExhausted as e:
            stopped = "daily_quota"
            log.warning("Scoring stopped: %s. %d jobs left unscored for the next run.",
                        e, len(jobs) - completed)
            break
        result["url"] = job["url"]
        completed += 1

        if result.get("parse_error"):
            parse_errors += 1
        if result["score"] is None:
            errors += 1
            if not first_error_msg:
                first_error_msg = result.get("reasoning", "")

        results.append(result)
        _save_score(conn, result)

        log.info(
            "[%d/%d] score=%s  %s",
            completed, len(jobs), result["score"] if result["score"] is not None else "error",
            job.get("title", "?")[:60],
        )

    # If all jobs failed, check for systemic LLM config issue
    if not stopped and errors == len(jobs) and errors > 0 and ("404" in first_error_msg or "400" in first_error_msg):
        log.error(
            "ALL %d jobs failed to score — likely a systemic LLM configuration issue.\n"
            "  Check GEMINI_API_KEY, LLM_MODEL (default gemini-3.6-flash), "
            "and that model exists on https://ai.google.dev/gemini-api/docs/models\n"
            "  First error: %s",
            errors, first_error_msg,
        )

    elapsed = time.time() - t0
    log.info("Done: %d scored, %d errors in %.1fs (%.1f jobs/sec)", len(results) - errors, errors, elapsed,
             len(results) / elapsed if elapsed > 0 else 0)
    # Each unparseable reply costs a retry on a later run (another quota request).
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
    if stopped:
        stats["stopped"] = stopped
    return stats
