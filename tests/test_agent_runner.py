"""Tests for the unattended runner's queue (scripts/agent/buildqueue.py), risk gate (scripts/agent/gate.py),
session-usage gate (scripts/agent/usage.py) and preprod health check (scripts/validate.sh --preprod)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "scripts" / "agent"


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_AGENT_STATE", str(tmp_path / "state"))

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(AGENT / "buildqueue.py"), *args], capture_output=True, text=True)

    def result(data: dict) -> str:
        path = tmp_path / "r.json"
        path.write_text(json.dumps(data))
        return str(path)

    run.result = result
    return run


def test_empty_queue_has_nothing_next(q):
    assert q("next").returncode == 1


def test_future_item_is_not_due(q):
    q("add", "later", "--in", "2h")
    assert q("next").returncode == 1


def test_failed_attempts_end_in_blocked(q):
    q("add", "plan-a")
    for expected in ("queued", "blocked"):
        assert q("next").stdout.strip() == "plan-a"
        q("start", "plan-a")
        assert q("finish", "plan-a", "/nonexistent").stdout.strip() == expected
    assert q("next").returncode == 1


def test_question_then_answer_requeues_with_fresh_attempts(q):
    q("add", "plan-a")
    q("start", "plan-a")
    assert q("finish", "plan-a", q.result({"status": "question"})).stdout.strip() == "waiting-answer"
    q("answer", "plan-a", "use option B")
    assert q("get", "plan-a", "attempts").stdout.strip() == "0"
    assert "use option B" in q("get", "plan-a", "notes").stdout
    assert q("next").stdout.strip() == "plan-a"


def test_answer_sets_resume_and_failed_attempt_clears_it(q):
    q("add", "plan-a")
    q("start", "plan-a")
    q("set", "plan-a", "session_id=abc-123")
    q("finish", "plan-a", q.result({"status": "question"}))
    q("answer", "plan-a", "use option B")
    assert q("get", "plan-a", "resume").stdout.strip() == "1"
    assert q("get", "plan-a", "session_id").stdout.strip() == "abc-123"
    q("start", "plan-a")
    assert q("finish", "plan-a", "/nonexistent").stdout.strip() == "queued"
    assert q("get", "plan-a", "resume").returncode == 1
    assert q("get", "plan-a", "session_id").returncode == 1


def test_add_starts_without_resume(q):
    q("add", "plan-a")
    q("start", "plan-a")
    q("set", "plan-a", "session_id=abc-123", "resume=1")
    q("finish", "plan-a", q.result({"status": "blocked"}))
    q("add", "plan-a")
    assert q("get", "plan-a", "resume").returncode == 1
    assert q("get", "plan-a", "session_id").returncode == 1


def test_set_with_empty_value_removes_the_key(q):
    q("add", "plan-a")
    q("set", "plan-a", "resume=1")
    q("set", "plan-a", "resume=")
    assert q("get", "plan-a", "resume").returncode == 1


def test_no_daily_cap_by_default(q, monkeypatch):
    monkeypatch.delenv("AGENT_MAX_RUNS_PER_DAY", raising=False)
    for slug in ("plan-a", "plan-b", "plan-c", "plan-d", "plan-e", "plan-f"):
        q("add", slug)
    for slug in ("plan-a", "plan-b", "plan-c", "plan-d", "plan-e"):
        q("start", slug)
    assert q("next").stdout.strip() == "plan-f"
    assert "no daily cap" in q("list").stdout


def test_daily_cap_and_pause_stop_runs(q, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_RUNS_PER_DAY", "1")
    q("add", "plan-a")
    q("add", "plan-b")
    q("start", "plan-a")
    assert q("next").returncode == 1  # cap reached
    monkeypatch.setenv("AGENT_MAX_RUNS_PER_DAY", "5")
    q("pause", "1")
    assert q("next").returncode == 1


def test_review_result_waits_for_preprod(q):
    q("add", "plan-a")
    q("start", "plan-a")
    assert q("finish", "plan-a", q.result({"status": "review", "pr": 12})).stdout.strip() == "awaiting-preprod"
    assert q("get", "plan-a", "pr").stdout.strip() == "12"


@pytest.fixture
def repo(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "trunk")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    assert 1\n")
    (tmp_path / "plan.md").write_text("# Plan\n**Risk:** low (docs)\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "base")

    def commit(path: str, content: str) -> None:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
        git("add", "-A")
        git("commit", "-qm", f"change {path}")

    commit.path = tmp_path
    return commit


def gate(repo) -> dict:
    out = subprocess.run(
        [sys.executable, str(AGENT / "gate.py"), "plan.md", "--base", "base"], cwd=repo.path, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def test_gate_low_for_docs(repo):
    repo("docs/notes.md", "hello\n")
    assert gate(repo)["tier"] == "low"


def test_gate_high_risk_path_needs_review(repo):
    repo("Dockerfile", "FROM python\n")
    assert gate(repo)["tier"] == "review"


def test_gate_weakened_test_needs_review(repo):
    repo("tests/test_x.py", "def test_a():\n    pass\n")
    assert any("assert" in r for r in gate(repo)["reasons"])


def test_gate_over_budget_needs_review(repo):
    repo("src/pkg/big.py", "x = 1\n" * 300)
    assert gate(repo)["tier"] == "review"


def test_gate_respects_declared_review(repo):
    (repo.path / "plan.md").write_text("# Plan\n**Risk:** review (schema)\n")
    repo("docs/notes.md", "hello\n")
    assert gate(repo)["tier"] == "review"


def _usage():
    spec = importlib.util.spec_from_file_location("agent_usage", AGENT / "usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rate_event(utilization, resets_at=2_000_000_000):
    info = {"status": "allowed", "rateLimitType": "five_hour", "resetsAt": resets_at,
            "unifiedWindows": {"five_hour": {"utilization": utilization, "resetsAt": resets_at},
                               "seven_day": {"utilization": 0.9, "resetsAt": resets_at}}}
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info})


def test_usage_reads_five_hour_window_not_seven_day():
    lines = ['{"type": "system", "subtype": "init"}', "not json", _rate_event(0.11), '{"type": "result"}']
    assert _usage().five_hour_window(lines) == (0.11, 2_000_000_000)


def test_usage_missing_event_is_unknown():
    assert _usage().five_hour_window(['{"type": "result"}']) is None


@pytest.mark.parametrize(("utilization", "code"), [(0.29, 0), (0.30, 10), (0.95, 10)])
def test_usage_check_against_limit(monkeypatch, capsys, utilization, code):
    usage = _usage()
    monkeypatch.setenv("AGENT_MAX_SESSION_USAGE", "30")
    monkeypatch.setattr(usage, "probe", lambda: [_rate_event(utilization)])
    assert usage.cmd_check() == code
    if code == 10:
        assert float(capsys.readouterr().out.splitlines()[-1]) > 0  # hours until reset, for buildqueue.py pause


@pytest.mark.parametrize(("utilization", "code"), [(0.69, 0), (0.70, 10)])
def test_usage_check_default_limit_is_70(monkeypatch, utilization, code):
    usage = _usage()
    monkeypatch.delenv("AGENT_MAX_SESSION_USAGE", raising=False)
    monkeypatch.setattr(usage, "probe", lambda: [_rate_event(utilization)])
    assert usage.cmd_check() == code


def test_usage_check_unknown_and_disabled(monkeypatch):
    usage = _usage()
    monkeypatch.setattr(usage, "probe", lambda: [])
    monkeypatch.setenv("AGENT_MAX_SESSION_USAGE", "30")
    assert usage.cmd_check() == 11
    monkeypatch.setenv("AGENT_MAX_SESSION_USAGE", "0")
    monkeypatch.setattr(usage, "probe", lambda: pytest.fail("probe must not run when the check is disabled"))
    assert usage.cmd_check() == 0


def test_validate_preprod_runs_outside_a_checkout(tmp_path):
    # preprod.sh calls it from cron, whose cwd ($HOME) is not a git repo. Unset URL → its own error, not git's.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PREPROD_")}
    env["HOME"] = str(tmp_path)
    out = subprocess.run(["bash", str(AGENT.parent / "validate.sh"), "--preprod"], cwd=tmp_path, env=env,
                         capture_output=True, text=True)
    assert "not a git repository" not in out.stderr
    assert "PREPROD_HEALTH_URL is not set" in out.stderr


@pytest.fixture
def preprod(tmp_path):
    """Source preprod.sh with a temp origin + app checkout and stub deploy/health commands; returns run(seed_cmd, with_script)."""
    origin, app = tmp_path / "origin", tmp_path / "app"

    def git(*args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("init", "-q", "-b", "trunk", str(origin))
    git("-C", str(origin), "config", "user.email", "t@example.com")
    git("-C", str(origin), "config", "user.name", "t")
    (origin / "README").write_text("x\n")
    git("-C", str(origin), "add", "-A")
    git("-C", str(origin), "commit", "-qm", "base")
    git("clone", "-q", str(origin), str(app))
    marker = tmp_path / "seeded"

    def run(seed_cmd: str | None, with_script: bool) -> subprocess.CompletedProcess:
        if with_script and not (origin / "scripts" / "seed_preprod.py").exists():
            (origin / "scripts").mkdir()
            (origin / "scripts" / "seed_preprod.py").write_text("")
            git("-C", str(origin), "add", "-A")
            git("-C", str(origin), "commit", "-qm", "add seed")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PREPROD_")}
        env.update(HOME=str(tmp_path), APPLYPILOT_AGENT_STATE=str(tmp_path / "state"), PREPROD_APP_DIR=str(app),
                   PREPROD_DEPLOY_CMD="true", PREPROD_HEALTH_URL="http://127.0.0.1:1/health")
        if seed_cmd is not None:
            env["PREPROD_SEED_CMD"] = seed_cmd.replace("MARKER", str(marker))
        script = f'. "{AGENT / "preprod.sh"}"; sleep() {{ :; }}; health_check() {{ echo healthy; }}; deploy_ref trunk'
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)

    run.marker = marker
    return run


def test_preprod_deploy_runs_seed_when_script_exists(preprod):
    out = preprod("echo seeded > MARKER", with_script=True)
    assert out.returncode == 0, out.stderr
    assert preprod.marker.read_text() == "seeded\n"
    assert "healthy" in out.stdout


def test_preprod_deploy_skips_seed_without_script(preprod):
    out = preprod("echo seeded > MARKER", with_script=False)
    assert out.returncode == 0, out.stderr
    assert not preprod.marker.exists()
    assert "seed skipped: no scripts/seed_preprod.py at" in out.stderr
    assert "healthy" in out.stdout


def test_preprod_deploy_fails_when_seed_fails(preprod):
    out = preprod("exit 1", with_script=True)
    assert out.returncode == 1
    assert "healthy" not in out.stdout


def test_preprod_deploy_without_seed_cmd(preprod):
    out = preprod(None, with_script=True)
    assert out.returncode == 0, out.stderr
    assert "seed" not in out.stderr
