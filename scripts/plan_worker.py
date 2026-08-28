#!/usr/bin/env python3
"""Plan Queue Worker — continuously implements plans from plan_queue.json.

Reads the top plan from the queue, launches an opencode agent session to
implement one unit of work, checks for completion, and loops until the
queue is empty.

Usage:
    ./scripts/plan_worker.py                  # Run the worker loop
    ./scripts/plan_worker.py --enqueue PATH   # Add a plan to the queue
    ./scripts/plan_worker.py --dequeue PATH   # Remove a plan from the queue
    ./scripts/plan_worker.py --status         # Show queue status
    ./scripts/plan_worker.py --dry-run        # Show what would run, don't execute
"""

import json
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = REPO_ROOT / "agents" / "plan_queue.json"
LOG_FILE = REPO_ROOT / "plan_worker.log"
BUILD_PROMPT_FILE = REPO_ROOT / "agents" / "BUILD_PROMPT.md"

MAX_RETRIES = 2

# Ordered fallback list of OpenCode model IDs. The first entry is the default
# model; if a run fails (e.g. transient free-tier 403/429/removed-model), the
# worker retries the same plan iteration with the next model before counting it
# as a failure.
MODEL_FALLBACKS = [
    "opencode/nemotron-3.5-lightning-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/big-pickle",
    "opencode/mimo-v2.5-free",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("plan_worker")


# ── Queue helpers ────────────────────────────────────────────────────────────


def load_queue() -> dict:
    if not QUEUE_FILE.exists():
        return {
            "queue": [],
            "completed": [],
            "model": "opencode/nemotron-3.5-lightning-free",
            "max_iterations": 20,
            "iteration_counts": {},
            "retry_counts": {},
        }
    return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))


def save_queue(state: dict) -> None:
    QUEUE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def increment_iteration(state: dict, plan: str) -> int:
    counts = state.setdefault("iteration_counts", {})
    counts[plan] = counts.get(plan, 0) + 1
    return counts[plan]


def get_retry_count(state: dict, plan: str) -> int:
    return state.get("retry_counts", {}).get(plan, 0)


def set_retry_count(state: dict, plan: str, n: int) -> None:
    state.setdefault("retry_counts", {})[plan] = n


