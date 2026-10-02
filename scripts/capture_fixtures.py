#!/usr/bin/env python3
"""Capture real pipeline objects as pickle/JSON fixtures for smoke tests.

Usage:
    python scripts/capture_fixtures.py --n 1
    python scripts/capture_fixtures.py --n 1 --sites indeed,linkedin
    python scripts/capture_fixtures.py --out tests/data --scrub   # committed JSON samples (see below)

Creates:
    tests/fixtures/jobs_raw.pkl       — DataFrame rows + _site_counts + DB rows
    tests/fixtures/jobs_enriched.json  — 3 jobs with full_description
    tests/fixtures/profile_anonymized.json — from profile.example.json (no secrets)
    tests/fixtures/resume_sample.txt   — empty placeholder
    tests/fixtures/smartextract_intel_sample.pkl — 1 site collect_intelligence output

All files are gitignored. Re-running overwrites deterministically.

With --out/--scrub, writes small JSON samples meant to be committed (public repo):
    <out>/jobspy_<site>.json          — 5 raw JobSpy rows per board
    <out>/workday_search.json         — one raw Workday CXS listing page
    <out>/gemini_score_response.json  — one raw Gemini scoring reply (scored against a fake resume)
--scrub replaces anything from your profile.json/resume.txt (name, email, phone, URLs, companies,
school, passwords) plus any email address, and caps descriptions at 2,000 characters.
Check before committing: grep -ri "<your email>" tests/data  (expect no matches).
"""

import argparse
import json
import math
import os
import pickle
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _setup_isolated_env(tmp_dir: Path) -> None:
    """Point ApplyPilot at a temporary directory."""
    os.environ["APPLYPILOT_DIR"] = str(tmp_dir)
    (tmp_dir / "tailored_resumes").mkdir(exist_ok=True)
    (tmp_dir / "cover_letters").mkdir(exist_ok=True)
    (tmp_dir / "logs").mkdir(exist_ok=True)


def _capture_jobs_raw(conn: sqlite3.Connection, out_dir: Path) -> None:
    """Pickle raw discovered jobs + site counts."""
    from applypilot.database import get_jobs_by_stage

    jobs = get_jobs_by_stage(conn, stage="discovered", limit=100)

    data = {
        "jobs": jobs,
        "count": len(jobs),
    }

    with open(out_dir / "jobs_raw.pkl", "wb") as f:
        pickle.dump(data, f)

    print(f"  jobs_raw.pkl: {len(jobs)} jobs")


def _capture_jobs_enriched(conn: sqlite3.Connection, out_dir: Path) -> None:
    """Capture enriched jobs with full_description."""
    from applypilot.database import get_jobs_by_stage

    jobs = get_jobs_by_stage(conn, stage="enriched", limit=3)

    # Truncate full_description to 6000 chars (matches scorer.py:89)
    for job in jobs:
        if job.get("full_description") and len(job["full_description"]) > 6000:
            job["full_description"] = job["full_description"][:6000] + "..."

    with open(out_dir / "jobs_enriched.json", "w") as f:
        json.dump(jobs, f, indent=2, default=str)

    print(f"  jobs_enriched.json: {len(jobs)} jobs")


def _capture_profile(out_dir: Path) -> None:
    """Copy profile.example.json as anonymized profile."""
    profile_path = Path(__file__).parent.parent / "profile.example.json"
    if profile_path.exists():
        shutil.copy(profile_path, out_dir / "profile_anonymized.json")
        print("  profile_anonymized.json: copied from profile.example.json")
    else:
        # Create minimal placeholder
        placeholder = {
            "name": "Test User",
            "email": "test@example.com",
            "phone": "555-0100",
            "location": "San Francisco, CA",
            "linkedin": "https://linkedin.com/in/test",
            "github": "https://github.com/test",
        }
        with open(out_dir / "profile_anonymized.json", "w") as f:
            json.dump(placeholder, f, indent=2)
        print("  profile_anonymized.json: created placeholder")


SAMPLE_RESUME = """Test User
San Francisco, CA | test@example.com | 555-0100

EXPERIENCE

Software Engineer
Test Corp | 2020 - Present
- Built scalable backend systems
- Led team of 3 engineers

EDUCATION

BS Computer Science
University of California | 2016 - 2020

SKILLS
Python, JavaScript, SQL, Git, Docker, AWS
"""


