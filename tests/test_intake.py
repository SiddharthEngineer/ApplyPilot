"""Add job by link or paste (strelitzia.intake), with the scrape, extraction and scoring faked."""

import pytest

from strelitzia import intake, users
from strelitzia.database import init_db, job_company, job_key
from strelitzia.enrichment import extract
from strelitzia.enrichment.posting_model import JobPosting, Location
from strelitzia.llm import LLMStopRun
from strelitzia.web import tasks

LONG = "We are hiring a data scientist to build forecasting models. " * 12  # ~700 chars


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "jobs.db")


@pytest.fixture
def uid(conn):
    return users.owner(conn).id


@pytest.fixture
def fakes(monkeypatch):
    """Records calls; the scrape returns `fakes.result`, extraction a posting, scoring sets a fit score."""

    class Fakes:
        result = {"status": "ok", "full_description": LONG, "application_url": "https://acme.com/apply"}
        scrapes: list = []
        extracted: list = []
        scored: list = []
        score_error: Exception | None = None

    f = Fakes()

    def scrape(url):
        f.scrapes.append(url)
        return dict(f.result)

    def extract_one(job, today=None):
        f.extracted.append(job["full_description"])
        return JobPosting(company="Acme", title_normalized="Data Scientist",
                          locations=[Location(city="Austin", state="TX", country="US")])

    def score(user_id, url):
        if f.score_error:
            raise f.score_error
        f.scored.append(url)
        f.conn.execute("UPDATE jobs SET fit_score = 80 WHERE user_id = ? AND url = ?", (user_id, url))

    monkeypatch.setattr(intake, "_scrape", scrape)
    monkeypatch.setattr(extract, "extract_one", extract_one)
    monkeypatch.setattr(intake, "_score", score)
    return f


def _run(conn, fakes, url):
    fakes.conn = conn
    uid = users.owner(conn).id
    intake.run_intake(uid, url, conn)
    return dict(conn.execute("SELECT * FROM jobs WHERE user_id = ? AND url = ?", (uid, url)).fetchone())


@pytest.mark.parametrize(("raw", "expected"), [
    ("  https://boards.greenhouse.io/acme/jobs/123?utm_source=x&gh_jid=123&trk=abc#apply ",
     "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123"),
    ("jobs.lever.co/acme/abc-def?src=linkedin&refId=1&trackingId=2", "https://jobs.lever.co/acme/abc-def"),
    ("https://www.linkedin.com/jobs/view/4012345678/?refId=x&trackingId=y", "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://linkedin.com/jobs/view/senior-data-scientist-at-acme-4012345678", "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=data", "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.indeed.com/viewjob?jk=abc123&from=serp", "https://www.indeed.com/viewjob?jk=abc123&from=serp"),
    ("https://www.linkedin.com/jobs/view/python-3-developer-at-acme-3812345678/", "https://www.linkedin.com/jobs/view/3812345678/"),
    ("https://careers.example.com/#/job/123?utm_source=x", "https://careers.example.com/#/job/123?utm_source=x"),
    ("https://careers.example.com/jobs?id=9#apply", "https://careers.example.com/jobs?id=9"),
])
def test_normalize_url(raw, expected):
    assert intake.normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "mailto:a@b.com", "ftp://example.com/x"])
def test_normalize_url_rejects_non_web_links(raw):
    with pytest.raises(ValueError):
        intake.normalize_url(raw)


def test_add_job_by_link_and_dedupe(conn, uid):
    row, created = intake.add_job(conn, uid, "https://www.linkedin.com/jobs/view/42/?trk=feed")
    assert created
    assert row["url"] == "https://www.linkedin.com/jobs/view/42/"
    assert row["job_key"] == job_key(row["url"])
    assert (row["source"], row["strategy"], row["site"], row["intake_status"]) == ("manual", "manual", "linkedin", "queued")
    assert row["added_at"] == row["discovered_at"]
    assert row["full_description"] is None

    again, created = intake.add_job(conn, uid, "https://www.linkedin.com/jobs/search/?currentJobId=42")
    assert not created and again["url"] == row["url"]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.parametrize(("stored", "pasted"), [
    ("https://www.linkedin.com/jobs/view/senior-eng-at-acme-381234?trk=x", "https://linkedin.com/jobs/view/381234"),
    ("https://boards.greenhouse.io/acme/jobs/7?gh_src=1&utm_source=li", "https://boards.greenhouse.io/acme/jobs/7?gh_src=1"),
])
def test_dedupes_against_another_form_of_a_discovered_link(conn, uid, stored, pasted):
    conn.execute("INSERT INTO jobs (url, title) VALUES (?, 'Old')", (stored,))
    conn.commit()
    row, created = intake.add_job(conn, uid, pasted)
    assert not created and row["url"] == stored and row["source"] == "manual"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_readding_a_discovered_job_marks_it_manual(conn, uid):
    url = "https://boards.greenhouse.io/acme/jobs/7"
    conn.execute("INSERT INTO jobs (url, title, site, discovered_at, fit_flags) VALUES (?, 'Old', 'Acme', 'then', ?)",
                 (url, '["prefilter"]'))
    conn.commit()
    row, created = intake.add_job(conn, uid, url + "?utm_campaign=x", intake.ManualFields(title="New", company="Acme Inc"))
    assert not created
    assert (row["source"], row["title"], row["company"]) == ("manual", "Old", "Acme Inc")
    assert row["added_at"] and row["discovered_at"] == "then"


