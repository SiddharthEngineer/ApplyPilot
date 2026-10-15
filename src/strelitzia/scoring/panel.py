"""Resume panel: blind A/B grading of two drafts per job, in Claude Pro chats, plus the user's own picks.

A run picks N jobs that have two comparable drafts (`select_items`): draft "a" is the older one (or the one
written under rules_version_a), draft "b" the newer one. Each item stores, at random, which of them packet 1
shows first; packet 2 shows the opposite order, so the two models see opposite orders. Versions are only ever
called "First" and "Second" in a packet. Every grade's winner is stored in packet 1's order.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from strelitzia.database import Connection, job_company

MODES = ("latest_vs_previous", "rules_a_vs_b")
DEFAULT_ITEMS = 5


@dataclass
class Item:
    job_url: str
    title: str
    company: str
    draft_a: dict  # the older draft (or rules_version_a)
    draft_b: dict  # the newer draft (or rules_version_b)


def _rows(conn: Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _drafts_by_job(conn: Connection) -> dict[str, list[dict]]:
    """{job_url: drafts oldest first}."""
    out: dict[str, list[dict]] = {}
    for d in _rows(conn, "SELECT * FROM drafts ORDER BY job_url, version"):
        out.setdefault(d["job_url"], []).append(d)
    return out


def _rules_pair(drafts: list[dict]) -> tuple[str | None, str | None]:
    """(older, newer) rules versions: the newest draft's rules and the newest other rules before it."""
    ordered = sorted(drafts, key=lambda d: d["created_at"] or "")
    if not ordered:
        return None, None
    newer = ordered[-1]["rules_version"]
    older = next((d["rules_version"] for d in reversed(ordered) if d["rules_version"] != newer), None)
    return older, newer


def select_items(conn: Connection, n: int = DEFAULT_ITEMS, mode: str = "latest_vs_previous",
                 rules_a: str | None = None, rules_b: str | None = None) -> list[Item]:
    """Up to n jobs with two comparable drafts, the job with the newest draft first.

    latest_vs_previous: the job's previous draft (a) vs its latest (b).
    rules_a_vs_b: the job's latest draft under rules_a vs its latest under rules_b. By default rules_b is the
    rules version of the newest draft overall and rules_a the newest other rules version before it.
    Drafts with identical resumes aren't comparable and are skipped.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown panel mode {mode!r} (use one of: {', '.join(MODES)})")
    by_job = _drafts_by_job(conn)
    if mode == "rules_a_vs_b" and (rules_a is None or rules_b is None):
        default_a, default_b = _rules_pair([d for ds in by_job.values() for d in ds])
        rules_a, rules_b = rules_a or default_a, rules_b or default_b
        if rules_a is None or rules_b is None or rules_a == rules_b:
            return []
    pairs = []
    for url, drafts in by_job.items():
        if mode == "latest_vs_previous":
            if len(drafts) < 2:
                continue
            a, b = drafts[-2], drafts[-1]
        else:
            under_a = [d for d in drafts if d["rules_version"] == rules_a]
            under_b = [d for d in drafts if d["rules_version"] == rules_b]
            if not under_a or not under_b:
                continue
            a, b = under_a[-1], under_b[-1]
        if a["resume_json"] == b["resume_json"]:
            continue
        pairs.append((url, a, b))
    pairs.sort(key=lambda p: p[2]["created_at"] or "", reverse=True)
    jobs = {}
    if pairs:
        urls = [p[0] for p in pairs]
        marks = ", ".join("?" * len(urls))
        jobs = {r["url"]: r for r in _rows(conn, f"SELECT url, title, company, site FROM jobs WHERE url IN ({marks})",
                                           tuple(urls))}
    items = []
    for url, a, b in pairs:
        job = jobs.get(url)
        if job is None:
            continue
        items.append(Item(job_url=url, title=job.get("title") or "Untitled role", company=job_company(job),
                          draft_a=a, draft_b=b))
        if len(items) >= n:
            break
    return items


def comparable_count(conn: Connection, mode: str = "latest_vs_previous", **kw) -> int:
    return len(select_items(conn, n=10**6, mode=mode, **kw))


def create_run(conn: Connection, n: int = DEFAULT_ITEMS, mode: str = "latest_vs_previous",
               rng: random.Random | None = None) -> str:
    """Pick the items, store a random order per item, and return the new run's id.

    Raises:
        ValueError: an unknown mode, or no job with two comparable drafts.
    """
    if n < 1:
        raise ValueError("A panel needs at least one item.")
    items = select_items(conn, n, mode)
    if not items:
        raise ValueError("No job has two comparable drafts yet.")
    rng = rng or random.SystemRandom()
    run_id = uuid.uuid4().hex
    rules_a = _common([i.draft_a["rules_version"] for i in items])
    rules_b = _common([i.draft_b["rules_version"] for i in items])
    conn.execute(
        "INSERT INTO panel_runs (id, created_at, mode, rules_version_a, rules_version_b, status) "
        "VALUES (?, ?, ?, ?, ?, 'open')",
        (run_id, datetime.now(UTC).isoformat(), mode, rules_a, rules_b),
    )
    for no, item in enumerate(items, 1):
        conn.execute(
            "INSERT INTO panel_items (run_id, item_no, job_url, draft_a_id, draft_b_id, shown_first) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, no, item.job_url, item.draft_a["id"], item.draft_b["id"], rng.choice("ab")),
        )
    conn.commit()
    return run_id


def _common(values: list[str | None]) -> str | None:
    """The most frequent value (the newest on a tie), e.g. the rules version most b drafts were written under."""
    counts: dict[str | None, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(reversed(list(counts)), key=lambda v: counts[v]) if counts else None


def get_run(conn: Connection, run_id: str) -> dict:
    """The run row. Raises KeyError when there is no such run."""
    row = conn.execute("SELECT * FROM panel_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return dict(row)


def run_items(conn: Connection, run_id: str) -> list[dict]:
    """The run's items in order, each with its job and both drafts ("draft_a", "draft_b")."""
    items = _rows(conn, "SELECT * FROM panel_items WHERE run_id = ? ORDER BY item_no", (run_id,))
    for item in items:
        job = conn.execute("SELECT * FROM jobs WHERE url = ?", (item["job_url"],)).fetchone()
        item["job"] = dict(job) if job else {"url": item["job_url"]}
        for side in ("a", "b"):
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (item[f"draft_{side}_id"],)).fetchone()
            item[f"draft_{side}"] = dict(row) if row else {}
    return items


def list_runs(conn: Connection) -> list[dict]:
    return _rows(conn, "SELECT id, created_at, mode, rules_version_a, rules_version_b, status FROM panel_runs "
                       "ORDER BY created_at DESC")


def json_loads(text: str | None, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
