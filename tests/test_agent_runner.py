"""Tests for the unattended runner's queue (scripts/agent/buildqueue.py) and risk gate (scripts/agent/gate.py)."""

import json
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
