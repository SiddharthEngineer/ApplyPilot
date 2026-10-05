"""Prompt for the `extract` stage: system prompt plus the batched user message.

The rules come from a sample of real postings on the VPS (see the R8 plan's Historical Record):
Workday tenants ("What you'll be doing:" / "What we need to see:" / "Ways to stand out from the crowd:",
"Minimum Qualifications:" / "Preferred Qualifications:", pay ranges in a closing paragraph, application
windows), Greenhouse/Ashby ("Pay Transparency Notice", "Annual Salary: $X — $Y USD", Logistics blocks with
education and visa policy), and Indeed/LinkedIn (markdown `**Header**` sections, hourly pay, recruiter
fluff). The system prompt is identical for every batch, so it is eligible for Gemini implicit caching.
"""

from __future__ import annotations

import re

from applypilot.database import JOB_BOARD_SITES
from applypilot.enrichment.posting_model import ENUMS, ROLE_CATEGORIES
from applypilot.storage.drive_layout import job_key

MAX_DESCRIPTION_CHARS = 12_000

_ROLE_HINTS = {
    "data_science": "data scientist, decision scientist, statistical modeling for product/business",
    "data_engineering": "data/analytics engineer, ETL/ELT, pipelines, warehouses, data platform",
    "ml_ai_engineering": "ML/AI/LLM engineer, MLOps, applied AI engineer, model training/serving",
    "data_analytics_bi": "data/business/BI analyst, dashboards, reporting, analytics",
    "software_engineering": "software/backend/frontend/full-stack/mobile engineer or developer",
    "research_science": "research scientist/engineer, applied scientist, PhD research roles",
    "product_program_management": "product, program, project or technical program manager",
    "quant_finance": "quantitative researcher/developer/analyst, trading",
    "hardware_electrical": "hardware, electrical, chip/ASIC/FPGA, photonics, embedded/firmware, RF",
    "it_infra_devops": "DevOps, SRE, cloud/infra/platform/network/security engineer, sysadmin, IT",
    "other": "sales, customer success, operations, consulting, non-technical, anything else",
}


def build_extract_prompt(today: str) -> str:
    """System prompt for extraction. `today` (YYYY-MM-DD) resolves relative dates like "posted 3 days ago"."""
    enum_lines = "\n".join(
        f"- {name}: {' | '.join(values)}" for name, values in ENUMS.items() if name != "role_category"
    )
    role_lines = "\n".join(f"  - {c}: {_ROLE_HINTS[c]}" for c in ROLE_CATEGORIES)
    return f"""You extract structured data from job postings. Today is {today}.

You get several postings, each starting with a JOB_ID line and a metadata block (title, company, location,
salary, source) followed by the description. Return one object per posting in "jobs", with its JOB_ID copied
exactly into "job_id". Never mix facts between postings.

CORE RULE: extract, never infer. If the posting doesn't state something, use null, "unknown" or [].
Never invent a company, salary, date, skill, or requirement. Copy names, numbers and tools as written.
Use both the metadata block and the description; the metadata location and salary are reliable.

ROLE CATEGORY (role_category): decide from the job title first, the responsibilities second.
{role_lines}

ENUMS (use exactly these values):
- role_category: {' | '.join(ROLE_CATEGORIES)}
{enum_lines}

FIELD RULES
- company: the employer, not the job board or a staffing agency's client placeholder. null if unstated.
- title_normalized: the title without req IDs, locations, shift notes or work-mode tags
  ("Sr. Data Engineer (Remote) - 6456969" -> "Senior Data Engineer").
- seniority: from the title (intern, junior/new grad/associate -> entry, senior/sr -> senior, staff/principal/
  distinguished -> staff_principal, manager/lead of people -> manager, director/VP/head -> director_plus).
  If the title has no level, use the minimum required years: under 2 -> entry, 2-4 -> mid, 5+ -> senior.
  Otherwise unknown.
- employment_type: as stated. If several apply, pick the first of internship, co_op, contract, temporary,
  part_time, full_time. unknown when the posting doesn't say.
- work_mode: from explicit wording or the location string. "remote", "work from anywhere", "Remote - USA" ->
  remote. A required share of office time (days per week, a percentage) -> hybrid, even when the location
  says remote-friendly. "on-site", "in office 5 days", "not remote" -> onsite. Nothing explicit -> unknown.
- locations: each listed place as city / state / country. US states as 2-letter codes, the US as "US".
  Leave out "Remote" itself; remote_region holds where a remote worker may live ("US", "EMEA", "Canada").
- salary_min / salary_max: base pay as plain numbers (no commas, "$120K" -> 120000). If several base-pay
  ranges are given (by location or level), use the lowest minimum and the highest maximum. A single figure
  goes in both. Exclude equity, bonus and on-target-earnings extras unless that is the only figure.
  salary_currency as an ISO code ("USD"); salary_period: year, month or hour. All null with no pay info.
- posted_date / application_deadline / start_date: YYYY-MM-DD. Only a stated closing date is a deadline
  ("applications close 08/24/2026"); "accepted at least until <date>" and "rolling" are not (null).
  Resolve relative dates against today.
- summary: at most 2 sentences in your own words: what the role does and for which team/product.
  No marketing language, no company praise.
- team: the team or org named in the posting, else null.
- responsibilities / required_qualifications / preferred_qualifications: short phrases, one requirement per
  item, in the posting's order. "basic", "minimum", "required", "must have", "what we need to see" ->
  required_qualifications. "preferred", "nice to have", "bonus", "ways to stand out", "strongly desired" ->
  preferred_qualifications. Leave out EEO, legal, privacy, benefits and application-process text.
- skills_required / skills_preferred: concrete tools, languages, frameworks and methods named in the
  required / preferred qualifications ("Python", "PyTorch", "SQL", "Kubernetes"). One per item, no duplicates.
- education_level: the minimum degree required (alternatives like "Bachelor's + 7 years or PhD" -> the
  lowest degree). education_fields: the fields named ("Computer Science").
- years_experience_min / years_experience_max: from the required qualifications only; with degree-dependent
  alternatives, use the figure for the lowest degree. "5+ years" -> min 5, max null.
- visa_sponsorship: yes or no only when stated ("we sponsor visas", "no sponsorship", "must be authorized
  to work without sponsorship" -> no). Otherwise unknown.
- security_clearance: required, preferred or none only when stated; otherwise unknown.
- travel: the stated travel expectation ("up to 25%"), else null.
- benefits: short items (401(k), equity, medical, parental leave) only when listed.
- company_blurb: one sentence on what the company does, from its own description, else null.
"""


