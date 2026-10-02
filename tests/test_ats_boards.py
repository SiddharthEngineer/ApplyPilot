"""Tests for the Greenhouse / Lever / Ashby discovery source (no network)."""

import sqlite3
from unittest import mock

import httpx
import pytest

from applypilot.discovery import ats_boards as ats

# Trimmed samples recorded from each public API on 2026-10-02 (fields the module reads).
GREENHOUSE = {
    "jobs": [
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "title": "Senior Data Scientist, Growth",
            "location": {"name": "Remote - US"},
            "content": "&lt;div&gt;&lt;p&gt;Build models.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;",
            "company_name": "Acme",
        },
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
            "title": "Account Executive",
            "location": {"name": "Italy (Remote)"},
            "content": "&lt;p&gt;Sell.&lt;/p&gt;",
        },
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/3",
            "title": "Data Scientist",
            "location": {"name": "London, UK"},
            "content": "",
        },
    ],
    "meta": {"total": 3},
}

LEVER = [
    {
        "text": "Software Engineer, Platform",
        "hostedUrl": "https://jobs.lever.co/acme/aaa",
        "applyUrl": "https://jobs.lever.co/acme/aaa/apply",
        "categories": {"location": "New York, NY", "commitment": "Full-time"},
        "workplaceType": "remote",
        "descriptionPlain": "We build the platform.",
        "lists": [{"text": "What you'll do", "content": "<li>Ship code</li>"}],
        "additionalPlain": "Equal opportunity employer.",
        "salaryRange": {"min": 150000, "max": 200000, "currency": "USD", "interval": "per-year-salary"},
    },
    {
        "text": "Android Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/bbb",
        "applyUrl": "https://jobs.lever.co/acme/bbb/apply",
        "categories": {"location": "London"},
        "workplaceType": "hybrid",
        "descriptionPlain": "Mobile.",
    },
]

ASHBY = {
    "apiVersion": "1",
    "jobs": [
        {
            "title": "AI Engineer",
            "location": "San Francisco, CA",
            "isRemote": False,
            "isListed": True,
            "jobUrl": "https://jobs.ashbyhq.com/acme/x1",
            "applyUrl": "https://jobs.ashbyhq.com/acme/x1/application",
            "descriptionPlain": "x" * 300,
            "compensation": {"compensationTierSummary": "$180K – $240K"},
        },
        {
            "title": "AI Engineer (unlisted)",
            "location": "Remote",
            "isRemote": True,
            "isListed": False,
            "jobUrl": "https://jobs.ashbyhq.com/acme/x2",
            "descriptionPlain": "hidden",
        },
        {
            "title": "Solutions Architect",
            "location": "AMER",
            "isRemote": True,
            "isListed": True,
            "jobUrl": "https://jobs.ashbyhq.com/acme/x3",
            "descriptionHtml": "<p>Help customers.</p>",
            "compensation": {},
        },
    ],
}

SAMPLES = {"greenhouse": GREENHOUSE, "lever": LEVER, "ashby": ASHBY}

_RealClient = httpx.Client  # tests patch ats.httpx.Client; keep the real class for the mock transport


def _client_returning(payloads: dict[str, object]) -> httpx.Client:
    """httpx client whose transport answers each ATS URL with the given payload (or status int)."""
    def handler(request: httpx.Request) -> httpx.Response:
        for needle, payload in payloads.items():
            if needle in str(request.url):
                if isinstance(payload, int):
                    return httpx.Response(payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404)
    return _RealClient(transport=httpx.MockTransport(handler))


def _jobs_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, salary TEXT, description TEXT, location TEXT, "
        "site TEXT, strategy TEXT, discovered_at TEXT, full_description TEXT, application_url TEXT, "
        "detail_scraped_at TEXT)"
    )
    return conn


class TestNormalize:
    def test_greenhouse(self):
        jobs = ats.normalize("greenhouse", GREENHOUSE, "Acme")
        j = jobs[0]
        assert j["url"] == "https://job-boards.greenhouse.io/acme/jobs/1"
        assert j["title"] == "Senior Data Scientist, Growth"
        assert j["location"] == "Remote - US"
        assert j["description"] == "Build models.\nPython"
        assert (j["site"], j["strategy"]) == ("Acme", "ats_greenhouse")
        assert j["full_description"] is None  # short description: enrich still fetches it
        assert jobs[2]["description"] is None

    def test_lever(self):
        j = ats.normalize("lever", LEVER, "Acme")[0]
        assert j["url"] == "https://jobs.lever.co/acme/aaa"
        assert j["application_url"] == "https://jobs.lever.co/acme/aaa/apply"
        assert j["title"] == "Software Engineer, Platform"
        assert j["location"] == "New York, NY (Remote)"
        assert "What you'll do" in j["description"] and "Ship code" in j["description"]
        assert j["salary"] == "USD 150,000-200,000/per-year-salary"
        assert j["strategy"] == "ats_lever"

    def test_ashby_skips_unlisted_and_marks_remote(self):
        jobs = ats.normalize("ashby", ASHBY, "Acme")
        assert [j["title"] for j in jobs] == ["AI Engineer", "Solutions Architect"]
        assert jobs[0]["salary"] == "$180K – $240K"
        assert jobs[0]["full_description"] == "x" * 300
        assert jobs[1]["location"] == "AMER (Remote)"
        assert jobs[1]["description"] == "Help customers."


