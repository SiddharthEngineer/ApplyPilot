"""The Message Batches runner (claude-llm-provider Task 3), against a fake SDK batches API."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from strelitzia import llm, llm_batch
from strelitzia.database import init_db
from strelitzia.llm_batch import open_batches, resume_open_batches, run_batch

USAGE = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _ok(cid, text, stop_reason="end_turn"):
    message = SimpleNamespace(model="claude-haiku-5-5", usage=USAGE, stop_reason=stop_reason,
                              content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=message))


def _failed(cid, kind):
    if kind == "errored":
        error = SimpleNamespace(error=SimpleNamespace(type="overloaded_error", message="busy"))
        return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="errored", error=error))
    return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type=kind))


class FakeBatches:
    """Creates batches, reports them in_progress for `polls` retrieves, then returns `results[batch_id]`."""

    def __init__(self, results_for=None, polls=1):
        self.created: list[list[dict]] = []
        self.results_for = results_for or (lambda requests: [_ok(r["custom_id"], "ok") for r in reversed(requests)])
        self.polls = polls
        self.retrieves: dict[str, int] = {}
        self.results_by_id: dict[str, list] = {}

    def create(self, requests):
        batch_id = f"msgbatch_{len(self.created)}"
        self.created.append(requests)
        self.results_by_id[batch_id] = self.results_for(requests)
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id):
        if batch_id not in self.results_by_id:
            request = httpx2.Request("GET", f"https://api.anthropic.com/v1/messages/batches/{batch_id}")
            raise anthropic.NotFoundError("gone", response=httpx2.Response(404, request=request), body=None)
        n = self.retrieves[batch_id] = self.retrieves.get(batch_id, 0) + 1
        counts = SimpleNamespace(processing=1, succeeded=0, errored=0, canceled=0, expired=0)
        return SimpleNamespace(processing_status="ended" if n > self.polls else "in_progress", request_counts=counts)

    def results(self, batch_id):
        return iter(self.results_by_id[batch_id])


@pytest.fixture
def conn(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_EXTRACT_MODEL", raising=False)
    c = init_db(tmp_path / "t.db")
    monkeypatch.setattr(llm, "_ledger_conn", lambda: c)
    monkeypatch.setattr(llm_batch, "_sleep", lambda s: None)
    llm.reset_clients()
    yield c
    llm.reset_clients()


def _install(fake):
    client = llm.get_client("extract")
    client._sdk = SimpleNamespace(messages=SimpleNamespace(batches=fake))
    return client


def _items(n):
    client = llm.get_client("extract")
    return [(f"job{i}", client.request_params([{"role": "user", "content": f"posting {i}"}])) for i in range(n)]


def test_out_of_order_results_are_matched_by_custom_id(conn):
    fake = FakeBatches(results_for=lambda reqs: [_ok(r["custom_id"], r["params"]["messages"][0]["content"])
                                                 for r in reversed(reqs)])
    _install(fake)
    got = {}
    stats = run_batch("extract", _items(3), lambda cid, text, err: got.__setitem__(cid, (text, err)), poll_seconds=0)
    assert got == {f"job{i}": (f"posting {i}", None) for i in range(3)}
    assert stats["batches"] == 1 and stats["succeeded"] == 3 and stats["errored"] == 0
    assert fake.retrieves == {"msgbatch_0": 2}  # polled until ended
    assert fake.created[0][0]["params"]["model"] == "claude-haiku-5-5"
    rows = conn.execute("SELECT batch, cost_usd FROM llm_usage").fetchall()
    assert len(rows) == 3 and all(r["batch"] == 1 for r in rows)
    assert stats["cost_usd"] == pytest.approx(3 * (1000 * 0.10 + 100 * 0.50) / 1e6 / 2)
    assert conn.execute("SELECT status, request_count FROM llm_batches").fetchall()[0][:] == ("collected", 3)


def test_errored_expired_and_cut_off_results_are_errors(conn):
    def results(reqs):
        return [_ok("job0", "fine"), _failed("job1", "errored"), _failed("job2", "expired"),
                _failed("job3", "canceled"), _ok("job4", '{"a":', stop_reason="max_tokens")]
    _install(FakeBatches(results_for=results))
    got = {}
    stats = run_batch("extract", _items(5), lambda cid, text, err: got.__setitem__(cid, (text, err)), poll_seconds=0)
    assert got["job0"] == ("fine", None)
    assert got["job1"] == (None, "errored: overloaded_error: busy")
    assert got["job2"] == (None, "expired") and got["job3"] == (None, "canceled")
    assert got["job4"][0] is None and "max_tokens" in got["job4"][1]
    assert stats["succeeded"] == 1 and stats["errored"] == 4
    # The cut-off answer was still billed.
    assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2


def test_a_failing_callback_does_not_drop_the_rest(conn):
    _install(FakeBatches())
    seen = []

    def on_result(cid, text, err):
        seen.append(cid)
        if cid == "job1":
            raise RuntimeError("bad row")

    stats = run_batch("extract", _items(3), on_result, poll_seconds=0)
    assert sorted(seen) == ["job0", "job1", "job2"] and stats["callback_errors"] == 1


def test_split_at_the_request_limit(conn, monkeypatch):
    monkeypatch.setattr(llm_batch, "_MAX_REQUESTS", 2)
    fake = _install(FakeBatches())._sdk.messages.batches
    got = []
    stats = run_batch("extract", _items(5), lambda cid, text, err: got.append(cid), poll_seconds=0)
    assert [len(b) for b in fake.created] == [2, 2, 1]
    assert stats["batches"] == 3 and sorted(got) == [f"job{i}" for i in range(5)]


def test_split_at_the_size_limit(conn, monkeypatch):
    items = _items(4)
    import json
    monkeypatch.setattr(llm_batch, "_MAX_BYTES", len(json.dumps(items[0][1])) * 2)
    assert [len(c) for c in llm_batch._chunks(items)] == [2, 2]


@pytest.mark.parametrize("cid", ["https://x/job/1", "a" * 65, ""])
def test_custom_ids_are_validated(conn, cid):
    with pytest.raises(ValueError):
        run_batch("extract", [(cid, {})], lambda *a: None)


def test_duplicate_custom_ids_rejected(conn):
    with pytest.raises(ValueError):
        run_batch("extract", [("a", {}), ("a", {})], lambda *a: None)


def test_resume_picks_up_an_open_batch(conn):
    fake = FakeBatches(polls=10**9)  # never ends during the first run
    _install(fake)

    class Killed(Exception):
        pass

    def killed(seconds):
        raise Killed

    llm_batch._sleep, real_sleep = killed, llm_batch._sleep
    try:
        with pytest.raises(Killed):
            run_batch("extract", _items(2), lambda *a: None, poll_seconds=0)
    finally:
        llm_batch._sleep = real_sleep
    assert [b["id"] for b in open_batches("extract")] == ["msgbatch_0"]
    assert open_batches("scoring") == []

    fake.polls = 0
    got = {}
    stats = resume_open_batches("extract", lambda cid, text, err: got.__setitem__(cid, text), poll_seconds=0)
    assert got == {"job0": "ok", "job1": "ok"} and stats["batches"] == 1
    assert open_batches() == []
    assert conn.execute("SELECT COUNT(*) FROM llm_usage WHERE batch = 1").fetchone()[0] == 2


def test_resume_drops_a_batch_the_api_no_longer_has(conn):
    _install(FakeBatches())
    conn.execute("INSERT INTO llm_batches (id, purpose, submitted_at, status, request_count) "
                 "VALUES ('msgbatch_gone', 'extract', '2026-01-01', 'submitted', 3)")
    conn.commit()
    assert resume_open_batches("extract", lambda *a: None)["batches"] == 0
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "lost"


def test_nothing_to_do(conn):
    assert run_batch("extract", [], lambda *a: None)["batches"] == 0
    assert resume_open_batches("extract", lambda *a: None)["batches"] == 0


def test_collecting_twice_does_not_double_count(conn):
    fake = FakeBatches()
    client = _install(fake)
    run_batch("extract", _items(3), lambda *a: None, poll_seconds=0)
    # A run killed mid-collection leaves the batch 'submitted'; collecting it again must not add usage rows.
    conn.execute("UPDATE llm_batches SET status = 'submitted'")
    conn.commit()
    llm_batch._collect(client, "extract", "msgbatch_0", lambda *a: None, llm_batch._new_stats(), conn)
    assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 3


def test_open_batch_estimate_counts_against_the_budget(conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "1")
    fake = FakeBatches(polls=10**9)
    _install(fake)

    def killed(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm_batch, "_sleep", killed)
    with pytest.raises(KeyboardInterrupt):
        run_batch("extract", _items(4), lambda *a: None, poll_seconds=0, estimate_usd=0.8)
    assert llm.reserved_spend(conn) == pytest.approx(0.8)
    with pytest.raises(llm.LLMStopRun):
        llm.check_budget(0.3, conn)  # $0.80 reserved + $0.30 > $1
    llm.check_budget(0.1, conn)


def test_resume_without_waiting_leaves_running_batches(conn):
    fake = FakeBatches(polls=10**9)
    _install(fake)
    conn.execute("INSERT INTO llm_batches (id, purpose, submitted_at, status, request_count) "
                 "VALUES ('msgbatch_x', 'extract', '2026-01-01', 'submitted', 2)")
    conn.commit()
    fake.results_by_id["msgbatch_x"] = []
    stats = resume_open_batches("extract", lambda *a: None, poll_seconds=0, wait=False)
    assert stats["still_open"] == 1 and stats["batches"] == 0
    assert fake.retrieves == {"msgbatch_x": 1}
    assert [b["id"] for b in open_batches("extract")] == ["msgbatch_x"]


def test_a_batch_and_its_usage_belong_to_its_user(conn, monkeypatch):
    from strelitzia import users

    alice = users.invite(conn, "alice@example.com").id
    fake = FakeBatches(polls=10**9)  # never ends during the first run
    _install(fake)

    def killed(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm_batch, "_sleep", killed)
    with pytest.raises(KeyboardInterrupt):
        run_batch("extract", _items(2), lambda *a: None, poll_seconds=0, estimate_usd=0.5, user_id=alice)
    assert [b["user_id"] for b in open_batches("extract", conn)] == [alice]
    assert open_batches("extract", conn, users.owner(conn).id) == []
    assert llm.user_month_spend(conn, alice) == pytest.approx(0.5)  # reserved until collected

    fake.polls = 0
    owner = users.owner(conn).id
    assert resume_open_batches("extract", lambda *a: None, poll_seconds=0, user_id=owner)["batches"] == 0
    assert resume_open_batches("extract", lambda *a: None, poll_seconds=0, user_id=alice)["batches"] == 1
    assert {r[0] for r in conn.execute("SELECT user_id FROM llm_usage").fetchall()} == {alice}
    assert llm.user_month_spend(conn, alice) == pytest.approx(2 * (1000 * 0.10 + 100 * 0.50) / 1e6 / 2)
    assert llm.user_month_spend(conn, owner) == 0


def test_a_batch_defaults_to_the_acting_user(conn):
    _install(FakeBatches())
    with llm.acting_for("u1"):
        run_batch("extract", _items(1), lambda *a: None, poll_seconds=0)
    assert conn.execute("SELECT user_id FROM llm_batches").fetchone()[0] == "u1"
    assert conn.execute("SELECT user_id FROM llm_usage").fetchone()[0] == "u1"