def test_paste_only_job_gets_a_synthetic_url(conn, uid, fakes):
    row, created = intake.add_job(conn, uid, None, intake.ManualFields(title="Analyst", company="Globex", description=LONG))
    assert created and row["url"].startswith("manual://") and len(row["url"]) == len("manual://") + 32
    assert row["full_description"] == LONG.strip() and row["detail_scraped_at"]
    assert row["site"] is None and job_company(row) == "Globex"

    done = _run(conn, fakes, row["url"])
    assert fakes.scrapes == [] and fakes.scored == [row["url"]]
    assert (done["intake_status"], done["title"], done["location"]) == ("ok", "Analyst", "Austin, TX, US")

    again, created = intake.add_job(conn, uid, row["url"])
    assert not created and again["url"] == row["url"]
    with pytest.raises(ValueError):
        intake.add_job(conn, uid, "manual://" + "0" * 32)
    with pytest.raises(ValueError):
        intake.add_job(conn, uid, None, intake.ManualFields(title="No description"))
    with pytest.raises(ValueError):
        intake.add_job(conn, uid, "  ")


def test_manual_link_rows_never_use_the_hostname_as_company(conn, uid):
    row, _ = intake.add_job(conn, uid, "https://jobs.lever.co/acme/1")
    assert row["site"] == "jobs.lever.co" and job_company(row) == "Unknown company"


def test_intake_scrapes_extracts_and_scores(conn, uid, fakes):
    row, _ = intake.add_job(conn, uid, "https://boards.greenhouse.io/acme/jobs/1")
    done = _run(conn, fakes, row["url"])
    assert fakes.scrapes == [row["url"]] and fakes.extracted == [LONG]
    assert done["full_description"] == LONG and done["application_url"] == "https://acme.com/apply"
    assert (done["title"], done["company"], done["location"]) == ("Data Scientist", "Acme", "Austin, TX, US")
    assert done["details_json"] and done["fit_score"] == 80
    assert (done["intake_status"], done["intake_error"]) == ("ok", None)


def test_a_scrape_without_an_apply_link_keeps_the_stored_one(conn, uid, fakes):
    url = "https://boards.greenhouse.io/acme/jobs/9"
    conn.execute("INSERT INTO jobs (url, application_url) VALUES (?, 'https://acme.com/apply/9')", (url,))
    conn.commit()
    intake.add_job(conn, uid, url)
    fakes.result = {"status": "partial", "full_description": LONG, "application_url": None}
    assert _run(conn, fakes, url)["application_url"] == "https://acme.com/apply/9"


def test_login_wall_needs_paste(conn, uid, fakes):
    fakes.result = {"status": "ok", "full_description": "Sign in to view more jobs. Join LinkedIn today. " * 20,
                    "final_url": "https://www.linkedin.com/authwall?trk=x"}
    row, _ = intake.add_job(conn, uid, "https://www.linkedin.com/jobs/view/42/")
    done = _run(conn, fakes, row["url"])
    assert done["intake_status"] == "needs_paste" and "sign-in" in done["intake_error"]
    assert done["full_description"] is None and fakes.extracted == [] and fakes.scored == []


