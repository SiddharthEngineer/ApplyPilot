#!/usr/bin/env python3
"""Eval for the fit assessment: which Claude model and effort rank jobs closest to your own judgement (M20).

    mkdir -p ~/.strelitzia/eval
    python scripts/eval_fit.py sample --n 30 > ~/.strelitzia/eval/fit_labels.csv
    # fill in `label` with good, ok or bad for each row
    python scripts/eval_fit.py run --models claude-haiku-5-5,claude-sonnet-5-5 --efforts low,medium

`sample` picks extracted jobs spread across role categories and current fit scores. `run` assesses every labelled job
synchronously with each model/effort combination, scores it with the current preferences (`compute_fit`), and prints
the Spearman correlation of the overall score with the labels (bad=0, ok=1, good=2), how many good jobs land in the
top 10, and the cost per 1,000 jobs from the `llm_usage` rows the run wrote. It never writes to the jobs table.

The labels file holds job data: keep it under ~/.strelitzia, never in the repo.
Pick the cheapest combination within 0.05 correlation of the best, and set it in llm_models.yaml (`scoring`).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from strelitzia import config
from strelitzia.database import init_db, job_company, job_key
from strelitzia.enrichment.posting_model import JobPosting
from strelitzia.llm import EFFORTS, LLMClient, LLMStopRun
from strelitzia.scoring.assessment import (
    ASSESS_SCHEMA,
    assess_messages,
    compute_fit,
    load_resume_text,
    parse_assessment,
)
from strelitzia.scoring.preferences import Preferences, load_preferences

LABELS_PATH = config.APP_DIR / "eval" / "fit_labels.csv"
LABEL_VALUES = {"bad": 0, "ok": 1, "good": 2}
CSV_COLUMNS = ("job_key", "title", "company", "label")
TOP_N = 10
# Combinations within this much Spearman correlation of the best count as equally good; the cheapest one wins.
TOLERANCE = 0.05
MAX_OUTPUT_TOKENS = 2048  # as scorer._MAX_OUTPUT_TOKENS

_EXTRACTED = "SELECT * FROM jobs WHERE full_description IS NOT NULL AND details_json IS NOT NULL"


# ── sample ────────────────────────────────────────────────────────────────

def _posting(job: dict) -> JobPosting:
    try:
        return JobPosting.from_dict(json.loads(job["details_json"]))
    except ValueError:
        return JobPosting()


def _spread(items: list) -> list:
    """`items` reordered so that every prefix is spread over the whole list (bisection order: ends, middle, ...)."""
    if not items:
        return []
    order, seen = [], set()
    queue = [(0, len(items) - 1)]
    while queue:
        lo, hi = queue.pop(0)
        for i in (lo, hi):
            if i not in seen:
                seen.add(i)
                order.append(i)
        if hi - lo > 1:
            mid = (lo + hi) // 2
            if mid not in seen:
                seen.add(mid)
                order.append(mid)
            queue += [(lo, mid), (mid, hi)]
    return [items[i] for i in order]


def sample_jobs(jobs: list[dict], n: int) -> list[dict]:
    """Up to `n` jobs, round-robin over role categories, each category's picks spread over its fit scores."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for job in sorted(jobs, key=lambda j: j["url"]):
        groups[_posting(job).role_category].append(job)
    queues = [_spread(sorted(g, key=lambda j: (j.get("fit_score") is None, j.get("fit_score") or 0)))
              for _, g in sorted(groups.items())]
    picked: list[dict] = []
    for i in range(max((len(q) for q in queues), default=0)):
        for q in queues:
            if i < len(q) and len(picked) < n:
                picked.append(q[i])
    return picked


def cmd_sample(conn, n: int, out) -> int:
    jobs = [dict(r) for r in conn.execute(_EXTRACTED).fetchall()]
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for job in sample_jobs(jobs, n):
        company = _posting(job).company or job_company(job)
        writer.writerow((job.get("job_key") or job_key(job["url"]), job.get("title") or "", company, ""))
    return 0


# ── run ───────────────────────────────────────────────────────────────────

def read_labels(path: Path) -> dict[str, int]:
    """job_key -> 0/1/2 for the rows labelled good, ok or bad (others are skipped)."""
    with path.open(newline="", encoding="utf-8") as f:
        return {row["job_key"].strip(): LABEL_VALUES[row["label"].strip().lower()]
                for row in csv.DictReader(f)
                if (row.get("label") or "").strip().lower() in LABEL_VALUES and (row.get("job_key") or "").strip()}