def mark_completed(state: dict, plan: str, reason: str = "done") -> None:
    state["queue"] = [p for p in state["queue"] if p != plan]
    state["completed"].append(
        {
            "plan": plan,
            "reason": reason,
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    state.get("retry_counts", {}).pop(plan, None)
    # iteration_counts kept for audit trail


# ── Completion detection ─────────────────────────────────────────────────────


def check_plan_completed(plan_path: str) -> bool:
    """Check if a specific plan is done by inspecting the plan file's status.

    Completion is determined from the plan file's own `Status:` line (e.g.
    `Status: ✅ Completed`). This is strictly plan-specific: a successful run
    must update the plan it is working on, never a globally-shared marker.
    We deliberately do NOT treat STATE.md's "No remaining work" / "All tasks
    complete" as a completion signal, because STATE.md is shared across all
    plans and an agent finishing one plan can leave a phrase that falsely
    marks an unrelated queued plan as done.
    """
    full_plan = REPO_ROOT / plan_path
    if full_plan.exists():
        plan_text = full_plan.read_text(encoding="utf-8")
        # Match the plan file's own status line (e.g. `**Status:** ✅ Completed`
        # or `Status: ✅ Completed`) anchored to a whole line, so that prose
        # elsewhere in the file (e.g. a historical-record sentence that merely
        # mentions the marker) cannot be mistaken for a completion status.
        if re.search(
            r"(?m)^\*{0,2}Status\*{0,2}:\s*\*{0,2}\s*✅\s*Completed\s*$", plan_text
        ):
            log.info("Plan file status is ✅ Completed")
            return True

    return False


# ── Agent launch ─────────────────────────────────────────────────────────────


def build_agent_prompt(plan_path: str) -> str:
    """Build the full prompt to pipe to opencode."""
    build_prompt = BUILD_PROMPT_FILE.read_text(encoding="utf-8")
    return (
        f"{build_prompt}\n\n"
        f"---\n\n"
        f"You are working on this specific plan: {plan_path}\n"
        f"Focus exclusively on this plan. Do not work on anything else.\n"
    )


def run_agent(plan_path: str, model: str, iteration: int) -> int:
    """Launch opencode with the build prompt for one plan. Returns exit code."""
    prompt = build_agent_prompt(plan_path)

    cmd = [
        "opencode",
        "run",
        "--model",
        model,
        "--auto",
        "--dir",
        str(REPO_ROOT),
    ]

    log.info("Launching agent for %s (iteration %d)", plan_path, iteration)
    log.info("Command: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,  # 30-minute hard timeout per run
            cwd=str(REPO_ROOT),
            check=False,
        )

        # Log output
        if proc.stdout:
            log.info("Agent stdout (last 2000 chars):\n%s", proc.stdout[-2000:])
        if proc.stderr:
            log.warning("Agent stderr (last 2000 chars):\n%s", proc.stderr[-2000:])

        return proc.returncode

    except subprocess.TimeoutExpired:
        log.error("Agent timed out after 30 minutes for %s", plan_path)
        return -1
    except FileNotFoundError:
        log.error("opencode binary not found. Is it installed and on PATH?")
        return -1
    except Exception as e:  # noqa: BLE001 - broad catch-all for launcher robustness
        log.error("Unexpected error running agent: %s", e)
        return -1


# ── Main loop ────────────────────────────────────────────────────────────────


def worker_loop(dry_run: bool = False) -> None:
    state = load_queue()
    model = state.get("model", "opencode/nemotron-3.5-lightning-free")
    max_iter = state.get("max_iterations", 20)

    log.info("Plan worker started. Model=%s, max_iterations=%d", model, max_iter)
    log.info("Queue: %s", state["queue"])

    while state["queue"]:
        plan = state["queue"][0]
        iteration = increment_iteration(state, plan)
        log.info("─── Plan: %s | Iteration %d/%d ───", plan, iteration, max_iter)

        if iteration > max_iter:
            log.warning(
                "Plan %s exceeded max iterations (%d). Skipping.",
                plan,
                max_iter,
            )
            mark_completed(state, plan, reason="max_iterations_exceeded")
            save_queue(state)
            continue

        if dry_run:
            log.info("[DRY RUN] Would run agent for %s", plan)
            log.info("[DRY RUN] Prompt preview:\n%s", build_agent_prompt(plan)[:500])
            break

        exit_code = run_agent(plan, model, iteration)

        # On a non-zero exit, retry the same iteration with subsequent models
        # in the fallback list before counting this as a retry/failure.
        # This guards against transient free-tier failures (403/429/removed).
        fallback_index = MODEL_FALLBACKS.index(model) if model in MODEL_FALLBACKS else -1
        while exit_code != 0:
            next_index = fallback_index + 1
            if next_index >= len(MODEL_FALLBACKS):
                break  # No more fallbacks for this iteration
            fallback_index = next_index
            next_model = MODEL_FALLBACKS[next_index]
            log.warning(
                "Agent exited with code %d for %s (model %s). Retrying with fallback model %s.",
                exit_code,
                plan,
                model,
                next_model,
            )
            exit_code = run_agent(plan, next_model, iteration)
            model = next_model

        if exit_code != 0:
            log.warning(
                "Agent exited with code %d for %s using model %s after exhausting fallbacks.",
                exit_code,
                plan,
                model,
            )
            retries = get_retry_count(state, plan)
            set_retry_count(state, plan, retries + 1)
            log.warning(
                "Agent exited with code %d for %s (retry %d/%d)",
                exit_code,
                plan,
                retries + 1,
                MAX_RETRIES,
            )
            if retries + 1 > MAX_RETRIES:
                log.error(
                    "Plan %s failed %d times. Marking as error.", plan, MAX_RETRIES
                )
                mark_completed(state, plan, reason=f"error_exit_{exit_code}")
            save_queue(state)
            continue

        # Agent succeeded — check if plan is done
        set_retry_count(state, plan, 0)
        log.info("Plan %s succeeded with model %s.", plan, model)

        if check_plan_completed(plan):
            log.info("✅ Plan %s completed!", plan)
            mark_completed(state, plan, reason="done")
        else:
            log.info("Plan %s in progress. Next run will continue.", plan)

        save_queue(state)

    if not state["queue"]:
        log.info("🎉 Queue empty. All plans processed.")
    else:
        log.info("Worker stopped with %d plans remaining.", len(state["queue"]))


# ── CLI helpers ──────────────────────────────────────────────────────────────


def enqueue_plan(plan_path: str) -> None:
    state = load_queue()
    if plan_path in state["queue"]:
        log.info("Plan already in queue: %s", plan_path)
        return
    # Don't re-add if already completed
    if any(c["plan"] == plan_path for c in state["completed"]):
        log.info("Plan already completed: %s", plan_path)
        return
    state["queue"].append(plan_path)
    save_queue(state)
    log.info("Enqueued: %s (queue length: %d)", plan_path, len(state["queue"]))


def dequeue_plan(plan_path: str) -> None:
    state = load_queue()
    if plan_path not in state["queue"]:
        log.info("Plan not in queue: %s", plan_path)
        return
    state["queue"] = [p for p in state["queue"] if p != plan_path]
    save_queue(state)
    log.info("Dequeued: %s (queue length: %d)", plan_path, len(state["queue"]))


def show_status() -> None:
    state = load_queue()
    print(f"\n{'='*60}")
    print("  Plan Queue Worker Status")
    print(f"{'='*60}")
    print(f"  Model:            {state.get('model', 'opencode/nemotron-3.5-lightning-free')}")
    print(f"  Max iterations:   {state.get('max_iterations', 20)}")
    print(f"\n  Queue ({len(state['queue'])} pending):")
    for i, plan in enumerate(state["queue"]):
        iters = state.get("iteration_counts", {}).get(plan, 0)
        prefix = "→ " if i == 0 else "  "
        print(f"    {prefix}{plan}  (run {iters})")
    print(f"\n  Completed ({len(state['completed'])}):")
    for c in state["completed"]:
        print(f"    ✅ {c['plan']}  [{c['reason']}] @ {c['completed_at']}")
    if not state["completed"]:
        print("    (none)")
    print(f"{'='*60}\n")


def main() -> None:
    args = sys.argv[1:]

    if not args:
        worker_loop()
    elif args[0] == "--enqueue" and len(args) > 1:
        enqueue_plan(args[1])
    elif args[0] == "--dequeue" and len(args) > 1:
        dequeue_plan(args[1])
    elif args[0] == "--status":
        show_status()
    elif args[0] == "--dry-run":
        worker_loop(dry_run=True)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