# Paragraphs that are boilerplate for extraction: EEO, accommodations, privacy, scams, AI-in-hiring notices.
_BOILERPLATE = re.compile(
    r"equal (?:employment )?opportunity|without regard to|protected (?:veteran|by law)|reasonable accommodation|"
    r"accommodations?\b|privacy (?:notice|policy)|e-verify|know your rights|fraudulent|scam|recruitment fees|"
    r"arbitration|ai tools|pay transparency information|#li-|candidate privacy",
    re.IGNORECASE,
)
# Never cut these, even near the end of a posting: they carry pay and dates.
_KEEP = re.compile(r"[$€£]\s?\d|\bsalary\b|\bcompensation\b|\bpay range\b|\bclose[sd]? on\b|\bdeadline\b",
                   re.IGNORECASE)


def truncate_description(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Fit a description in `limit` characters, dropping boilerplate paragraphs from the end first.

    Paragraphs mentioning pay or a closing date are kept. If the text is still too long, it is cut at
    `limit` and any pay/deadline paragraphs from the cut-off tail are appended.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    paras = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    for i in range(len(paras) - 1, -1, -1):
        if sum(len(p) + 1 for p in paras) <= limit:
            break
        if _BOILERPLATE.search(paras[i]) and not _KEEP.search(paras[i]):
            del paras[i]
    text = "\n".join(paras)
    if len(text) <= limit:
        return text
    keep_tail = [p for p in text[limit:].split("\n") if _KEEP.search(p)]
    tail = "\n".join(keep_tail)[: limit // 4]
    head = text[: limit - len(tail) - len("\n[...]\n")].rstrip()
    return f"{head}\n[...]\n{tail}".rstrip() if tail else head


def job_id(job: dict) -> str:
    """The id a job carries through a batch (same key as its Drive files)."""
    return job_key(job["url"])


def format_batch(jobs: list[dict]) -> str:
    """User message for one extraction request: every job labelled with its JOB_ID and metadata."""
    blocks = []
    for job in jobs:
        site = (job.get("site") or "").strip()
        company = (job.get("company") or "").strip() or (site if site.lower() not in JOB_BOARD_SITES else "")
        source = job.get("strategy") or ""
        if site.lower() in JOB_BOARD_SITES:
            source = f"{source} ({site})" if source else site
        meta = [
            f"JOB_ID: {job_id(job)}",
            f"TITLE: {job.get('title') or ''}",
            f"COMPANY: {company or 'not given'}",
            f"LOCATION: {job.get('location') or 'not given'}",
            f"SALARY: {job.get('salary') or 'not given'}",
            f"SOURCE: {source or 'unknown'}",
        ]
        description = truncate_description(job.get("full_description") or job.get("description") or "")
        blocks.append("\n".join(meta) + "\nDESCRIPTION:\n" + description)
    header = f"{len(jobs)} posting(s). Return exactly {len(jobs)} object(s) in \"jobs\".\n\n"
    return header + "\n\n=====\n\n".join(blocks)
