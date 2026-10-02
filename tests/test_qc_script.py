"""Tests for the plan parser in scripts/qc.py."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("qc", Path(__file__).resolve().parent.parent / "scripts" / "qc.py")
qc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qc)

PLAN = """# Plan: Sample

## Goal
Something.

## Success Criteria
1. First criterion (`cmd one`).
2. Second criterion.

## Task Chain

### Task 1: Cloud only
**Runs:** cloud
**Status:** ✅ Complete (2026-10-02)

### Task 2: Needs the user
**Runs:** local (needs network)
**Status:** ❌ Not started

### Task 3: Mixed, done
**Runs:** cloud; live check: local
**Status:** ✅ Complete (2026-10-02)

### Task 4: Mixed, pending
**Runs:** cloud; live check: local
**Status:** 🟡 Cloud part done (2026-10-02), local steps pending

## Implementation Order
1. Not a criterion.
"""


def test_parse_success_criteria():
    assert qc.parse_success_criteria(PLAN) == ["First criterion (`cmd one`).", "Second criterion."]


def test_parse_success_criteria_missing():
    assert qc.parse_success_criteria("# Plan\n\n## Goal\n1. nope\n") == []


def test_parse_tasks():
    tasks = qc.parse_tasks(PLAN)
    assert [t["title"] for t in tasks] == [
        "Task 1: Cloud only", "Task 2: Needs the user", "Task 3: Mixed, done", "Task 4: Mixed, pending",
    ]
    assert tasks[1]["runs"] == "local (needs network)"
    assert tasks[0]["status"].startswith("✅")


def test_local_tasks_pending():
    pending = qc.local_tasks_pending(qc.parse_tasks(PLAN))
    assert [t["title"] for t in pending] == ["Task 2: Needs the user", "Task 4: Mixed, pending"]


def test_real_plan_has_five_criteria():
    plan = Path(__file__).resolve().parent.parent / "agents" / "plans" / "test-tiers-and-qc.md"
    assert len(qc.parse_success_criteria(plan.read_text(encoding="utf-8"))) == 5
