"""Synthetic extracted jobs and assessment replies for the fit-score tests (no real postings or resumes)."""

import json

POSTING = {
    "company": "Acme Robotics", "title_normalized": "Data Engineer", "role_category": "data_engineering",
    "seniority": "mid", "employment_type": "full_time", "work_mode": "hybrid",
    "locations": [{"city": "Springfield", "state": "IL", "country": "US"}],
    "salary_min": 90000, "salary_max": 110000, "salary_currency": "USD", "salary_period": "year",
    "skills_required": ["SQL", "Python"], "skills_preferred": ["Spark"], "years_experience_min": 3,
    "summary": "Builds data pipelines.",
}


def reply(skills: int = 4, experience: int = 3, domain: int = 3, flags: tuple[str, ...] = (),
          summary: str = "Solid match.") -> str:
    """A structured-output assessment reply."""
    return json.dumps({
        "skills_match": {"score": skills, "evidence": "Has SQL and Python."},
        "experience_match": {"score": experience, "evidence": "Similar level."},
        "domain_interest": {"score": domain, "evidence": "No preference stated."},
        "red_flags": [{"code": c, "note": "seen in posting"} for c in flags],
        "matched_keywords": ["SQL", "Python"],
        "summary": summary,
    })


def add_extracted_job(conn, url: str, title: str = "Data Engineer", posting: dict | None = None, **cols) -> str:
    """Insert a described and extracted job."""
    row = {"url": url, "title": title, "site": "acme", "location": "Springfield, IL",
           "full_description": "Build pipelines.", "details_json": json.dumps(posting or POSTING),
           "extracted_at": "2026-10-01T00:00:00+00:00", **cols}
    conn.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return url


def default_fit(skills: int, experience: int = 3) -> int:
    """The fit score with default preferences and no profile: skills x4, experience match x2, no red flags (1.0) x2,
    and education and experience unknown (0.5) at x4 each."""
    return round(100 * (4 * (skills - 1) / 4 + 2 * (experience - 1) / 4 + 2 + 4 * 0.5 + 4 * 0.5) / 16)
