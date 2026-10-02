"""Recorded-response tier: real board/LLM samples committed in tests/data/ (no network).

Samples are written by `python scripts/capture_fixtures.py --out tests/data --scrub`. Tests for a sample
skip until it has been captured and committed; the scrubber tests always run.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

DATA_DIR = Path(__file__).parent / "data"

_spec = importlib.util.spec_from_file_location(
    "capture_fixtures", Path(__file__).resolve().parent.parent / "scripts" / "capture_fixtures.py"
)
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)


def _load(name: str):
    path = DATA_DIR / name
    if not path.exists():
        pytest.skip(f"{name} not captured yet (scripts/capture_fixtures.py --out tests/data --scrub)")
    return json.loads(path.read_text(encoding="utf-8"))


# -- Scrubber ----------------------------------------------------------------

@pytest.fixture
def app_dir(tmp_path):
    (tmp_path / "profile.json").write_text(json.dumps({
        "personal": {"full_name": "Jane Q Doeman", "preferred_name": "", "email": "jane@doeman.dev",
                     "phone": "555-867-5309", "linkedin_url": "https://linkedin.com/in/janedoeman"},
        "site_passwords": {"workday": "hunter2secret"},
        "resume_facts": {"preserved_companies": ["Acme Widgets"], "preserved_school": "State Tech"},
        "skills_boundary": {"languages": ["Python"]},
    }))
    (tmp_path / "resume.txt").write_text("Jane Doeman | (555) 867-5309 | jane.alt@mail.example | https://janedoeman.dev\n")
    return tmp_path


def test_personal_terms(app_dir):
    terms = capture._personal_terms(app_dir)
    for expected in ["Jane Q Doeman", "Doeman", "Jane", "jane@doeman.dev", "hunter2secret", "Acme Widgets",
                     "State Tech", "(555) 867-5309", "https://janedoeman.dev"]:
        assert expected in terms
    assert "Python" not in terms  # skills aren't personal; scrubbing them would distort job text
    assert terms == sorted(terms, key=len, reverse=True)  # longest first, so full names win over parts


def test_scrub_removes_personal_data(app_dir):
    terms = capture._personal_terms(app_dir)
    data = {
        "title": "Engineer at ACME WIDGETS",
        "description": "Contact jane@doeman.dev or recruiter@corp.example. JANE DOEMAN. " + "x" * 5000,
        "nested": [{"note": "pw hunter2secret, State Tech alum"}],
        "min_amount": 100000,
    }
    out = capture.scrub(data, terms)
    blob = json.dumps(out).lower()
    for leaked in ["acme widgets", "doeman", "jane", "hunter2secret", "state tech", "@corp.example"]:
        assert leaked not in blob
    assert len(out["description"]) == capture.DESC_CAP
    assert out["min_amount"] == 100000


def test_json_safe():
    assert capture._json_safe(float("nan")) is None
    assert capture._json_safe(pd.Timestamp("2026-10-02")) == "2026-10-02T00:00:00"
    assert capture._json_safe(3) == 3


# -- Committed samples -------------------------------------------------------

@pytest.mark.parametrize("site", ["indeed", "linkedin"])
def test_jobspy_rows_store(site, tmp_path):
    from applypilot.database import init_db
    from applypilot.discovery.jobspy import store_jobspy_results

    rows = _load(f"jobspy_{site}.json")
    assert 1 <= len(rows) <= 5
    conn = init_db(tmp_path / "t.db")
    new, existing = store_jobspy_results(conn, pd.DataFrame(rows), site)
    assert new == len({r["job_url"] for r in rows if r.get("job_url")})
    stored = conn.execute("SELECT url, title FROM jobs").fetchall()
    assert all(url and title for url, title in stored)


def test_workday_page_parses():
    from applypilot.discovery.workday import search_employer

    sample = _load("workday_search.json")
    employer = {"name": sample["employer_key"], "base_url": "https://x", "tenant": "t", "site_id": "s"}
    with patch("applypilot.discovery.workday.workday_search", return_value=sample["response"]):
        jobs = search_employer(sample["employer_key"], employer, "software engineer",
                               location_filter=False, max_results=5)
    assert jobs and all(j["title"] and j["external_path"] for j in jobs)


def test_gemini_score_response_parses():
    from applypilot.scoring.scorer import _parse_score_response

    sample = _load("gemini_score_response.json")
    parsed = _parse_score_response(sample["response"])
    assert 1 <= parsed["score"] <= 10
    assert parsed["reasoning"]


def test_committed_data_has_no_personal_markers():
    for path in DATA_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not capture._EMAIL_RE.search(text), f"email address in {path.name}"
