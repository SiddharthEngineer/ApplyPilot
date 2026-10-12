# tests/data

`jobspy_*.json`, `workday_search.json` and `gemini_score_response.json` are **synthetic**: hand-written on 2026-10-12 to match
the live JobSpy, Workday CXS and Gemini response shapes, with fictional companies and no personal data.
`postings/` holds real public job postings.
To refresh from live sources, run `python scripts/capture_fixtures.py --out tests/data --scrub` yourself, then grep the
output for personal data (names, emails, phones) before committing.