class TestFetchBoard:
    @pytest.mark.parametrize("kind", ["greenhouse", "lever", "ashby"])
    def test_fetch_each_kind(self, kind):
        client = _client_returning({ats.ATS_URLS[kind].split("{")[0]: SAMPLES[kind]})
        jobs = ats.fetch_board(kind, "acme", company="Acme", client=client)
        assert jobs and all(j["strategy"] == f"ats_{kind}" for j in jobs)

    def test_url_uses_slug(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"jobs": []})
        ats.fetch_board("greenhouse", "acme", client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert seen == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"]

    def test_http_error_raises(self):
        with pytest.raises(httpx.HTTPStatusError):
            ats.fetch_board("lever", "nope", client=_client_returning({"api.lever.co": 404}))

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            ats.fetch_board("workable", "acme")


class TestTitleMatches:
    @pytest.mark.parametrize("title,expected", [
        ("Senior Data Scientist, Growth", True),
        ("data scientist", True),
        ("Scientist, Data Platform", True),
        ("Account Executive", False),
        ("Software Engineer II", True),
        ("Software Engineers, Platform", True),
        ("Manager, Software Engineering", False),
        ("Account Executive | Remote, Spain", False),
        ("ML Engineer (C++)", False),
    ])
    def test_match(self, title, expected):
        assert ats.title_matches(title, ["Data Scientist", "Software Engineer", "AI Engineer"]) is expected

    def test_empty_query_ignored(self):
        assert not ats.title_matches("Anything", ["", "  "])


class TestRunAtsDiscovery:
    BOARDS = (
        {"name": "Acme GH", "kind": "greenhouse", "slug": "acme"},
        {"name": "Acme Lever", "kind": "lever", "slug": "acme"},
        {"name": "Acme Ashby", "kind": "ashby", "slug": "acme"},
    )

    def _run(self, payloads, queries=("Data Scientist", "Software Engineer", "AI Engineer"),
             accept=("California", "United States", "US"), reject=()):
        conn = _jobs_db()
        with mock.patch.object(ats.httpx, "Client", lambda **kw: _client_returning(payloads)):
            stats = ats.run_ats_discovery(list(queries), list(accept), list(reject),
                                          boards=list(self.BOARDS), conn=conn)
        return stats, conn

    def test_filters_titles_and_locations_and_stores(self):
        stats, conn = self._run({"greenhouse.io": GREENHOUSE, "lever.co": LEVER, "ashbyhq.com": ASHBY})
        rows = conn.execute("SELECT site, strategy, title, location FROM jobs ORDER BY url").fetchall()
        # GH: remote Data Scientist kept; London Data Scientist dropped by location; AE dropped by title.
        # Lever: remote SWE kept; Android Engineer dropped by title.
        titles = {r[2] for r in rows}
        assert "Senior Data Scientist, Growth" in titles
        assert "Software Engineer, Platform" in titles
        assert "Data Scientist" not in titles
        assert "Account Executive" not in titles
        assert {r[1] for r in rows} <= {"ats_greenhouse", "ats_lever", "ats_ashby"}
        assert stats["new"] == len(rows)
        assert stats["errors"] == 0
        assert stats["per_board"]["greenhouse:acme"]["jobs"] == 3

    def test_second_run_counts_dupes(self):
        payloads = {"greenhouse.io": GREENHOUSE, "lever.co": LEVER, "ashbyhq.com": ASHBY}
        conn = _jobs_db()
        with mock.patch.object(ats.httpx, "Client", lambda **kw: _client_returning(payloads)):
            first = ats.run_ats_discovery(["Data Scientist"], ["US"], [], boards=list(self.BOARDS), conn=conn)
            second = ats.run_ats_discovery(["Data Scientist"], ["US"], [], boards=list(self.BOARDS), conn=conn)
        assert first["new"] == 1
        assert (second["new"], second["existing"]) == (0, 1)

    def test_one_board_failing_does_not_stop_others(self):
        stats, conn = self._run({"greenhouse.io": 500, "lever.co": LEVER, "ashbyhq.com": ASHBY})
        assert stats["errors"] == 1
        assert "error" in stats["per_board"]["greenhouse:acme"]
        assert stats["new"] >= 1
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE strategy = 'ats_lever'").fetchone()[0] == 1

    def test_full_description_marks_detail_scraped(self):
        _, conn = self._run({"ashbyhq.com": ASHBY, "greenhouse.io": {"jobs": []}, "lever.co": []},
                            queries=["AI Engineer"], accept=["CA"])
        row = conn.execute("SELECT full_description, detail_scraped_at, salary FROM jobs").fetchone()
        assert row[0] == "x" * 300 and row[1] is not None and row[2] == "$180K – $240K"

    def test_no_boards_is_noop(self):
        stats = ats.run_ats_discovery(["x"], [], [], boards=[], conn=_jobs_db())
        assert stats["boards"] == 0 and stats["new"] == 0


def test_shipped_config_is_valid():
    boards = ats.load_ats_boards()
    assert len(boards) >= 15
    assert {b["kind"] for b in boards} == {"greenhouse", "lever", "ashby"}
    assert len({(b["kind"], b["slug"]) for b in boards}) == len(boards)