def _ranks(values: list[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (Pearson on tie-averaged ranks); None when either side has no spread."""
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def good_in_top(scores: list[float], labels: list[int], n: int = TOP_N) -> int:
    """How many of the `n` best-scored jobs are labelled good (ties keep the labels file's order)."""
    top = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
    return sum(labels[i] == LABEL_VALUES["good"] for i in top)


def claude_assess(client: LLMClient, prefs: Preferences, resume_text: str, job: dict) -> str:
    """The assessment request the scorer sends, with this combination's model and effort."""
    return client.chat(assess_messages(prefs, resume_text, job), max_tokens=MAX_OUTPUT_TOKENS, schema=ASSESS_SCHEMA,
                       job_url=job.get("url"))


def _usage_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM llm_usage").fetchall()}


def eval_combo(conn, client: LLMClient, prefs: Preferences, resume_text: str, jobs: list[dict],
               labels: dict[str, int], assess: Callable = claude_assess) -> dict:
    """Assess `jobs` with one model/effort and measure the ranking against the labels."""
    before = _usage_ids(conn)
    scores, truth, errors = [], [], 0
    for job in jobs:
        try:
            assessment = parse_assessment(assess(client, prefs, resume_text, job))
        except LLMStopRun:
            raise
        except Exception as e:  # noqa: BLE001 - one failed job shouldn't end the eval
            print(f"  {client.model}/{client.effort}: {job.get('title', '?')}: {e}", file=sys.stderr)
            assessment = None
        if assessment is None:
            errors += 1
            continue
        overall, _, _ = compute_fit(prefs, {**job, "fit_assessment": json.dumps(assessment), "fit_score": None})
        scores.append(float(overall or 0))
        truth.append(labels[job["job_key"]])
    new = _usage_ids(conn) - before
    cost = 0.0
    if new:
        marks = ", ".join("?" * len(new))
        cost = float(conn.execute(f"SELECT SUM(cost_usd) FROM llm_usage WHERE id IN ({marks})",
                                  list(new)).fetchone()[0] or 0.0)
    return {
        "model": client.model, "effort": client.effort, "jobs": len(scores), "errors": errors,
        "spearman": spearman(scores, truth), "good_in_top10": good_in_top(scores, truth),
        "usd_per_1k": cost / len(jobs) * 1000 if jobs else 0.0,
    }


def pick(results: list[dict]) -> dict | None:
    """The cheapest combination within TOLERANCE correlation of the best one."""
    rated = [r for r in results if r["spearman"] is not None]
    if not rated:
        return None
    best = max(r["spearman"] for r in rated)
    return min((r for r in rated if r["spearman"] >= best - TOLERANCE), key=lambda r: r["usd_per_1k"])


def _labelled_jobs(conn, labels: dict[str, int]) -> list[dict]:
    jobs = []
    for row in conn.execute(_EXTRACTED).fetchall():
        job = dict(row)
        job["job_key"] = job.get("job_key") or job_key(job["url"])
        if job["job_key"] in labels:
            jobs.append(job)
    order = list(labels)
    return sorted(jobs, key=lambda j: order.index(j["job_key"]))


def cmd_run(conn, labels_path: Path, models: list[str], efforts: list[str], out,
            assess: Callable = claude_assess) -> int:
    labels = read_labels(labels_path)
    jobs = _labelled_jobs(conn, labels)
    if not jobs:
        print(f"No labelled extracted jobs in {labels_path}: fill in `label` with good, ok or bad.", file=sys.stderr)
        return 1
    if missing := len(labels) - len(jobs):
        print(f"{missing} labelled job(s) are not extracted in this database; skipping them.", file=sys.stderr)
    prefs, resume_text = load_preferences(conn), load_resume_text(conn)
    results = []
    print(f"{'model':<24} {'effort':<7} {'jobs':>4} {'errors':>6} {'spearman':>8} {'good@10':>7} {'$/1k jobs':>9}",
          file=out)
    for model in models:
        for effort in efforts:
            try:
                r = eval_combo(conn, LLMClient("scoring", model, effort), prefs, resume_text, jobs, labels, assess)
            except LLMStopRun as e:
                print(f"Stopped: {e}", file=sys.stderr)
                return 1
            results.append(r)
            rho = "n/a" if r["spearman"] is None else f"{r['spearman']:.3f}"
            print(f"{r['model']:<24} {r['effort']:<7} {r['jobs']:>4} {r['errors']:>6} {rho:>8} "
                  f"{r['good_in_top10']:>7} {r['usd_per_1k']:>9.2f}", file=out)
    choice = pick(results)
    if choice:
        print(f"\nPick: {choice['model']}, effort {choice['effort']} (cheapest within {TOLERANCE} of the best "
              "correlation). Set it as `scoring` in llm_models.yaml.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sample", help="print a CSV of jobs to label")
    p.add_argument("--n", type=int, default=30)
    p = sub.add_parser("run", help="assess the labelled jobs with each model/effort and compare")
    p.add_argument("--labels", type=Path, default=LABELS_PATH)
    p.add_argument("--models", default="claude-haiku-5-5,claude-sonnet-5-5")
    p.add_argument("--efforts", default="low,medium")
    args = parser.parse_args(argv)

    conn = init_db()
    if args.command == "sample":
        return cmd_sample(conn, args.n, sys.stdout)
    config.load_env()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    efforts = split(args.efforts)
    if bad := [e for e in efforts if e not in EFFORTS]:
        parser.error(f"unknown effort {', '.join(bad)}; expected {', '.join(EFFORTS)}")
    return cmd_run(conn, args.labels, split(args.models), efforts, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
