"""Background generation tasks (dashboard-api Task 6)."""

import threading
import time

import pytest
from fake_drive import FakeDrive, fake_media
from fastapi.testclient import TestClient
from web_helpers import add_job

from applypilot import database
from applypilot.database import get_connection, init_db, job_key
from applypilot.llm import LLMQuotaExhausted
from applypilot.web import tasks
from applypilot.web.app import create_app
from applypilot.web.tasks import QUOTA_MESSAGE, TaskRunner

HEADERS = {"X-ApplyPilot": "1"}
URLS = [f"https://example.com/jobs/{i}" for i in range(3)]


def wait_for(client, task_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = client.get(f"/app/api/tasks/{task_id}").json()
        if task["state"] in ("done", "error"):
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish: {task}")


class FakeGenerate:
    """Records calls; checks that only one task runs at a time; optional per-call failure."""

    def __init__(self, fail=None, delay=0.05):
        self.calls = []
        self.fail = fail or {}
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, url, kind):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.calls.append((url, kind))
            if url in self.fail:
                raise self.fail[url]
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def gen():
    return FakeGenerate()


@pytest.fixture
def tclient(db_path, gen):
    init_db(db_path)
    conn = get_connection(db_path)
    for url in URLS:
        add_job(conn, url, job_key=job_key(url))
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        yield c


def _generate(client, url, **body):
    return client.post(f"/app/api/jobs/{job_key(url)}/generate", json=body)


def test_generate_returns_a_task_that_finishes(tclient, gen):
    r = _generate(tclient, URLS[0], resume=True, cover=True)
    assert r.status_code == 200
    task = r.json()
    assert task["state"] == "queued" and task["kind"] == "both" and task["key"] == job_key(URLS[0])
    done = wait_for(tclient, task["id"])
    assert done["state"] == "done" and done["error"] is None
    assert done["started_at"] and done["finished_at"]
    assert gen.calls == [(URLS[0], "both")]


def test_kinds(tclient, gen):
    assert _generate(tclient, URLS[0]).json()["kind"] == "resume"  # default: resume only
    assert _generate(tclient, URLS[1], resume=False, cover=True).json()["kind"] == "cover"
    assert _generate(tclient, URLS[2], resume=False, cover=False).status_code == 422


def test_tasks_run_one_at_a_time_in_order(tclient, gen):
    gen.delay = 0.1
    ids = [_generate(tclient, url).json()["id"] for url in URLS]
    for task_id in ids:
        assert wait_for(tclient, task_id)["state"] == "done"
    assert [c[0] for c in gen.calls] == URLS
    assert gen.max_active == 1


def test_status_becomes_in_progress(tclient):
    key = job_key(URLS[0])
    wait_for(tclient, _generate(tclient, URLS[0]).json()["id"])
    detail = tclient.get(f"/app/api/jobs/{key}").json()
    assert detail["status"] == "in_progress" and detail["status_color"] == "yellow"
    assert detail["events"][0]["source"] == "generate"


def test_user_status_is_kept(tclient):
    key = job_key(URLS[1])
    tclient.post(f"/app/api/jobs/{key}/status", json={"status": "submitted"})
    wait_for(tclient, _generate(tclient, URLS[1]).json()["id"])
    detail = tclient.get(f"/app/api/jobs/{key}").json()
    assert detail["status"] == "submitted" and len(detail["events"]) == 1


def test_quota_error_message(db_path):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    gen = FakeGenerate(fail={URLS[0]: LLMQuotaExhausted("gemini-3.1-flash-lite", "PerDay")})
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        task = wait_for(c, _generate(c, URLS[0]).json()["id"])
    assert task["state"] == "error" and task["error"] == QUOTA_MESSAGE == "Daily Gemini quota reached; try again tomorrow."


def test_other_errors_are_reported_and_the_queue_continues(db_path):
    init_db(db_path)
    conn = get_connection(db_path)
    for url in URLS[:2]:
        add_job(conn, url, job_key=job_key(url))
    gen = FakeGenerate(fail={URLS[0]: ValueError("boom")})
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        first, second = (_generate(c, u).json()["id"] for u in URLS[:2])
        assert wait_for(c, first)["error"] == "ValueError: boom"
        assert wait_for(c, second)["state"] == "done"


def test_interrupted_tasks_are_recovered_on_start(db_path, gen):
    init_db(db_path)
    conn = get_connection(db_path)
    add_job(conn, URLS[0], job_key=job_key(URLS[0]))
    conn.execute("INSERT INTO dashboard_tasks (id, url, kind, state, created_at, started_at) "
                 "VALUES ('t1', ?, 'resume', 'running', '2026-10-05T00:00:00', '2026-10-05T00:00:01')", (URLS[0],))
    conn.commit()
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        task = c.get("/app/api/tasks/t1").json()
    assert task["state"] == "error" and task["error"] == "interrupted" and task["finished_at"]
    assert gen.calls == []  # not re-run


def test_queued_tasks_survive_a_restart(db_path, gen):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        task_id = _generate(c, URLS[0]).json()["id"]
        assert c.get(f"/app/api/tasks/{task_id}").json()["state"] == "queued"
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        assert wait_for(c, task_id)["state"] == "done"


