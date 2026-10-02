#!/usr/bin/env python3
"""Morning QC: run the test tiers and print a plan's review checklist.

Usage: python scripts/qc.py <plan-slug> [--skip-live] [--skip-llm]

1. Unit tier:  pytest tests/ -q
2. Live tier:  pytest -m live --run-live -q   (unless --skip-live)
3. LLM tier:   pytest -m llm --run-llm -q     (unless --skip-llm)
4. Prints agents/plans/<slug>.md Success Criteria as checkboxes, plus unfinished tasks that run locally.

Exits non-zero if any pytest tier failed. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLANS_DIR = REPO / "agents" / "plans"

# pytest exit code 5 means "no tests collected", which is fine for an empty tier.
_PYTEST_OK = {0, 5}


def parse_success_criteria(text: str) -> list[str]:
    """Return the numbered items under `## Success Criteria`."""
    match = re.search(r"^## Success Criteria\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return [m.group(1).strip() for m in re.finditer(r"^\s*\d+\.\s+(.+)$", match.group(1), re.MULTILINE)]


def parse_tasks(text: str) -> list[dict[str, str]]:
    """Return tasks as dicts with `title`, `runs` and `status` (empty string when missing)."""
    tasks = []
    for match in re.finditer(r"^### (Task [^\n]+)\n(.*?)(?=^### |^## |\Z)", text, re.MULTILINE | re.DOTALL):
        body = match.group(2)
        runs = re.search(r"^\*\*Runs:\*\*\s*(.+)$", body, re.MULTILINE)
        status = re.search(r"^\*\*Status:\*\*\s*(.+)$", body, re.MULTILINE)
        tasks.append({
            "title": match.group(1).strip(),
            "runs": runs.group(1).strip() if runs else "",
            "status": status.group(1).strip() if status else "",
        })
    return tasks


def local_tasks_pending(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    """Tasks whose Runs line mentions `local` and whose status isn't ✅."""
    return [t for t in tasks if "local" in t["runs"] and not t["status"].startswith("✅")]


def run_tier(name: str, args: list[str]) -> bool:
    print(f"\n=== {name}: {' '.join(args)}", flush=True)
    code = subprocess.call(args, cwd=REPO)
    ok = code in _PYTEST_OK
    print(f"=== {name}: {'OK' if ok else f'FAILED (exit {code})'}", flush=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug", help="plan slug, e.g. test-tiers-and-qc")
    parser.add_argument("--skip-live", action="store_true", help="skip live network tests")
    parser.add_argument("--skip-llm", action="store_true", help="skip LLM API tests")
    args = parser.parse_args(argv)

    plan_path = PLANS_DIR / f"{args.slug}.md"
    if not plan_path.exists():
        print(f"No plan at {plan_path}", file=sys.stderr)
        return 2

    py = sys.executable
    results = {"unit": run_tier("unit", [py, "-m", "pytest", "tests/", "-q"])}
    if not args.skip_live:
        results["live"] = run_tier("live", [py, "-m", "pytest", "tests/", "-m", "live", "--run-live", "-q"])
    if not args.skip_llm:
        results["llm"] = run_tier("llm", [py, "-m", "pytest", "tests/", "-m", "llm", "--run-llm", "-q"])

    text = plan_path.read_text(encoding="utf-8")
    print(f"\n## Success Criteria ({args.slug})")
    for item in parse_success_criteria(text):
        print(f"- [ ] {item}")

    pending = local_tasks_pending(parse_tasks(text))
    print("\n## Tasks for you")
    for task in pending:
        print(f"- [ ] {task['title']} (Runs: {task['runs']}; Status: {task['status'] or 'unknown'})")
    if not pending:
        print("- none")

    print("\n## Tiers: " + ", ".join(f"{k} {'OK' if v else 'FAILED'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
