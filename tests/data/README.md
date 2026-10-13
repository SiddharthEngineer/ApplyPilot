# tests/data

`jobspy_*.json`, `workday_search.json` and `claude_assess_response.json` are **synthetic**: hand-written on 2026-10-12 (the assessment reply on 2026-10-13) to match
the live JobSpy, Workday CXS and Claude structured-output response shapes, with fictional companies and no personal data.
`postings/` holds real public job postings.
To refresh from live sources, run `python scripts/capture_fixtures.py --out tests/data --scrub` yourself, then grep the
output for personal data (names, emails, phones) before committing.