def test_a_waiting_duplicate_is_not_queued_twice(db_path):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        a = _generate(c, URLS[0]).json()["id"]
        b = _generate(c, URLS[0]).json()["id"]
        cover = _generate(c, URLS[0], resume=False, cover=True).json()["id"]
        listed = c.get(f"/app/api/jobs/{job_key(URLS[0])}/tasks").json()
    assert a == b != cover
    assert [t["id"] for t in listed] == [cover, a]


def test_errors_and_csrf(tclient, app):
    assert tclient.get("/app/api/tasks/nope").status_code == 404
    assert tclient.post("/app/api/jobs/nope/generate", json={}).status_code == 404
    assert tclient.get("/app/api/jobs/nope/tasks").status_code == 404
    with TestClient(app) as bare:
        assert bare.post(f"/app/api/jobs/{job_key(URLS[0])}/generate", json={}).status_code == 403


# --- generate_for_job: the real task body, with fake stages and a fake Drive ---------------------------


@pytest.fixture
def default_db(tmp_path, monkeypatch):
    """generate_for_job uses the configured DB; point the default at a temp SQLite file."""
    path = tmp_path / "default.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    init_db()
    yield path
    database.close_connection()


@pytest.fixture
def fake_stages(tmp_path, monkeypatch):
    """run_tailoring / run_cover_letters stand-ins that write PDFs and record the paths, like the real ones."""
    from applypilot.scoring import cover_letter, tailor

    calls = []

    def fake_tailor(urls, **kw):
        calls.append(("tailor", urls, kw))
        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF resume")
        conn = get_connection()
        conn.execute("UPDATE jobs SET tailored_resume_path = ?, tailored_at = '2026-10-05T12:00:00' WHERE url = ?",
                     (str(pdf), urls[0]))
        conn.commit()
        return {"approved": 1, "failed": 0, "errors": 0}

    def fake_cover(urls, **kw):
        calls.append(("cover", urls, kw))
        txt = tmp_path / "resume_CL.txt"
        txt.write_text("Dear team")
        txt.with_suffix(".pdf").write_bytes(b"%PDF letter")
        conn = get_connection()
        conn.execute("UPDATE jobs SET cover_letter_path = ?, cover_letter_at = '2026-10-05T12:00:00' WHERE url = ?",
                     (str(txt), urls[0]))
        conn.commit()
        return {"generated": 1, "errors": 0}

    monkeypatch.setattr(tailor, "run_tailoring", fake_tailor)
    monkeypatch.setattr(cover_letter, "run_cover_letters", fake_cover)
    return calls


@pytest.fixture
def drive(monkeypatch):
    from applypilot.storage.drive import DriveClient

    fake = FakeDrive()
    monkeypatch.setattr(tasks, "_drive_configured", lambda: True)
    monkeypatch.setattr(DriveClient, "from_credentials", classmethod(lambda cls: DriveClient(fake, fake_media)))
    return fake


def test_generate_end_to_end_sets_drive_links(default_db, fake_stages, drive):
    url = add_job(get_connection(), URLS[0], job_key=job_key(URLS[0]))
    with TestClient(create_app(), headers=HEADERS) as c:  # db=None: the same configured DB as the stages
        task = wait_for(c, _generate(c, url, resume=True, cover=True).json()["id"])
        assert task["state"] == "done", task
        detail = c.get(f"/app/api/jobs/{job_key(url)}").json()
    assert [call[0] for call in fake_stages] == ["tailor", "cover"]
    assert fake_stages[0][1] == fake_stages[1][1] == [url]
    assert detail["status"] == "in_progress"
    assert detail["links"]["resume"].startswith("https://")
    assert detail["links"]["cover_letter"].startswith("https://")
    assert detail["links"]["drive_folder"].startswith("https://drive.google.com/drive/folders/")


def test_generate_without_drive_keeps_local_pdfs(default_db, fake_stages, monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, "_drive_configured", lambda: False)
    url = add_job(get_connection(), URLS[0], job_key=job_key(URLS[0]))
    tasks.generate_for_job(url, "resume")
    assert (tmp_path / "resume.pdf").exists()
    assert [c[0] for c in fake_stages] == ["tailor"]


@pytest.mark.parametrize("stats,message", [
    ({"approved": 0, "errors": 1}, "The resume failed with an error (see the server log)."),
    ({"approved": 0, "failed": 1}, "The resume didn't pass validation; try again."),
    ({"approved": 0}, "No resume was generated (the job has no description)."),
])
def test_generate_failure_messages(default_db, monkeypatch, stats, message):
    from applypilot.scoring import tailor

    monkeypatch.setattr(tailor, "run_tailoring", lambda **kw: stats)
    with pytest.raises(tasks.GenerationFailed, match=message.replace("(", r"\(").replace(")", r"\)")):
        tasks.generate_for_job(URLS[0], "resume")


def test_generate_quota_stop_raises(default_db, monkeypatch):
    from applypilot.scoring import cover_letter

    monkeypatch.setattr(cover_letter, "run_cover_letters", lambda **kw: {"generated": 0, "stopped": "daily_quota"})
    with pytest.raises(LLMQuotaExhausted):
        tasks.generate_for_job(URLS[0], "cover")


def test_runner_stops_cleanly(db_path):
    init_db(db_path)
    runner = TaskRunner(db_path, generate=FakeGenerate(), poll_seconds=0.01)
    runner.start()
    runner.stop()
    assert not runner._thread.is_alive()