def _capture_resume(out_dir: Path) -> None:
    """Create a minimal sample resume."""
    resume_text = SAMPLE_RESUME
    with open(out_dir / "resume_sample.txt", "w") as f:
        f.write(resume_text)
    print("  resume_sample.txt: created")


def _capture_smartextract_intel(out_dir: Path) -> None:
    """Capture a sample SmartExtract intelligence dict."""
    intel = {
        "site_name": "Hacker News Jobs",
        "url": "https://news.ycombinator.com/jobs",
        "strategy": {"type": "static", "selectors": {"title": ".titleline a"}},
        "card_candidates": [{"title": "Test Engineer", "url": "https://example.com/job/1"}],
        "json_ld": [],
        "raw_html_snippet": "<div class='titleline'><a href='https://example.com/job/1'>Test Engineer</a></div>",
    }

    with open(out_dir / "smartextract_intel_sample.pkl", "wb") as f:
        pickle.dump(intel, f)
    print("  smartextract_intel_sample.pkl: created")


# -- Committed JSON samples (--out/--scrub) ----------------------------------

DESC_CAP = 2000
REDACTED = "[REDACTED]"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_URL_RE = re.compile(r"https?://\S+")

_PROFILE_KEYS = {
    "personal": ["full_name", "preferred_name", "email", "phone", "address", "postal_code",
                 "linkedin_url", "github_url", "portfolio_url", "website_url"],
    "site_passwords": None,  # every value
    "resume_facts": ["preserved_companies", "preserved_projects", "preserved_school"],
}


def _personal_terms(app_dir: Path) -> list[str]:
    """Strings from the user's profile.json and resume.txt that must never appear in committed data."""
    terms: set[str] = set()
    profile_path = app_dir / "profile.json"
    if profile_path.exists():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        for section, keys in _PROFILE_KEYS.items():
            data = profile.get(section) or {}
            for key in (keys if keys is not None else list(data)):
                val = data.get(key)
                for v in (val if isinstance(val, list) else [val]):
                    if isinstance(v, str) and v.strip():
                        terms.add(v.strip())
        for key in ("full_name", "preferred_name"):
            name = (profile.get("personal") or {}).get(key) or ""
            terms.update(part for part in name.split() if len(part) >= 3)
    resume_path = app_dir / "resume.txt"
    if resume_path.exists():
        text = resume_path.read_text(encoding="utf-8", errors="ignore")
        terms.update(_EMAIL_RE.findall(text))
        terms.update(_PHONE_RE.findall(text))
        terms.update(u.rstrip(").,") for u in _URL_RE.findall(text))
    return sorted((t for t in terms if len(t) >= 3), key=len, reverse=True)


def scrub_text(text: str, terms: list[str]) -> str:
    """Replace personal terms (case-insensitive) and any email address."""
    for term in terms:
        text = re.sub(re.escape(term), REDACTED, text, flags=re.IGNORECASE)
    return _EMAIL_RE.sub(REDACTED, text)


def scrub(obj, terms: list[str]):
    """Recursively scrub strings; cap `description`-like fields at DESC_CAP characters."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = scrub(v, terms)
            if isinstance(v, str) and "description" in k.lower() and len(v) > DESC_CAP:
                v = v[:DESC_CAP]
            out[k] = v
        return out
    if isinstance(obj, list):
        return [scrub(v, terms) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj, terms)
    return obj


def _json_safe(value):
    """Make a DataFrame cell JSON-serializable (NaN -> None, dates -> ISO strings)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _write_json(path: Path, data, terms: list[str], do_scrub: bool) -> None:
    if do_scrub:
        data = scrub(data, terms)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {path.name}: written")


def _capture_jobspy_samples(out_dir: Path, sites: list[str], terms, do_scrub) -> None:
    from jobspy import scrape_jobs

    for site in sites:
        try:
            df = scrape_jobs(site_name=[site], search_term="software engineer", location="San Francisco, CA",
                             results_wanted=5, hours_old=72, description_format="markdown")
        except Exception as e:  # noqa: BLE001 — capture is best-effort per board
            print(f"  jobspy_{site}.json: skipped ({type(e).__name__}: {e})")
            continue
        rows = [{k: _json_safe(v) for k, v in row.items()} for row in df.head(5).to_dict("records")]
        if not rows:
            print(f"  jobspy_{site}.json: skipped (0 rows)")
            continue
        _write_json(out_dir / f"jobspy_{site}.json", rows, terms, do_scrub)


