"""Background tasks (dashboard-api Task 6): the queue, run one at a time, through POST /jobs/{key}/rescore."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from web_helpers import add_job

from strelitzia import users
from strelitzia.database import get_connection, init_db, job_key
from strelitzia.llm import LLMStopRun
from strelitzia.web import tasks
from strelitzia.web.app import create_app
from strelitzia.web.tasks import STOP_MESSAGES, TaskRunner

HEADERS = {"X-Strelitzia": "1"}
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
        self.users = []
        self.fail = fail or {}
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, user_id, url, kind):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.calls.append((url, kind))
            self.users.append(user_id)
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


def _queue(client, url):
    return client.post(f"/app/api/jobs/{job_key(url)}/rescore")


def test_rescore_returns_a_task_that_finishes(tclient, gen):
    r = _queue(tclient, URLS[0])
    assert r.status_code == 200
    task = r.json()
    assert task["state"] == "queued" and task["kind"] == "rescore" and task["key"] == job_key(URLS[0])
    done = wait_for(tclient, task["id"])
    assert done["state"] == "done" and done["error"] is None
    assert done["started_at"] and done["finished_at"]
    assert gen.calls == [(URLS[0], "rescore")]


def test_generation_kinds_are_gone(tclient, conn):
    """Resumes and cover letters are written in Claude and pasted back; there is no generate task or route."""
    assert tclient.post(f"/app/api/jobs/{job_key(URLS[0])}/generate", json={}).status_code in (404, 405)
    for kind in ("resume", "cover", "both"):
        with pytest.raises(ValueError):
            tasks.enqueue(conn, "u1", URLS[0], kind)


def test_tasks_run_one_at_a_time_in_order(tclient, gen):
    gen.delay = 0.1
    ids = [_queue(tclient, url).json()["id"] for url in URLS]
    for task_id in ids:
        assert wait_for(tclient, task_id)["state"] == "done"
    assert [c[0] for c in gen.calls] == URLS
    assert gen.max_active == 1


def test_queueing_leaves_the_status_alone(tclient):
    key = job_key(URLS[0])
    wait_for(tclient, _queue(tclient, URLS[0]).json()["id"])
    detail = tclient.get(f"/app/api/jobs/{key}").json()
    assert detail["status"] == "active" and detail["events"] == []


def test_budget_error_message(db_path):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    gen = FakeGenerate(fail={URLS[0]: LLMStopRun("budget", "over budget")})
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        task = wait_for(c, _queue(c, URLS[0]).json()["id"])
    assert task["state"] == "error" and task["error"] == STOP_MESSAGES["budget"]
    assert "LLM_MONTHLY_BUDGET_USD" in task["error"]


def test_other_errors_are_reported_and_the_queue_continues(db_path):
    init_db(db_path)
    conn = get_connection(db_path)
    for url in URLS[:2]:
        add_job(conn, url, job_key=job_key(url))
    gen = FakeGenerate(fail={URLS[0]: ValueError("boom")})
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        first, second = (_queue(c, u).json()["id"] for u in URLS[:2])
        assert wait_for(c, first)["error"] == "ValueError: boom"
        assert wait_for(c, second)["state"] == "done"


def test_interrupted_tasks_are_recovered_on_start(db_path, gen):
    init_db(db_path)
    conn = get_connection(db_path)
    add_job(conn, URLS[0], job_key=job_key(URLS[0]))
    conn.execute("INSERT INTO dashboard_tasks (id, url, kind, state, created_at, started_at) "
                 "VALUES ('t1', ?, 'rescore', 'running', '2026-10-05T00:00:00', '2026-10-05T00:00:01')", (URLS[0],))
    conn.commit()
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        task = c.get("/app/api/tasks/t1").json()
    assert task["state"] == "error" and task["error"] == "interrupted" and task["finished_at"]
    assert gen.calls == []  # not re-run


def test_queued_tasks_survive_a_restart(db_path, gen):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        task_id = _queue(c, URLS[0]).json()["id"]
        assert c.get(f"/app/api/tasks/{task_id}").json()["state"] == "queued"
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        assert wait_for(c, task_id)["state"] == "done"


def test_a_waiting_duplicate_is_not_queued_twice(db_path):
    init_db(db_path)
    conn = get_connection(db_path)
    add_job(conn, URLS[0], job_key=job_key(URLS[0]))
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        a = _queue(c, URLS[0]).json()["id"]
        b = _queue(c, URLS[0]).json()["id"]
        intake = tasks.enqueue(conn, users.owner(conn).id, URLS[0], "intake")["id"]
        listed = c.get(f"/app/api/jobs/{job_key(URLS[0])}/tasks").json()
    assert a == b != intake
    assert {t["id"] for t in listed} == {intake, a}


def test_errors_and_csrf(tclient, app):
    assert tclient.get("/app/api/tasks/nope").status_code == 404
    assert tclient.post("/app/api/jobs/nope/rescore").status_code == 404
    assert tclient.get("/app/api/jobs/nope/tasks").status_code == 404
    with TestClient(app) as bare:
        assert bare.post(f"/app/api/jobs/{job_key(URLS[0])}/rescore").status_code == 403


@pytest.mark.parametrize("reason", ["overloaded", "auth"])
def test_stop_error_messages(db_path, reason):
    init_db(db_path)
    add_job(get_connection(db_path), URLS[0], job_key=job_key(URLS[0]))
    gen = FakeGenerate(fail={URLS[0]: LLMStopRun(reason)})
    with TestClient(create_app(db=db_path, generate=gen), headers=HEADERS) as c:
        task = wait_for(c, _queue(c, URLS[0]).json()["id"])
    assert task["error"] == STOP_MESSAGES[reason]


def test_generate_for_job_dispatches(monkeypatch):
    calls = []
    monkeypatch.setattr("strelitzia.intake.run_intake", lambda user_id, url: calls.append(("intake", url)))
    monkeypatch.setattr(tasks, "assess_task", lambda user_id, url, kind: calls.append((kind, url)))
    tasks.generate_for_job("u1", URLS[0], "intake")
    tasks.generate_for_job("u1", URLS[1], "rescore")
    tasks.generate_for_job("u1", None, "reassess_all")
    tasks.generate_for_job("u1", None, "rescore_all")
    assert calls == [("intake", URLS[0]), ("rescore", URLS[1]), ("reassess_all", None), ("rescore_all", None)]


def test_rescore_all_runs_every_active_job_in_a_batch(monkeypatch):
    calls = []
    monkeypatch.setattr("strelitzia.scoring.scorer.run_scoring", lambda **kw: calls.append(kw) or {"scored": 2})
    tasks.assess_task("u1", None, "rescore_all")
    assert calls == [{"mode": "batch", "scope": "active", "user_id": "u1"}]
    with pytest.raises(ValueError):
        tasks.enqueue(None, "u1", URLS[0], "rescore_all")  # takes no job


def test_runner_stops_cleanly(db_path):
    init_db(db_path)
    runner = TaskRunner(db_path, generate=FakeGenerate(), poll_seconds=0.01)
    runner.start()
    runner.stop()
    assert not runner._thread.is_alive()


@pytest.mark.parametrize("kind", ["resume", "cover", "both", "bogus"])
def test_leftover_generation_tasks_fail_without_running_anything(monkeypatch, kind):
    """Rows queued by an older version must not fall through to a (paid) re-assessment of every job."""
    monkeypatch.setattr(tasks, "assess_task", lambda user_id, url, kind: pytest.fail("assess_task ran"))
    with pytest.raises(tasks.GenerationFailed):
        tasks.generate_for_job("u1", URLS[0], kind)


def test_a_leftover_generation_task_errors_in_the_queue(db_path):
    init_db(db_path)
    conn = get_connection(db_path)
    add_job(conn, URLS[0], job_key=job_key(URLS[0]))
    conn.execute("INSERT INTO dashboard_tasks (id, url, kind, state, created_at) "
                 "VALUES ('old', ?, 'both', 'queued', '2026-10-05T00:00:00')", (URLS[0],))
    conn.commit()
    with TestClient(create_app(db=db_path, generate=tasks.generate_for_job), headers=HEADERS) as c:
        task = wait_for(c, "old")
    assert task["state"] == "error" and "Open in Claude" in task["error"]


def test_tasks_belong_to_a_user(db_path, gen):
    """Dedupe is per user, the runner passes the task's user, and another user's tasks and jobs are 404."""
    init_db(db_path)
    conn = get_connection(db_path)
    owner, other = users.owner(conn).id, users.invite(conn, "b@example.com").id
    add_job(conn, URLS[0], job_key=job_key(URLS[0]))
    add_job(conn, URLS[0], job_key=job_key(URLS[0]), user_id=other)
    add_job(conn, URLS[1], job_key=job_key(URLS[1]), user_id=other)
    mine = tasks.enqueue(conn, owner, URLS[0], "rescore")
    theirs = tasks.enqueue(conn, other, URLS[0], "rescore")
    assert mine["id"] != theirs["id"] and theirs["user_id"] == other
    assert tasks.enqueue(conn, other, URLS[0], "rescore")["id"] == theirs["id"]
    assert tasks.enqueue(conn, other, None, "reassess_all")["id"] != tasks.enqueue(conn, owner, None, "reassess_all")["id"]
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        assert c.get(f"/app/api/tasks/{theirs['id']}").status_code == 404
        assert c.get(f"/app/api/tasks/{mine['id']}").json()["key"] == job_key(URLS[0])
        assert [t["id"] for t in c.get(f"/app/api/jobs/{job_key(URLS[0])}/tasks").json()] == [mine["id"]]
        assert c.get(f"/app/api/jobs/{job_key(URLS[1])}").status_code == 404  # only B has it
        assert c.post(f"/app/api/jobs/{job_key(URLS[1])}/rescore").status_code == 404
    runner = TaskRunner(db_path, generate=gen)
    runner.run_one(conn, tasks.get_task(conn, theirs["id"]))
    assert gen.users == [other] and tasks.get_task(conn, theirs["id"])["state"] == "done"