def test_thin_text_or_failed_scrape_needs_paste(conn, uid, fakes):
    fakes.result = {"status": "partial", "full_description": "Engineer. Apply now.", "application_url": "https://x/apply"}
    row, _ = intake.add_job(conn, uid, "https://careers.example.com/jobs/1")
    done = _run(conn, fakes, row["url"])
    assert done["intake_status"] == "needs_paste" and "20 characters" in done["intake_error"]
    assert done["description"] == "Engineer. Apply now." and done["application_url"] == "https://x/apply"

    fakes.result = {"status": "error", "full_description": None, "error": "HTTP 404"}
    row, _ = intake.add_job(conn, uid, "https://careers.example.com/jobs/2")
    done = _run(conn, fakes, row["url"])
    assert done["intake_status"] == "needs_paste" and "HTTP 404" in done["intake_error"]


def test_is_unreadable_accepts_a_real_linkedin_posting():
    result = {"status": "ok", "full_description": LONG * 3}
    assert intake.is_unreadable(result, "https://www.linkedin.com/jobs/view/42/") is None


def test_paste_then_reintake_skips_the_scrape(conn, uid, fakes):
    fakes.result = {"status": "ok", "full_description": "Join LinkedIn", "final_url": "https://www.linkedin.com/authwall"}
    row, _ = intake.add_job(conn, uid, "https://www.linkedin.com/jobs/view/42/")
    _run(conn, fakes, row["url"])

    intake.save_pasted(conn, uid, row["url"], intake.ManualFields(title="ML Engineer", company="", description=LONG))
    pasted = dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (row["url"],)).fetchone())
    assert (pasted["intake_status"], pasted["intake_error"], pasted["title"]) == ("queued", None, "ML Engineer")

    done = _run(conn, fakes, row["url"])
    assert len(fakes.scrapes) == 1 and fakes.extracted == [LONG.strip()]
    assert (done["intake_status"], done["title"], done["fit_score"]) == ("ok", "ML Engineer", 80)


def test_budget_stop_keeps_the_description(conn, uid, fakes):
    fakes.score_error = LLMStopRun("budget")
    row, _ = intake.add_job(conn, uid, "https://boards.greenhouse.io/acme/jobs/1")
    with pytest.raises(LLMStopRun):
        _run(conn, fakes, row["url"])
    done = dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (row["url"],)).fetchone())
    assert done["full_description"] == LONG and done["intake_status"] == "error"
    assert done["intake_error"] == tasks.STOP_MESSAGES["budget"]


def test_other_errors_are_recorded_and_raised(conn, uid, fakes):
    fakes.score_error = tasks.GenerationFailed("The assessment stopped.")
    row, _ = intake.add_job(conn, uid, "https://boards.greenhouse.io/acme/jobs/1")
    with pytest.raises(tasks.GenerationFailed):
        _run(conn, fakes, row["url"])
    done = dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (row["url"],)).fetchone())
    assert (done["intake_status"], done["intake_error"]) == ("error", "The assessment stopped.")


def test_intake_is_a_task_kind(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(intake, "run_intake", lambda user_id, url: calls.append(url))
    task = tasks.enqueue(conn, users.owner(conn).id, "https://example.com/1", "intake")
    assert task["kind"] == "intake" and task["state"] == "queued"
    tasks.generate_for_job("u1", "https://example.com/1", "intake")
    assert calls == ["https://example.com/1"]


def test_two_users_add_the_same_link_as_separate_rows(conn, uid, fakes):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    a, created_a = intake.add_job(conn, uid, url, intake.ManualFields(title="A's title"))
    other = users.invite(conn, "b@example.com").id
    assert intake.find_existing(conn, other, url + "?utm_source=x") is None
    b, created_b = intake.add_job(conn, other, url + "?utm_source=x", intake.ManualFields(company="B's company"))
    assert created_a and created_b and a["url"] == b["url"] == url and a["job_key"] == b["job_key"]
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE url = ?", (url,)).fetchone()[0] == 2

    fakes.conn = conn
    intake.run_intake(other, url, conn)
    intake.save_pasted(conn, other, url, intake.ManualFields(title="B pasted", description=LONG))
    after_a = dict(conn.execute("SELECT * FROM jobs WHERE user_id = ? AND url = ?", (uid, url)).fetchone())
    assert after_a == a
    after_b = dict(conn.execute("SELECT * FROM jobs WHERE user_id = ? AND url = ?", (other, url)).fetchone())
    assert after_b["details_json"] and after_b["fit_score"] == 80 and after_b["title"] == "B pasted"
    with pytest.raises(LookupError):
        intake.run_intake(other, "https://boards.greenhouse.io/acme/jobs/2", conn)
