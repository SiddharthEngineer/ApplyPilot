"""Assessment layer of the fit score: a fixed Claude rubric, plus `recompute_fit`, which folds it into `fit_score`.

Each request has three parts:
  1. the rubric (fixed system block, identical for every user),
  2. the candidate: resume text plus the structured inputs from the Preferences page (skills, industries, target
     roles), rendered as JSON with sorted keys, so its sha256 (`assessment_input_hash`) only changes when they do,
  3. the job (user turn): the extracted posting as JSON plus the description, truncated.
Both system blocks are cached, so every request after the first in a run reads them from cache.

The user steers the rubric only through those structured inputs; there is no free-text prompt.
"""

from __future__ import annotations

import hashlib
import json
import logging

from strelitzia import users
from strelitzia.database import job_company
from strelitzia.enrichment.posting_model import JobPosting
from strelitzia.llm import json_object
from strelitzia.scoring.preferences import (
    COMPONENT_LABELS,
    LLM_INPUTS,
    RED_FLAGS,
    Component,
    Preferences,
    combine,
    load_preferences,
    score_preferences,
)

log = logging.getLogger(__name__)

# Bump whenever the rubric, the schema or the request layout changes: every assessment becomes stale.
ASSESS_VERSION = 1
DESCRIPTION_CHARS = 8000

RATED = ("skills_match", "experience_match", "domain_interest")
PREFILTER_FLAG = "prefilter"

ASSESS_PROMPT = """You assess how well a job fits one candidate. The candidate's resume and stated wishes come next as JSON; the job posting is in the user turn. Judge only from that text, and never invent experience the resume doesn't show.

Rate three dimensions from 1 to 5 using these anchors, and give one sentence of evidence for each that names concrete skills, titles or facts from both sides.

skills_match: the job's required and preferred skills against the candidate's demonstrated skills.
  5 = has nearly every required skill with real use in work or projects, plus several preferred ones
  4 = has most required skills; gaps are minor or quickly learned
  3 = has about half of the required skills, or the core ones only at a shallow level
  2 = has a few relevant skills; major required ones are missing
  1 = little overlap, a different discipline
  Count skills_want the job uses in the candidate's favour and skills_avoid it centres on against them.

experience_match: seniority, years and scope the job asks for against the candidate's career.
  5 = the level and scope match the candidate's recent roles
  4 = one step off (slightly junior or senior) but a realistic move
  3 = a stretch: noticeably more years or scope than shown, or clearly below the candidate's level
  2 = far apart (for example a director role for a junior candidate)
  1 = no realistic path

domain_interest: the industry, product and kind of work against what the candidate says they want.
  5 = squarely in industries_favor or target_roles
  4 = adjacent to them
  3 = neutral: nothing stated either way, or no preference given
  2 = mostly work the candidate hasn't asked for
  1 = in industries_avoid, or centred on skills_avoid

red_flags: list only what the posting itself shows, with a short note each. Codes:
  mostly_sales: the role is mainly selling despite its title
  commission_heavy: pay is mostly commission
  title_mismatch: the duties don't match the title
  unrealistic_requirements: requirements no one could meet (for example more years of a tool than it has existed)
  contract_disguised: a contract or gig presented as a permanent job
  relocation_required: relocation is required and not offered
  unpaid_or_trial: unpaid work, an unpaid trial or a paid-to-apply scheme
  vague_posting: too vague to tell what the job is
  other: anything else a careful candidate would want to know before applying
Return an empty list when there are none; most postings have none.

matched_keywords: ATS keywords from the posting that the candidate's resume matches or could honestly claim.
summary: two sentences on the fit, plainly worded."""

_RATING = json_object({
    "score": {"type": "integer", "description": "1 to 5, using the rubric anchors"},
    "evidence": {"type": "string", "description": "One sentence naming concrete facts from both sides"},
})
ASSESS_SCHEMA = json_object({
    "skills_match": _RATING,
    "experience_match": _RATING,
    "domain_interest": _RATING,
    "red_flags": {"type": "array", "items": json_object({
        "code": {"type": "string", "enum": list(RED_FLAGS)},
        "note": {"type": "string"},
    })},
    "matched_keywords": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string"},
})


# ── Inputs and their hash ─────────────────────────────────────────────────

def candidate_block(prefs: Preferences, resume_text: str) -> str:
    """The second system block: the resume and the assessment inputs, as deterministic JSON."""
    doc = {"resume": resume_text.strip(), **{k: sorted(getattr(prefs, k)) for k in LLM_INPUTS}}
    return "CANDIDATE:\n" + json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1)


def assessment_input_hash(prefs: Preferences, resume_text: str) -> str:
    """sha256 of the candidate block: changes only when the resume or the assessment inputs change."""
    return hashlib.sha256(candidate_block(prefs, resume_text).encode("utf-8")).hexdigest()


def assessment_key(prefs: Preferences, resume_text: str) -> str:
    """What `jobs.assessment_hash` holds for an up-to-date assessment: the rubric version and the input hash."""
    return f"{ASSESS_VERSION}:{assessment_input_hash(prefs, resume_text)}"


def load_resume_text(conn, user_id: str | None = None) -> str:
    """The user's resume text from their profile (the owner's by default; "" when there is none yet)."""
    from strelitzia.profile import resume_text

    return resume_text(conn, user_id)


def current_assessment_key(conn, prefs: Preferences | None = None, user_id: str | None = None) -> str:
    """The key of an up-to-date assessment for the user (the owner by default)."""
    return assessment_key(prefs or load_preferences(conn, user_id), load_resume_text(conn, user_id))


# ── Request and reply ─────────────────────────────────────────────────────

