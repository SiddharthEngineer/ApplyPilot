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
    """{job_url: drafts oldest first}, without the resume text and the cover letter (only the packet needs those)."""
    out: dict[str, list[dict]] = {}
    for d in _rows(conn, "SELECT id, job_url, version, rules_version, resume_json, created_at FROM drafts "
                         "ORDER BY job_url, version"):
        out.setdefault(d["job_url"], []).append(d)
    return out


def _rules_pair(drafts: list[dict], newer: str | None = None) -> tuple[str | None, str | None]:
    """(older, newer) rules versions: by default the newest draft's rules, and the newest other rules before it."""
    ordered = sorted(drafts, key=lambda d: d["created_at"] or "")
    if not ordered:
        return None, newer
    newer = newer or ordered[-1]["rules_version"]
    older = next((d["rules_version"] for d in reversed(ordered) if d["rules_version"] != newer), None)
    return older, newer


def select_items(conn: Connection, n: int = DEFAULT_ITEMS, mode: str = "latest_vs_previous",
                 rules_a: str | None = None, rules_b: str | None = None) -> list[Item]:
    """Up to n jobs with two comparable drafts, the job with the newest draft first.

    latest_vs_previous: the job's latest draft (b) vs the newest earlier draft with a different resume (a).
    rules_a_vs_b: the job's latest draft under rules_a vs its latest under rules_b. By default rules_b is the
    rules version of the newest draft overall, and rules_a the rules version of the newest draft under other rules.
    Drafts with identical resumes aren't comparable and are skipped.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown panel mode {mode!r} (use one of: {', '.join(MODES)})")
    by_job = _drafts_by_job(conn)
    if mode == "rules_a_vs_b" and (rules_a is None or rules_b is None):
        default_a, rules_b = _rules_pair([d for ds in by_job.values() for d in ds], rules_b)
        rules_a = rules_a or default_a
        if rules_a is None or rules_b is None or rules_a == rules_b:
            return []
    pairs = []
    for url, drafts in by_job.items():
        if mode == "latest_vs_previous":
            b = drafts[-1]
            a = next((d for d in reversed(drafts[:-1]) if d["resume_json"] != b["resume_json"]), None)
            if a is None:
                continue
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
    run, stored = get_run(conn, run_id), run_items(conn, run_id)
    conn.execute("UPDATE panel_runs SET packet_text = ?, packet_text_swapped = ? WHERE id = ?",
                 (panel_packet(conn, run, items=stored).text, panel_packet(conn, run, swapped=True, items=stored).text,
                  run_id))
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
    if not items:
        return items
    urls = sorted({i["job_url"] for i in items})
    jobs = {r["url"]: r for r in _rows(conn, f"SELECT * FROM jobs WHERE url IN ({', '.join('?' * len(urls))})",
                                       tuple(urls))}
    ids = sorted({i[f"draft_{side}_id"] for i in items for side in ("a", "b")})
    drafts = {r["id"]: r for r in _rows(conn, f"SELECT * FROM drafts WHERE id IN ({', '.join('?' * len(ids))})",
                                        tuple(ids))}
    for item in items:
        item["job"] = jobs.get(item["job_url"]) or {"url": item["job_url"]}
        for side in ("a", "b"):
            item[f"draft_{side}"] = drafts.get(item[f"draft_{side}_id"]) or {}
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


def panel_packet(conn: Connection, run: dict, swapped: bool = False, items: list[dict] | None = None) -> Packet:
    """Packet 1 (the stored order) or packet 2 (swapped): per item the job, its qualifications and both resumes.

    Nothing in it tells the versions apart except their text: no version numbers, rules versions or dates.
    """
    items = items if items is not None else run_items(conn, run["id"])
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


class PicksLocked(Exception):
    """Every item is picked and the report has un-blinded the panel."""


def record_pick(conn: Connection, run_id: str, item_no: int, winner: str) -> None:
    """Your blind pick for one item (first | second | tie, in packet 1's order).

    Picks can change until the last one is in; after that the report shows which version was newer, so they're locked.

    Raises:
        KeyError: no such run or item. ValueError: an unknown winner. PicksLocked: every item is already picked.
    """
    if winner not in WINNERS:
        raise ValueError(f"Unknown pick {winner!r} (use one of: {', '.join(WINNERS)})")
    if conn.execute("SELECT 1 FROM panel_items WHERE run_id = ? AND item_no = ?", (run_id, item_no)).fetchone() is None:
        raise KeyError(f"{run_id}/{item_no}")
    total = conn.execute("SELECT COUNT(*) FROM panel_items WHERE run_id = ?", (run_id,)).fetchone()[0]
    picked = conn.execute("SELECT COUNT(*) FROM panel_grades WHERE run_id = ? AND grader = 'you'", (run_id,)).fetchone()[0]
    if picked >= total:
        raise PicksLocked("Your picks are locked: the report has un-blinded this panel.")
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


# ── Report ────────────────────────────────────────────────────────────────

GRADE_POINTS = {"A": 4, "B": 3, "C": 2, "D": 1}
NEWER_IS_BETTER = 0.6  # your picks must favor the newer version at least this often
MIN_COMPARE = 10  # submissions per rules version before heard-back rates are compared
DUE_ITEMS = DEFAULT_ITEMS
DUE_DAYS = 30


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _model_summary(graded: dict[int, dict]) -> dict:
    """Mean overall grade (A=4 … D=1), clarity, met rate per qualification kind, per-qualification met rates and
    red flags, per side ("a" older, "b" newer)."""
    out: dict = {}
    for side in ("a", "b"):
        versions = [g["detail"].get(side) or {} for g in graded.values()]
        quals = [q for v in versions for q in v.get("qualifications") or [] if isinstance(q, dict)]
        met = {kind: _mean([1.0 if q.get("status") == "met" else 0.0 for q in quals if q.get("kind") == kind])
               for kind in ("required", "preferred")}
        out[side] = {
            "mean_overall": _mean([GRADE_POINTS[v["overall"]] for v in versions if v.get("overall") in GRADE_POINTS]),
            "mean_clarity": _mean([v["clarity"] for v in versions if isinstance(v.get("clarity"), int)]),
            "met_rate": met,
            "red_flags": sum(len(v.get("red_flags") or []) for v in versions),
        }
    per_qual: dict[tuple[int, str], dict] = {}
    for no, g in sorted(graded.items()):
        for side in ("a", "b"):
            for q in (g["detail"].get(side) or {}).get("qualifications") or []:
                if isinstance(q, dict) and q.get("text"):
                    row = per_qual.setdefault((no, q["text"]), {"item": no, "text": q["text"],
                                                                "kind": q.get("kind"), "a": None, "b": None})
                    row[side] = q.get("status")
    out["qualifications"] = list(per_qual.values())
    return out


def panel_report(conn: Connection, run_id: str) -> dict:
    """The un-blinded run: per grader the newer version's win rate (ties count half) and, for the models, grades,
    met rates and red flags per version; agreement between graders; and the verdict, which rests on your picks.

    Raises:
        KeyError: no such run.
    """
    run = get_run(conn, run_id)
    items = run_items(conn, run_id)
    all_grades = grades(conn, run_id)
    sides = {g: {no: _side_of(x["winner"], next(i for i in items if i["item_no"] == no)) for no, x in graded.items()}
             for g, graded in all_grades.items()}
    graders: dict[str, dict] = {}
    for grader in GRADERS:
        picked = sides.get(grader) or {}
        points = [1.0 if s == "b" else 0.5 if s == "tie" else 0.0 for s in picked.values()]
        entry = {"graded": len(picked), "newer_win_rate": _mean(points)}
        if grader in MODELS and picked:
            entry.update(_model_summary(all_grades[grader]))
        graders[grader] = entry

    def agreement(x: str, y: str) -> float | None:
        common = set(sides.get(x) or {}) & set(sides.get(y) or {})
        return _mean([1.0 if sides[x][no] == sides[y][no] else 0.0 for no in common]) if common else None

    total = len(items)
    yours = graders["you"]
    if yours["graded"] < total:
        verdict = None
    else:
        more_flags = any(graders[m].get("b", {}).get("red_flags", 0) > graders[m].get("a", {}).get("red_flags", 0)
                         for m in MODELS)
        rate = yours["newer_win_rate"] or 0.0
        if rate >= NEWER_IS_BETTER and not more_flags:
            verdict = "newer is better"
        elif rate <= 1 - NEWER_IS_BETTER:
            verdict = "worse"
        else:
            verdict = "no clear gain"
    return {
        "run_id": run_id, "status": run["status"], "mode": run["mode"], "items_total": total,
        "rules_version_a": run["rules_version_a"], "rules_version_b": run["rules_version_b"],
        "items": [{"item": i["item_no"], "title": i["job"].get("title"), "company": job_company(i["job"]),
                   "older_version": i["draft_a"].get("version"), "newer_version": i["draft_b"].get("version"),
                   "newer_shown": "first" if i["shown_first"] == "b" else "second",
                   "winners": {g: _side_label((sides.get(g) or {}).get(i["item_no"])) for g in GRADERS}}
                  for i in items],
        "graders": graders,
        "agreement": {"sonnet_vs_opus": agreement("sonnet", "opus"), "sonnet_vs_you": agreement("sonnet", "you"),
                      "opus_vs_you": agreement("opus", "you")},
        "verdict": verdict,
        "heard_back": heard_back_by_rules(conn),
    }


def _side_label(side: str | None) -> str | None:
    return {"a": "older", "b": "newer", "tie": "tie"}.get(side) if side else None


def heard_back_by_rules(conn: Connection) -> list[dict]:
    """Across runs: per submitted_rules_version, how many submissions heard back (rate None under MIN_COMPARE)."""
    rows = _rows(conn, "SELECT submitted_rules_version AS rules, COUNT(*) AS submitted, "
                       "SUM(CASE WHEN user_status = 'heard_back' THEN 1 ELSE 0 END) AS heard_back FROM jobs "
                       "WHERE submitted_rules_version IS NOT NULL GROUP BY submitted_rules_version "
                       "ORDER BY MIN(submitted_at)")
    out = []
    for r in rows:
        enough = r["submitted"] >= MIN_COMPARE
        out.append({"rules_version": r["rules"], "submitted": r["submitted"], "heard_back": r["heard_back"] or 0,
                    "rate": round((r["heard_back"] or 0) / r["submitted"], 2) if enough else None,
                    "note": None if enough else "too few to compare"})
    return out


def panel_due(conn: Connection, rules_version: str | None = None, now: datetime | None = None) -> str | None:
    """Why a panel is due, or None: the rules changed since the last completed panel (and 5 jobs have a draft
    under the new rules and one under older rules), or there has been no panel or 30+ days since the last one
    (and 5 jobs have two comparable drafts). Not while a run started in the last 30 days is still open."""
    from strelitzia.scoring.workbench import RULES_VERSION

    rules_version = rules_version or RULES_VERSION
    now = now or datetime.now(UTC)
    newest = conn.execute("SELECT created_at, status FROM panel_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if newest is not None and newest[1] != "complete" and \
            (now - datetime.fromisoformat(newest[0])).days < DUE_DAYS:
        return None  # a panel is in progress
    last = conn.execute("SELECT created_at, rules_version_b FROM panel_runs WHERE status = 'complete' "
                        "ORDER BY created_at DESC LIMIT 1").fetchone()
    if last is not None and last[1] != rules_version and \
            len(select_items(conn, DUE_ITEMS, "rules_a_vs_b", rules_b=rules_version)) >= DUE_ITEMS:
        return "the resume rules changed since the last panel"
    if len(select_items(conn, DUE_ITEMS)) < DUE_ITEMS:
        return None
    if last is None:
        return "no panel has been run yet"
    days = (now - datetime.fromisoformat(last[0])).days
    return f"{days} days since the last panel" if days >= DUE_DAYS else None