def _capture_workday_sample(out_dir: Path, terms, do_scrub) -> None:
    from applypilot.discovery.workday import load_employers, workday_search

    for key, employer in load_employers().items():
        try:
            data = workday_search(employer, "software engineer", limit=5)
        except Exception as e:  # noqa: BLE001 — try the next employer
            print(f"  workday {key}: {type(e).__name__}: {e}")
            continue
        if data.get("jobPostings"):
            data["jobPostings"] = data["jobPostings"][:5]
            _write_json(out_dir / "workday_search.json", {"employer_key": key, "response": data}, terms, do_scrub)
            return
    print("  workday_search.json: skipped (no employer returned postings)")


def _capture_gemini_sample(out_dir: Path, terms, do_scrub) -> None:
    from applypilot.llm import get_client
    from applypilot.scoring.scorer import SCORE_PROMPT

    job_path = out_dir / "jobspy_indeed.json"
    jobs = json.loads(job_path.read_text(encoding="utf-8")) if job_path.exists() else []
    job = next((j for j in jobs if j.get("description")), None)
    if job is None:
        print("  gemini_score_response.json: skipped (no captured job with a description)")
        return
    job_text = (f"TITLE: {job.get('title')}\nCOMPANY: {job.get('site')}\nLOCATION: {job.get('location')}\n\n"
                f"DESCRIPTION:\n{job['description'][:DESC_CAP]}")
    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{SAMPLE_RESUME}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]
    try:
        client = get_client()
        response = client.chat(messages, max_tokens=512, temperature=0.2)
    except Exception as e:  # noqa: BLE001 — capture is best-effort
        print(f"  gemini_score_response.json: skipped ({type(e).__name__}: {e})")
        return
    _write_json(out_dir / "gemini_score_response.json",
                {"model": client.model, "job_url": job.get("job_url"), "response": response}, terms, do_scrub)


def capture_samples(out_dir: Path, sites: list[str], do_scrub: bool, real_app_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    terms = _personal_terms(real_app_dir) if do_scrub else []
    print(f"Capturing JSON samples to {out_dir} (scrub={'on, %d terms' % len(terms) if do_scrub else 'off'})...")
    _capture_jobspy_samples(out_dir, sites, terms, do_scrub)
    _capture_workday_sample(out_dir, terms, do_scrub)
    _capture_gemini_sample(out_dir, terms, do_scrub)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture pipeline fixtures for smoke tests")
    parser.add_argument("--n", type=int, default=1, help="Number of jobs to capture (default: 1)")
    parser.add_argument("--sites", type=str, default="indeed,linkedin", help="Comma-separated JobSpy sites")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write committed JSON samples here (e.g. tests/data) instead of pickle fixtures")
    parser.add_argument("--scrub", action="store_true", help="Remove personal data from --out samples")
    args = parser.parse_args()

    if args.out is not None:
        real_app_dir = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))
        from applypilot.config import load_env
        load_env()
        sites = [s.strip() for s in args.sites.split(",")]
        capture_samples(args.out, sites, args.scrub, real_app_dir)
        if not args.scrub:
            print("WARNING: --scrub was not set; do not commit these files.")
        return

    out_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)

    sites = [s.strip() for s in args.sites.split(",")]

    print(f"Capturing fixtures (n={args.n}, sites={sites})...")

    with tempfile.TemporaryDirectory(prefix="applypilot_capture_") as tmp:
        tmp_dir = Path(tmp)
        _setup_isolated_env(tmp_dir)

        # Import after env setup
        from applypilot.database import get_stats, init_db

        conn = init_db(tmp_dir / "applypilot.db")

        # Run discovery
        from applypilot.discovery.jobspy import search_jobs

        print(f"  Running JobSpy search (sites={sites})...")
        try:
            result = search_jobs(
                query="software engineer",
                location="San Francisco, CA",
                sites=sites,
                results_per_site=args.n,
                hours_old=72,
            )
            print(f"  JobSpy result: {result.get('total', 0)} jobs")
        except (RuntimeError, OSError) as e:
            print(f"  JobSpy error (non-fatal): {e}")

        stats = get_stats(conn)
        print(f"  DB stats: {stats['total']} total jobs")

        # Capture fixtures
        _capture_jobs_raw(conn, out_dir)
        _capture_jobs_enriched(conn, out_dir)
        _capture_profile(out_dir)
        _capture_resume(out_dir)
        _capture_smartextract_intel(out_dir)

    print(f"\nFixtures saved to {out_dir}")
    print("These files are gitignored — do not commit them.")


if __name__ == "__main__":
    main()
