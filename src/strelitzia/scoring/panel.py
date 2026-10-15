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
from strelitzia.scoring.targets import targets
from strelitzia.scoring.workbench import _Doc, _obj, extract_json, posting_of, schema_errors

MODES = ("latest_vs_previous", "rules_a_vs_b")
DEFAULT_ITEMS = 5
PANEL_VERSION = 1
MODELS = ("sonnet", "opus")
GRADERS = (*MODELS, "you")
# Which packet each model grades: Sonnet gets packet 1 (the stored order), Opus packet 2 (swapped).
GRADER_PACKET = {"sonnet": 1, "opus": 2}
GRADES = ("A", "B", "C", "D")
WINNERS = ("first", "second", "tie")


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
    run = get_run(conn, run_id)
    conn.execute("UPDATE panel_runs SET packet_text = ?, packet_text_swapped = ? WHERE id = ?",
                 (panel_packet(conn, run).text, panel_packet(conn, run, swapped=True).text, run_id))
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


def _json(text: str | None, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


# ── Rubric ────────────────────────────────────────────────────────────────

PROJECT_NAME = "Strelitzia Panel"
PROJECT_INSTRUCTIONS = (
    "You are a strict resume screener. Each chat brings a blind panel packet: several jobs, each with two versions "
    "of a resume called First and Second. Grade them with the \"Strelitzia panel rubric\" document, which is "
    "binding. Quote evidence verbatim from the resume, never infer experience that isn't written down, and end "
    "every reply with exactly one ```json block in the rubric's format."
)

_QUAL = _obj({"text": {"type": "string"}, "kind": {"type": "string", "enum": ["required", "preferred"]},
              "status": {"type": "string", "enum": ["met", "partial", "not_met"]},
              "evidence": {"type": "string"}})
_VERSION = _obj({"qualifications": {"type": "array", "items": _QUAL},
                 "overall": {"type": "string", "enum": list(GRADES)},
                 "clarity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                 "red_flags": {"type": "array", "items": {"type": "string"}}})
REPLY_SCHEMA = _obj({
    "panel": {"type": "integer", "enum": [PANEL_VERSION]},
    "items": {"type": "array", "items": _obj({"item": {"type": "integer"}, "first": _VERSION, "second": _VERSION,
                                              "winner": {"type": "string", "enum": list(WINNERS)},
                                              "why": {"type": "string"}})},
})

EXAMPLE_REPLY = {
    "panel": PANEL_VERSION,
    "items": [{
        "item": 1,
        "first": {
            "qualifications": [
                {"text": "Airflow", "kind": "required", "status": "met",
                 "evidence": "Migrated the nightly orders pipeline from cron to Airflow"},
                {"text": "Terraform", "kind": "preferred", "status": "not_met", "evidence": ""},
            ],
            "overall": "B", "clarity": 4,
            "red_flags": ["Vague bullet: 'Worked on various data projects'"],
        },
        "second": {"qualifications": ["<same list, graded for the second version>"], "overall": "A", "clarity": 5,
                   "red_flags": []},
        "winner": "second",
        "why": "Second shows every required qualification with a concrete result in the first role.",
    }],
}


def _rubric() -> _Doc:
    d = _Doc("Strelitzia panel rubric")
    d.p("You grade resumes the way an applicant-tracking screener and a busy recruiter would. Each packet holds "
        "numbered items. Each item is one job (title, company, required and preferred qualifications) and two "
        "versions of my resume for it, called First and Second. Nothing says which version is newer; don't guess.")
    d.h("For each item, grade each version on its own first")
    d.ul([
        "1. Qualifications: for every required and every preferred qualification listed for the job, give "
        "met, partial or not_met. As evidence, quote the resume verbatim (the shortest phrase that shows it). "
        "Leave evidence empty when not_met. Only what is written counts: don't infer.",
        "2. Overall grade A-D. A: every required qualification met, plus most preferred ones. B: every required "
        "qualification met. C: most required qualifications met. D: otherwise. Partial counts as not met here.",
        "3. Clarity 1-5: can a recruiter see the fit for this job in 10 seconds? 5 = immediately, 1 = not at all.",
        "4. Red flags, each a short phrase: claims that look unsupported or inflated, keyword stuffing (skills "
        "listed with no work behind them), vague bullets with no action or result.",
    ])
    d.h("Then compare")
    d.ul(["5. Winner: first, second or tie, with one sentence on why. Judge the versions as a hiring screen "
          "would: qualifications met first, then clarity, then red flags. Length and position don't matter."])
    d.h("Output")
    d.p("Write a short comment per item if you like, then end your reply with exactly one ```json block in this "
        "format, with one entry per item in the packet, in order. Keep the qualification texts as the packet "
        "lists them. Use only the keys shown.")
    d.pre(json.dumps(EXAMPLE_REPLY, indent=2))
    return d


def rubric_doc() -> str:
    """The rubric as HTML (the Google Doc in the Strelitzia Panel project)."""
    return _rubric().html()


def rubric_text() -> str:
    return _rubric().text()


# ── Packets ───────────────────────────────────────────────────────────────

@dataclass
class Packet:
    text: str
    prompt: str


def _order(item: dict, swapped: bool) -> tuple[str, str]:
    """The sides ("a"/"b") shown first and second in packet 1 (swapped=False) or packet 2."""
    first = item["shown_first"]
    second = "b" if first == "a" else "a"
    return (second, first) if swapped else (first, second)


def panel_packet(conn: Connection, run: dict, swapped: bool = False) -> Packet:
    """Packet 1 (the stored order) or packet 2 (swapped): per item the job, its qualifications and both resumes.

    Nothing in it tells the versions apart except their text: no version numbers, rules versions or dates.
    """
    items = run_items(conn, run["id"])
    d = _Doc("Strelitzia resume panel")
    d.p(f"{len(items)} items. Grade each with the Strelitzia panel rubric, then end with the JSON block.")
    for item in items:
        job = item["job"]
        d.h(f"Item {item['item_no']}: {job.get('title') or 'Untitled role'} at {job_company(job)}")
        posting_targets = targets(posting_of(job))
        for kind, label in (("required", "Required qualifications"), ("preferred", "Preferred qualifications")):
            listed = [t.text for t in posting_targets if t.kind == kind]
            if listed:
                d.p(label + ":").ul(listed)
            else:
                d.p(f"{label}: none listed.")
        for label, side in zip(("First", "Second"), _order(item, swapped), strict=True):
            d.p(f"Item {item['item_no']}, {label} resume:")
            d.pre((item[f"draft_{side}"].get("resume_text") or "").strip())
    text = d.text()
    return Packet(text=text, prompt=_prompt(text))


def _prompt(packet_text: str) -> str:
    return ("Grade this blind resume panel with the \"Strelitzia panel rubric\" document in this project. "
            "Grade each version on its own first, then pick a winner. End your reply with the JSON block.\n\n"
            + packet_text)


def run_packets(run: dict) -> list[dict]:
    """The two stored packets with their prompts, for the page."""
    out = []
    for no, (model, key) in enumerate((("sonnet", "packet_text"), ("opus", "packet_text_swapped")), 1):
        text = run.get(key) or ""
        out.append({"packet": no, "model": model, "text": text, "prompt": _prompt(text)})
    return out


# ── Import ────────────────────────────────────────────────────────────────

@dataclass
class ImportOutcome:
    ok: bool
    errors: list[str]
    graded: int = 0

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "graded": self.graded}


def _to_packet1(winner: str, swapped: bool) -> str:
    if not swapped or winner == "tie":
        return winner
    return "second" if winner == "first" else "first"


def _side_of(winner: str, item: dict) -> str:
    """'a', 'b' or 'tie' for a winner in packet 1's order."""
    if winner == "tie":
        return "tie"
    first, second = _order(item, swapped=False)
    return first if winner == "first" else second


def import_grades(conn: Connection, run_id: str, grader: str, text: str) -> ImportOutcome:
    """Store a model's pasted reply for the run (replacing that model's earlier grades).

    The reply's first/second are in the order of the packet that model graded; each version's grade is stored
    under its draft side ("a"/"b"), and the winner in packet 1's order.
    """
    if grader not in MODELS:
        return ImportOutcome(False, [f"Unknown grader {grader!r} (use one of: {', '.join(MODELS)})"])
    try:
        get_run(conn, run_id)
    except KeyError:
        return ImportOutcome(False, ["No such panel run."])
    try:
        data, where = extract_json(text)
    except ValueError as e:
        return ImportOutcome(False, [str(e)])
    errors = schema_errors(data, REPLY_SCHEMA)
    if errors:
        return ImportOutcome(False, [f"{where} doesn't match the panel format: " + "; ".join(errors[:8])])
    items = run_items(conn, run_id)
    graded = data["items"]
    if len(graded) != len(items):
        return ImportOutcome(False, [f"The reply grades {len(graded)} items, but this panel has {len(items)}. "
                                     "Ask Claude to grade every item in the packet."])
    numbers = sorted(g["item"] for g in graded)
    expected = [i["item_no"] for i in items]
    if numbers != expected:
        return ImportOutcome(False, [f"The reply grades items {numbers}, but this panel has items {expected}."])
    swapped = GRADER_PACKET[grader] == 2
    by_no = {i["item_no"]: i for i in items}
    now = datetime.now(UTC).isoformat()
    conn.execute("DELETE FROM panel_grades WHERE run_id = ? AND grader = ?", (run_id, grader))
    for g in graded:
        item = by_no[g["item"]]
        first_side, second_side = _order(item, swapped)
        detail = {first_side: g["first"], second_side: g["second"], "why": g.get("why", "")}
        conn.execute(
            "INSERT INTO panel_grades (run_id, item_no, grader, winner, detail_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, g["item"], grader, _to_packet1(g["winner"], swapped), json.dumps(detail), now),
        )
    _update_status(conn, run_id)
    conn.commit()
    return ImportOutcome(True, [], graded=len(graded))


def record_pick(conn: Connection, run_id: str, item_no: int, winner: str) -> None:
    """Your blind pick for one item (first | second | tie, in packet 1's order).

    Raises:
        KeyError: no such run or item. ValueError: an unknown winner.
    """
    if winner not in WINNERS:
        raise ValueError(f"Unknown pick {winner!r} (use one of: {', '.join(WINNERS)})")
    if conn.execute("SELECT 1 FROM panel_items WHERE run_id = ? AND item_no = ?", (run_id, item_no)).fetchone() is None:
        raise KeyError(f"{run_id}/{item_no}")
    conn.execute("DELETE FROM panel_grades WHERE run_id = ? AND item_no = ? AND grader = 'you'", (run_id, item_no))
    conn.execute(
        "INSERT INTO panel_grades (run_id, item_no, grader, winner, detail_json, created_at) "
        "VALUES (?, ?, 'you', ?, '{}', ?)",
        (run_id, item_no, winner, datetime.now(UTC).isoformat()),
    )
    _update_status(conn, run_id)
    conn.commit()


def grades(conn: Connection, run_id: str) -> dict[str, dict[int, dict]]:
    """{grader: {item_no: {"winner", "detail"}}}."""
    out: dict[str, dict[int, dict]] = {}
    for g in _rows(conn, "SELECT * FROM panel_grades WHERE run_id = ?", (run_id,)):
        out.setdefault(g["grader"], {})[g["item_no"]] = {"winner": g["winner"], "detail": _json(g["detail_json"], {})}
    return out


def _update_status(conn: Connection, run_id: str) -> None:
    """A run is complete once you picked every item and at least one model graded it."""
    total = conn.execute("SELECT COUNT(*) FROM panel_items WHERE run_id = ?", (run_id,)).fetchone()[0]
    done = {g: len(items) == total for g, items in grades(conn, run_id).items()}
    complete = done.get("you", False) and any(done.get(m, False) for m in MODELS)
    conn.execute("UPDATE panel_runs SET status = ? WHERE id = ?", ("complete" if complete else "open", run_id))
