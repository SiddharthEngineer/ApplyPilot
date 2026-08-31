#!/usr/bin/env python3
"""Capture real pipeline objects as pickle/JSON fixtures for smoke tests.

Usage:
    python scripts/capture_fixtures.py --n 1
    python scripts/capture_fixtures.py --n 1 --sites indeed,linkedin

Creates:
    tests/fixtures/jobs_raw.pkl       — DataFrame rows + _site_counts + DB rows
    tests/fixtures/jobs_enriched.json  — 3 jobs with full_description
    tests/fixtures/profile_anonymized.json — from profile.example.json (no secrets)
    tests/fixtures/resume_sample.txt   — empty placeholder
    tests/fixtures/smartextract_intel_sample.pkl — 1 site collect_intelligence output

All files are gitignored. Re-running overwrites deterministically.
"""

import argparse
import json
import os
import pickle
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


def _capture_resume(out_dir: Path) -> None:
    """Create a minimal sample resume."""
    resume_text = """Test User
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture pipeline fixtures for smoke tests")
    parser.add_argument("--n", type=int, default=1, help="Number of jobs to capture (default: 1)")
    parser.add_argument("--sites", type=str, default="indeed,linkedin", help="Comma-separated JobSpy sites")
    args = parser.parse_args()

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