def posting_document(job: dict) -> dict:
    """The job as the model sees it: the extracted posting, the listing title and the company."""
    posting = _posting(job)
    doc = posting.to_dict()
    doc["listing_title"] = job.get("title")
    doc["company"] = posting.company or job_company(job) or None
    return doc


def assess_messages(prefs: Preferences, resume_text: str, job: dict) -> list[dict]:
    """Messages for one assessment: rubric and candidate (two cached system blocks), then the job."""
    description = (job.get("full_description") or "")[:DESCRIPTION_CHARS]
    job_text = (f"POSTING (extracted):\n{json.dumps(posting_document(job), ensure_ascii=False)}\n\n"
                f"DESCRIPTION:\n{description}")
    return [
        {"role": "system", "content": ASSESS_PROMPT},
        {"role": "system", "content": candidate_block(prefs, resume_text)},
        {"role": "user", "content": job_text},
    ]


def _rating(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("rating is not an object")
    score = max(1, min(5, int(value["score"])))
    return {"score": score, "evidence": str(value.get("evidence") or "")}


def parse_assessment(text: str) -> dict | None:
    """The assessment from a structured-output reply (ratings clamped to 1-5), or None if it isn't one."""
    try:
        data = json.loads(text)
        out = {k: _rating(data[k]) for k in RATED}
        flags = data.get("red_flags") or []
        out["red_flags"] = [
            {"code": f["code"] if f.get("code") in RED_FLAGS else "other", "note": str(f.get("note") or "")}
            for f in flags if isinstance(f, dict)
        ]
        out["matched_keywords"] = [str(k) for k in data.get("matched_keywords") or [] if str(k).strip()]
        out["summary"] = str(data.get("summary") or "")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return out


# ── Fit score (pure Python) ───────────────────────────────────────────────

def _posting(job: dict) -> JobPosting:
    try:
        return JobPosting.from_dict(json.loads(job.get("details_json") or "{}"))
    except ValueError:
        return JobPosting()


def _rated_importance(prefs: Preferences, key: str) -> str:
    """The fixed weights of the ratings. Domain interest only counts once the user has said which industries they
    like or avoid."""
    if key == "skills_match":
        return "very_important"
    if key == "domain_interest" and not (prefs.industries_favor or prefs.industries_avoid):
        return "dont_care"
    return "important"


def _flag_text(flag: dict, counted: bool) -> str:
    text = flag["code"].replace("_", " ") + ("" if counted else " (not counted)")
    return f"{text}: {flag['note']}" if flag.get("note") else text


def assessment_components(prefs: Preferences, assessment: dict) -> list[Component]:
    """The assessment layer's components: ratings 1-5 mapped to 0-1 at fixed weights, and red flags (0 when the
    posting has any flag the user counts, else 1) at Important."""
    out = []
    for key in RATED:
        rating = assessment.get(key) or {}
        score = (rating["score"] - 1) / 4 if isinstance(rating.get("score"), int) else None
        out.append(Component(key, COMPONENT_LABELS[key], score, _rated_importance(prefs, key), False,
                             rating.get("evidence", "")))
    flags = assessment.get("red_flags") or []
    counted = [f["code"] in prefs.red_flags_counted for f in flags]
    note = "; ".join(_flag_text(f, c) for f, c in zip(flags, counted, strict=True))
    out.append(Component("red_flags", COMPONENT_LABELS["red_flags"], 0.0 if any(counted) else 1.0, "important",
                         False, note or "none"))
    return out


def compute_fit(prefs: Preferences, job: dict) -> tuple[int | None, list[dict], list[str]]:
    """(fit_score, breakdown, flags) for one job row from its stored assessment and the current preferences.

    A job with no assessment keeps its current fit_score (an old 1-10 score times 10, or NULL); its preference
    breakdown and dealbreaker flags are still filled in. A prefiltered job scores 0.
    """
    components = score_preferences(prefs, _posting(job))
    assessment = _assessment(job)
    prefiltered = bool(assessment and assessment.get("prefiltered"))
    if assessment and not prefiltered:
        components += assessment_components(prefs, assessment)
    overall, flags = combine(components)
    if prefiltered:
        overall, flags = 0, [PREFILTER_FLAG, *flags]
    elif assessment is None:
        overall = job.get("fit_score")
    breakdown = [c.to_dict() for c in components if c.importance != "dont_care"]
    return overall, breakdown, flags


def _assessment(job: dict) -> dict | None:
    try:
        value = json.loads(job["fit_assessment"]) if job.get("fit_assessment") else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def recompute_fit(conn, user_id: str | None = None, urls: list[str] | None = None,
                  prefs: Preferences | None = None) -> int:
    """Recompute fit_score, fit_breakdown and fit_flags for the user's extracted jobs (all, or `urls`). No LLM calls.

    `user_id` defaults to the owner; `prefs` to the user's saved preferences. Returns the number of jobs updated.
    """
    user_id = user_id or users.owner(conn).id
    prefs = prefs or load_preferences(conn, user_id)
    sql = ("SELECT url, title, company, site, strategy, details_json, fit_assessment, fit_score FROM jobs "
           "WHERE details_json IS NOT NULL AND user_id = ?")
    params: list = [user_id]
    if urls is not None:
        if not urls:
            return 0
        sql += f" AND url IN ({', '.join('?' * len(urls))})"
        params += list(urls)
    updates = []
    for row in conn.execute(sql, params).fetchall():
        job = dict(row)
        overall, breakdown, flags = compute_fit(prefs, job)
        updates.append((overall, json.dumps(breakdown), json.dumps(flags) if flags else None, user_id, job["url"]))
    if updates:
        conn.executemany("UPDATE jobs SET fit_score = ?, fit_breakdown = ?, fit_flags = ? WHERE user_id = ? AND url = ?",
                         updates)
        conn.commit()
    return len(updates)
