"""Claude workbench: resumes and cover letters are written in a Claude Pro chat, not through the API.

The app prepares the inputs and checks the output:
- `rules_doc()` and `library_doc()` are the fixed inputs (Google Docs attached to a Claude Project once).
- `job_packet()` is the per-job input: the posting, its required/preferred targets marked evidenced or not,
  the allowed role keys and project slugs, and the current draft.
- Claude ends every reply with a ```json block matching RESULT_SCHEMA. The user pastes that reply back and
  `import_result()` runs the deterministic checks, then renders and records the draft.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from strelitzia import config, tracking
from strelitzia.database import Connection, job_company
from strelitzia.profile import load_profile
from strelitzia.scoring.content_library import ContentLibrary, parse_content_library_text
from strelitzia.scoring.resume_model import TailoredResume
from strelitzia.scoring.targets import EvidenceMap, coverage, evidence_map, matches
from strelitzia.scoring.template import (
    fixed_from_data,
    fixed_roles_from_resume,
    parse_base_resume,
    render_resume,
    resume_to_text,
)
from strelitzia.scoring.validator import (
    BANNED_WORDS,
    resume_parts,
    validate_claims,
    validate_letter,
    validate_numbers_known,
    validate_pdf_text,
    validate_provenance,
    validate_resume_model,
    validate_skills_known,
    validate_visible,
    validate_wording,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HEADLINE_MAX = 110
BULLET_MAX = 230
COVER_MAX_WORDS = 250
PROMPT_MAX = 600
DESCRIPTION_MAX = 12000


# ── Output contract ───────────────────────────────────────────────────────

def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def result_schema(library: ContentLibrary | None = None) -> dict:
    """JSON Schema of the pasted result; with a library, role keys and project slugs are enums."""
    slug: dict = {"type": "string"}
    role_key: dict = {"type": "string"}
    if library is not None:
        library.ensure_keys()
        slug = {"type": "string", "enum": [p.slug for r in library.roles for p in r.projects]}
        role_key = {"type": "string", "enum": [r.key for r in library.roles]}
    bullet = _obj({"text": {"type": "string"}, "project_ids": {"type": "array", "items": slug}})
    resume = _obj({
        "headline": {"type": "string", "maxLength": HEADLINE_MAX},
        "roles": {"type": "array", "items": _obj({"role_key": role_key, "bullets": {"type": "array", "items": bullet}})},
        "skills": {"type": "array", "items": _obj({"category": {"type": "string"}, "items": {"type": "string"}})},
        "dropped_roles": {"type": "array", "items": role_key},
    })
    cover = _obj({"paragraphs": {"type": "array", "items": {"type": "string"}},
                  "evidence_slugs": {"type": "array", "items": slug}})
    cover["type"] = ["object", "null"]
    return _obj({
        "strelitzia": {"type": "integer", "enum": [SCHEMA_VERSION]},
        "resume": resume,
        "cover_letter": cover,
        "notes_for_user": {"type": "string"},
    }, required=["strelitzia", "resume", "cover_letter"])


RESULT_SCHEMA = result_schema()

_PY_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
             "boolean": bool, "null": type(None)}


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Validate against the JSON Schema subset RESULT_SCHEMA uses (type, enum, properties, items, maxLength)."""
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        ok = any(isinstance(value, _PY_TYPES[n]) and not (n in ("integer", "number") and isinstance(value, bool))
                 for n in names)
        if not ok:
            return [f"{path}: expected {' or '.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(map(json.dumps, schema["enum"][:20]))
        return [f"{path}: {json.dumps(value)} is not allowed (allowed: {allowed})"]
    errors: list[str] = []
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{path}: {len(value)} characters, at most {schema['maxLength']}")
    if isinstance(value, dict) and "properties" in schema:
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing \"{key}\"")
        for key, sub in value.items():
            if key in schema["properties"]:
                errors.extend(schema_errors(sub, schema["properties"][key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key \"{key}\"")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], f"{path}[{i}]"))
    return errors


EXAMPLE_RESULT = {
    "strelitzia": SCHEMA_VERSION,
    "resume": {
        "headline": "Data engineer building Airflow and Spark pipelines for finance analytics",
        "roles": [{"role_key": "<role key>", "bullets": [
            {"text": "Migrated the nightly orders pipeline from cron to Airflow, cutting runtime from 6 hours to 45 minutes",
             "project_ids": ["<project slug>"]}]}],
        "skills": [{"category": "Languages", "items": "Python, SQL"}],
        "dropped_roles": [],
    },
    "cover_letter": {"paragraphs": ["<paragraph 1>", "<paragraph 2>", "<paragraph 3>"],
                     "evidence_slugs": ["<project slug>"]},
    "notes_for_user": "Optional: anything you want me to know, e.g. qualifications I can't claim.",
}


# ── Documents (one builder, rendered as HTML for Drive and as text for the clipboard) ──

@dataclass
class _Doc:
    title: str
    blocks: list[tuple[str, object]] = field(default_factory=list)

    def h(self, text: str) -> _Doc:
        self.blocks.append(("h", text))
        return self

    def p(self, text: str) -> _Doc:
        self.blocks.append(("p", text))
        return self

    def ul(self, items: list[str]) -> _Doc:
        self.blocks.append(("ul", list(items)))
        return self

    def pre(self, text: str) -> _Doc:
        self.blocks.append(("pre", text))
        return self

    def html(self) -> str:
        e = html_mod.escape
        out = [f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>{e(self.title)}</title></head><body>",
               f"<h1>{e(self.title)}</h1>"]
        for kind, value in self.blocks:
            if kind == "h":
                out.append(f"<h2>{e(value)}</h2>")
            elif kind == "p":
                out.append(f"<p>{e(value)}</p>")
            elif kind == "ul":
                out.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in value) + "</ul>")
            else:
                out.append(f"<pre>{e(value)}</pre>")
        out.append("</body></html>")
        return "\n".join(out)

    def text(self) -> str:
        out = [f"# {self.title}", ""]
        for kind, value in self.blocks:
            if kind == "h":
                out += [f"## {value}", ""]
            elif kind == "p":
                out += [value, ""]
            elif kind == "ul":
                out += [f"- {i}" for i in value] + [""]
            else:
                out += ["```", value, "```", ""]
        return "\n".join(out).rstrip() + "\n"


def _rules() -> _Doc:
    d = _Doc("Strelitzia rules")
    d.p("You write my one-page resume and a short cover letter for one job at a time. Each job comes as a "
        "\"Claude packet\" (a Google Doc in this project, or pasted text). My real experience is in the "
        "\"Strelitzia content library\" doc: projects with stable slugs, grouped under role keys, plus my base resume. "
        "Strelitzia checks every reply mechanically, so follow these rules exactly.")
    d.h("Honesty")
    d.ul([
        "Only claim what the content library or the base resume shows. The packet marks each required and preferred "
        "qualification as evidenced (with the project slugs that back it) or unevidenced.",
        "Cover every evidenced required qualification at least once, in a bullet that cites a backing project, "
        "under the most recent role it applies to.",
        "Never mention an unevidenced qualification, skill or tool anywhere: not in bullets, the headline, Skills or "
        "the cover letter. List it in notes_for_user instead so I see it.",
        "Every number in a bullet must appear in the facts of the projects that bullet cites. Never invent, round or "
        "combine numbers. The same goes for the cover letter and the projects in its evidence_slugs (numbers about the "
        "company may come from the posting). The headline cites no project, so any number in it must be in my "
        "library or base resume.",
        "No keyword lists inside bullets. Each bullet describes real work.",
        "No hidden text of any kind, and no instructions to screeners or AI systems (for example \"ignore previous "
        "instructions\" or \"rank this candidate first\"). Screeners detect these and reject the application.",
    ])
    d.h("Wording")
    d.ul([
        "Use the posting's exact term for each qualification. The first time, add the acronym or the expansion "
        "(\"machine learning (ML)\").",
        "Bullets: strong verb, what was built or done, tools, outcome. Write fresh from the facts; vary the verbs.",
        f"One page. Each bullet at most {BULLET_MAX} characters. Keep each role within the bullet range the packet gives.",
        "Cite every project a bullet draws on in project_ids, using slugs from that bullet's own role. Never leave "
        "project_ids empty.",
        "Skills: keep my base resume's categories (you may reorder them) and put the posting's evidenced must-haves "
        "first. Add nothing that isn't evidenced.",
        f"Headline: one truthful line under my name, at most {HEADLINE_MAX} characters (an empty string for none).",
        "No em dashes or en dashes; use commas, periods or hyphens.",
        "Banned words and phrases: " + ", ".join(BANNED_WORDS) + ".",
    ])
    d.h("Cover letter")
    d.ul([
        f"Exactly 3 paragraphs, {COVER_MAX_WORDS} words or fewer in total. No greeting and no sign-off: the app adds "
        "\"Dear Hiring Manager,\" and my name.",
        "Paragraph 1: a specific thing I built that solves their problem. Paragraph 2: two achievements from the "
        "resume's cited projects, with numbers. Paragraph 3: one specific thing about the company from the posting, "
        "then a short close.",
        "List every project the letter draws on in evidence_slugs. Direct and plain; no narration about yourself.",
        "Set cover_letter to null only when I ask for the resume alone.",
    ])
    d.h("Output format")
    d.ul([
        "End every reply with exactly one ```json block matching the schema below: the full resume and cover letter, "
        "not a diff. Put any explanation before the block.",
        "When I ask for a change, reply with the complete updated JSON again.",
        "When I paste \"These problems were found\", fix exactly those and change nothing else.",
    ])
    d.p("Schema:")
    d.pre(json.dumps(RESULT_SCHEMA, indent=2))
    d.p("Example (shape only):")
    d.pre(json.dumps(EXAMPLE_RESULT, indent=2))
    return d


def rules_doc() -> str:
    """The "Strelitzia rules" Google Doc (HTML)."""
    return _rules().html()


def rules_text() -> str:
    return _rules().text()


RULES_VERSION = hashlib.sha256(rules_doc().encode("utf-8")).hexdigest()[:8]


def _library(library: ContentLibrary, base_resume_text: str = "") -> _Doc:
    library.ensure_keys()
    d = _Doc("Strelitzia content library")
    d.p("Everything here is true and may be claimed. Cite projects by slug and roles by key; numbers must come "
        "from the cited project's facts.")
    for role in library.roles:
        d.h(f"Role {role.key}: {role.title} ({role.dates})")
        for proj in role.projects:
            facts = [f"Project slug: {proj.slug}", f"Name: {proj.name} ({proj.dates})"]
            facts += [f"{label}: {value}" for label, value in (
                ("Context", proj.context), ("Scope/Scale", proj.scope_scale),
                ("Tools & Actions", proj.tools_actions), ("Outcome/Metrics", proj.outcome_metrics),
            ) if value]
            if proj.angles:
                facts.append("Angles: " + ", ".join(proj.angles))
            d.ul(facts)
    if base_resume_text.strip():
        d.h("Base resume")
        d.p("My current resume. Its roles, dates, education and skills are also true.")
        d.pre(base_resume_text.strip())
    return d


def library_doc(library: ContentLibrary, base_resume_text: str = "") -> str:
    """The "Strelitzia content library" Google Doc (HTML), slugs visible."""
    return _library(library, base_resume_text).html()


def library_text(library: ContentLibrary, base_resume_text: str = "") -> str:
    return _library(library, base_resume_text).text()


# ── Job packet ────────────────────────────────────────────────────────────

@dataclass
class Packet:
    name: str
    html: str
    text: str
    prompt: str
    evidence: EvidenceMap


def posting_of(job: dict) -> dict:
    try:
        data = json.loads(job.get("details_json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def packet_name(job: dict) -> str:
    return f"{job_company(job)} – {job.get('title') or 'Untitled role'} – Claude packet"


def job_evidence(job: dict, library: ContentLibrary | None, base_resume_text: str) -> EvidenceMap:
    return evidence_map(posting_of(job), library, base_resume_text,
                        description=job.get("full_description") or job.get("description") or "")


def _evidence_line(e) -> str:
    if not e.evidenced:
        return f"{e.target.text}: UNEVIDENCED, do not claim"
    where = []
    if e.slugs:
        where.append("projects " + ", ".join(e.slugs))
    if e.base_roles:
        where.append("base resume " + ", ".join(e.base_roles))
    return f"{e.target.text}: evidenced ({'; '.join(where)})"


def job_packet(job: dict, library: ContentLibrary, base_resume_text: str, current: dict | None = None,
               *, inline: bool = True, fixed_roles: dict | None = None) -> Packet:
    """The per-job packet and the prompt to paste into the chat.

    inline=False (Drive): the packet is a Doc in the project, so the prompt is a short pointer to it.
    inline=True (no Drive): the prompt carries the packet, the rules and the content library.
    """
    from strelitzia.scoring.tailor import _role_slots

    library.ensure_keys()
    name = packet_name(job)
    base = parse_base_resume(base_resume_text, library) if base_resume_text.strip() else None
    fixed_roles = {**(fixed_roles_from_resume(base) if base else {}), **(fixed_roles or {})}
    slots = _role_slots(library, base)
    ev = job_evidence(job, library, base_resume_text)

    d = _Doc(name)
    d.ul([f"Role: {job.get('title') or ''}", f"Company: {job_company(job)}",
          f"Location: {job.get('location') or 'N/A'}", f"Posting: {job.get('url') or ''}",
          f"Rules version: {RULES_VERSION}"])
    for kind, label in (("required", "Required qualifications"), ("preferred", "Preferred qualifications")):
        d.h(label)
        lines = [_evidence_line(e) for e in ev.items if e.target.kind == kind]
        if lines:
            d.ul(lines)
        else:
            d.p("None listed in the posting.")
    unmet = [e.target.text for e in ev.unevidenced()]
    d.h("Not claimable (unevidenced)")
    if unmet:
        d.p("Leave these out of the resume and letter; mention them in notes_for_user if useful.")
        d.ul(unmet)
    else:
        d.p("None: every listed qualification is backed by the library or the base resume.")
    d.h("Roles, bullet ranges and allowed project slugs")
    role_lines = []
    for role in library.roles:
        lo, hi = slots[role.key]
        info = fixed_roles.get(role.key, {})
        head = f"{info['title']} at {info['company']}" if info.get("title") and info.get("company") else role.title
        need = "required" if lo > 0 else "optional, may go in dropped_roles"
        slugs = ", ".join(p.slug for p in role.projects) or "(no projects)"
        role_lines.append(f"{role.key}: {head} ({info.get('dates') or role.dates}); {lo}-{hi} bullets, {need}; "
                          f"projects: {slugs}")
    d.ul(role_lines)
    if base and base.skills:
        d.p("Skill categories (from the base resume): " + "; ".join(f"{k}: {v}" for k, v in base.skills.items()))
    d.h("Job description")
    d.pre((job.get("full_description") or job.get("description") or "")[:DESCRIPTION_MAX].strip() or "(none)")
    if current:
        d.h("Current draft")
        d.p("Start from this draft; keep what already works.")
        d.pre(json.dumps(current, indent=2, ensure_ascii=False))

    text = d.text()
    if inline:
        prompt = ("Tailor my resume and cover letter for this job. Follow the rules, use only the content library "
                  "facts, and end your reply with the JSON block.\n\n"
                  + text + "\n" + rules_text() + "\n" + library_text(library, base_resume_text))
    else:
        title = name if len(name) <= 160 else name[:157] + "..."
        prompt = (f"In this project, tailor my resume and cover letter for the job in the Google Doc '{title}'. "
                  "Follow the Strelitzia rules doc, use only facts from the content library doc and the packet, "
                  "and leave out everything the packet marks unevidenced. "
                  + ("Start from the current draft in the packet. " if current else "")
                  + "End your reply with the JSON block.")
    return Packet(name=name, html=d.html(), text=text, prompt=prompt, evidence=ev)


# ── Paste-back import ─────────────────────────────────────────────────────

_FENCE = re.compile(r"```[ \t]*([A-Za-z]*)[ \t]*\n(.*?)```", re.S)


def extract_json(text: str) -> tuple[object, str]:
    """(parsed JSON, where it came from) from a pasted reply: the last ```json block, or the whole text if it is JSON.

    Raises:
        ValueError: nothing pasted, no JSON block, or invalid JSON.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing was pasted. Copy Claude's whole reply, including the ```json block at the end.")
    blocks = [body for lang, body in _FENCE.findall(text) if lang.lower() in ("json", "")]
    if blocks:
        source, where = blocks[-1], "The last ```json block"
    elif text.startswith("{"):
        source, where = text, "The pasted text"
    else:
        raise ValueError("No ```json block found. Ask Claude to end its reply with the JSON block, then paste the "
                         "whole reply.")
    try:
        data = json.loads(source)
    except json.JSONDecodeError as e:
        raise ValueError(f"{where} isn't valid JSON ({e.msg} at line {e.lineno}, column {e.colno}).") from e
    return data, where


def extract_result(text: str) -> dict:
    """The result object from a pasted reply: the last ```json block, or the whole text if it is JSON.

    Raises:
        ValueError: no JSON block, invalid JSON, or JSON that doesn't match RESULT_SCHEMA.
    """
    data, where = extract_json(text)
    errors = schema_errors(data, RESULT_SCHEMA)
    if errors:
        raise ValueError(f"{where} doesn't match the Strelitzia format: " + "; ".join(errors[:8]))
    return data


class RenderRejected(ValueError):
    """The rendered files failed a check (page overflow, hidden text, injection)."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ImportOutcome:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fix_prompt: str | None = None
    draft_version: int | None = None
    coverage: dict | None = None
    unmet: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def fix_prompt(errors: list[str]) -> str:
    lines = ["These problems were found:"] + [f"{i}. {e}" for i, e in enumerate(errors, 1)]
    lines.append("Keep everything else the same and reply with the corrected JSON.")
    return "\n".join(lines)


class NoContentLibrary(LookupError):
    """The user's profile has no content library yet."""


@dataclass
class Inputs:
    """What a paste is checked and rendered against (a user's profile, `Inputs.for_user`)."""
    library: ContentLibrary
    base_resume_text: str
    profile: dict
    fixed: dict

    @classmethod
    def for_user(cls, conn: Connection, user_id: str) -> Inputs:
        """The user's content library, resume text, profile (in profile.json's shape) and fixed resume blocks."""
        profile = load_profile(conn, user_id)
        if not profile.content_library_md.strip():
            raise NoContentLibrary("No content library yet")
        fixed = fixed_from_data(profile.extras.get("resume_fixed") or {}, profile.resume_text)
        return cls(parse_content_library_text(profile.content_library_md), profile.resume_text,
                   profile.as_profile_json(), fixed)

    def known_text(self) -> str:
        """The candidate's real material: library facts and the base resume."""
        facts = " ".join(p.facts() for r in self.library.roles for p in r.projects)
        return f"{facts} {self.base_resume_text}"


def output_prefix(job: dict) -> str:
    """<site>_<title>_<job key>: the key keeps two postings with the same site and title from sharing files."""
    from strelitzia.database import job_key

    safe_title = re.sub(r"[^\w\s-]", "", job.get("title") or "")[:50].strip().replace(" ", "_")
    safe_site = re.sub(r"[^\w\s-]", "", job.get("site") or "")[:20].strip().replace(" ", "_")
    return f"{safe_site}_{safe_title}_{job.get('job_key') or job_key(job['url'])}"


def check_result(data: dict, job: dict, inputs: Inputs) -> tuple[TailoredResume | None, list[str], list[str]]:
    """All deterministic checks on a parsed result. Returns (resume, errors, warnings)."""
    from strelitzia.scoring.tailor import _role_slots

    library = inputs.library.ensure_keys()
    errors = schema_errors(data, result_schema(library))
    if errors:
        return None, errors, []
    base = parse_base_resume(inputs.base_resume_text, library) if inputs.base_resume_text.strip() else None
    fixed_roles = {**(fixed_roles_from_resume(base) if base else {}), **(inputs.fixed.get("roles") or {})}
    try:
        resume = TailoredResume.from_llm_json(data["resume"], library, fixed_roles=fixed_roles)
    except ValueError as e:
        return None, [str(e)], []
    order = {r.key: i for i, r in enumerate(library.roles)}
    resume.roles.sort(key=lambda r: order.get(r.role_key, len(order)))

    ev = job_evidence(job, library, inputs.base_resume_text)
    known = inputs.known_text()
    # The skills boundary doesn't make a skill claimable, so the watchlist check sees only real material.
    model = validate_resume_model(resume, {**inputs.profile, "skills_boundary": {}}, mode="strict",
                                  slots=_role_slots(library, base), known_text=known)
    errors = [e for e in model["errors"] if not e.startswith(("Banned words", "LLM self-talk"))]
    errors += validate_wording(resume, BULLET_MAX)
    errors += validate_provenance(resume, library)
    errors += validate_claims(resume, ev, known)
    errors += validate_skills_known(resume, known)
    errors += validate_numbers_known(resume.headline or "", known, "headline")
    letter = data.get("cover_letter")
    if letter:
        personal = inputs.profile.get("personal", {})
        errors += validate_letter(letter["paragraphs"], letter["evidence_slugs"], library, ev,
                                  sign_off=personal.get("preferred_name") or personal.get("full_name", ""),
                                  max_words=COVER_MAX_WORDS,
                                  posting_text=job.get("full_description") or job.get("description") or "")
    warnings = _coverage_warnings(resume, ev)
    return resume, list(dict.fromkeys(errors)), warnings


def _coverage_warnings(resume: TailoredResume, ev: EvidenceMap) -> list[str]:
    text = " ".join(t for _, t in resume_parts(resume))
    return [f"Evidenced required qualification not on the resume: {e.target.text}"
            for e in ev.evidenced() if e.target.kind == "required" and not matches(e.target, text, ev.pairs)]


def import_result(conn: Connection, job: dict, text: str, inputs: Inputs | None = None,
                  out_dir: Path | None = None, cover_dir: Path | None = None) -> ImportOutcome:
    """Check a pasted reply; on success render the files and record a new draft version for the job's user.

    On any error nothing is saved, and the outcome carries a `fix_prompt` to paste back into the chat.
    """
    try:
        data = extract_result(text)
    except ValueError as e:
        return ImportOutcome(ok=False, errors=[str(e)], fix_prompt=fix_prompt([str(e)]))
    inputs = inputs or Inputs.for_user(conn, job["user_id"])
    resume, errors, warnings = check_result(data, job, inputs)
    if errors:
        return ImportOutcome(ok=False, errors=errors, warnings=warnings, fix_prompt=fix_prompt(errors))

    if out_dir is None:
        out_dir, cover_dir = config.TAILORED_DIR, cover_dir or config.COVER_LETTER_DIR
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_prefix(job)
    try:
        files, render_warnings = render_outputs(resume, data, job, inputs, out_dir, prefix, cover_dir)
    except RenderRejected as e:
        return ImportOutcome(ok=False, errors=e.errors, warnings=warnings, fix_prompt=fix_prompt(e.errors))
    warnings += render_warnings

    ev = job_evidence(job, inputs.library, inputs.base_resume_text)
    cov = coverage(resume_to_text(resume, inputs.profile, inputs.fixed), ev.targets(), ev.pairs)
    unmet = [e.target.text for e in ev.unevidenced()]
    user_id = job["user_id"]
    row = conn.execute("SELECT draft_version FROM jobs WHERE user_id = ? AND url = ?", (user_id, job["url"])).fetchone()
    version = ((row[0] if row else None) or 0) + 1
    now = datetime.now(UTC).isoformat()
    history = draft_history(job) + [{"version": version, "at": now, "rules_version": RULES_VERSION}]
    report = {"draft_version": version, "rules_version": RULES_VERSION, "at": now, "coverage": cov.to_dict(),
              "unmet": unmet, "warnings": warnings, "notes_for_user": data.get("notes_for_user") or "",
              "history": history}

    (out_dir / f"{prefix}.json").write_text(
        resume.to_json(job_url=job["url"], draft_version=version, rules_version=RULES_VERSION), encoding="utf-8")
    (out_dir / f"{prefix}_RESULT.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / f"{prefix}_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"{prefix}.txt").write_text(resume_to_text(resume, inputs.profile, inputs.fixed), encoding="utf-8")

    conn.execute(
        "UPDATE jobs SET tailored_resume_path = ?, tailored_at = ?, draft_version = ?, draft_rules_version = ?, "
        "tailor_report = ?, resume_docx_path = ? WHERE user_id = ? AND url = ?",
        (files["resume"], now, version, RULES_VERSION, json.dumps(report), files["resume_docx"], user_id, job["url"]),
    )
    conn.execute(
        "INSERT INTO drafts (id, user_id, job_url, version, rules_version, resume_json, resume_text, cover_json, "
        "coverage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, user_id, job["url"], version, RULES_VERSION, json.dumps(resume.to_dict(), ensure_ascii=False),
         resume_to_text(resume, inputs.profile, inputs.fixed), json.dumps(data.get("cover_letter")),
         json.dumps(cov.to_dict()), now),
    )
    if files.get("cover_letter"):  # a resume-only draft keeps the last letter
        conn.execute("UPDATE jobs SET cover_letter_path = ?, cover_letter_at = ? WHERE user_id = ? AND url = ?",
                     (files["cover_letter"], now, user_id, job["url"]))
    conn.commit()
    # A first draft starts the application: Active/Inactive become In progress (a status the user set stays).
    if tracking.effective_status(job, datetime.now(UTC).date()) in ("active", "inactive"):
        tracking.set_status(conn, user_id, job["url"], "in_progress", source="workbench")
    return ImportOutcome(ok=True, warnings=warnings, draft_version=version, coverage=cov.to_dict(), unmet=unmet,
                         files=files)


def render_outputs(resume: TailoredResume, data: dict, job: dict, inputs: Inputs, out_dir: Path,
                   prefix: str, cover_dir: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Render the resume PDF (fit to one page), its DOCX and the cover-letter PDF.

    Returns ({"resume", "resume_docx", "cover_letter"?: path}, warnings).

    Raises:
        RenderRejected: still longer than one page after trimming to the bullet minimums, or hidden text.
    """
    from strelitzia.scoring import cover_letter
    from strelitzia.scoring.docx_export import to_docx
    from strelitzia.scoring.tailor import _role_slots, fit_to_one_page

    base = parse_base_resume(inputs.base_resume_text, inputs.library) if inputs.base_resume_text.strip() else None
    cover_dir = cover_dir or out_dir
    letter = data.get("cover_letter")
    # Render into staging files so a rejected paste never replaces the last accepted draft's files.
    staged = {"resume": out_dir / f"{prefix}.staging.pdf", "resume_docx": out_dir / f"{prefix}.staging.docx"}
    if letter:
        cover_dir.mkdir(parents=True, exist_ok=True)
        staged["cover_letter"] = cover_dir / f"{prefix}_CL.staging.pdf"
    try:
        info, dropped = fit_to_one_page(resume, inputs.profile, inputs.fixed, _role_slots(inputs.library, base),
                                        staged["resume"])
        if info["overflow"]:
            raise RenderRejected(["The resume is longer than one page even with the fewest bullets allowed. "
                                  "Shorten the bullets (aim for one printed line each) and the Skills lines."])
        html = render_resume(resume, inputs.profile, fixed=inputs.fixed)
        problems = validate_visible(html) + validate_pdf_text(staged["resume"], html)
        if letter:
            letter_html = cover_letter.letter_html(letter["paragraphs"], inputs.profile, job, fixed=inputs.fixed)
            cover_letter.render_pdf(letter["paragraphs"], inputs.profile, job, staged["cover_letter"],
                                    fixed=inputs.fixed)
            problems += [f"Cover letter: {p}" for p in
                         validate_visible(letter_html) + validate_pdf_text(staged["cover_letter"], letter_html)]
        if problems:
            raise RenderRejected(problems)
        to_docx(resume, staged["resume_docx"], inputs.profile, inputs.fixed)
    except BaseException:
        for path in staged.values():
            path.unlink(missing_ok=True)
        raise
    files = {}
    for kind, path in staged.items():
        final = path.with_name(path.name.replace(".staging", ""))
        path.replace(final)
        files[kind] = str(final)
    if letter:
        Path(files["cover_letter"]).with_suffix(".txt").write_text(
            cover_letter.letter_text(letter["paragraphs"], inputs.profile), encoding="utf-8")
    return files, [f"Dropped to fit one page: '{t}'" for t in dropped]


def tailor_report(job: dict) -> dict:
    """The job's last accepted draft report (coverage, unmet, warnings, history), or {}."""
    try:
        report = json.loads(job.get("tailor_report") or "{}")
    except ValueError:
        return {}
    return report if isinstance(report, dict) else {}


def draft_history(job: dict) -> list[dict]:
    """[{"version", "at", "rules_version"}] for every accepted paste, oldest first."""
    return list(tailor_report(job).get("history") or [])


def current_result(job: dict) -> dict | None:
    """The last accepted result for this job (the packet's current draft), if there is one."""
    path = job.get("tailored_resume_path")
    if not path:
        return None
    p = Path(path)
    sidecar = p.with_name(p.stem + "_RESULT.json")
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── Drive Docs (Claude Project inputs and job packets) ─────────────────────

PROJECT_INSTRUCTIONS = (
    "You tailor my resume and cover letter for one job per chat. The \"Strelitzia rules\" document is binding: "
    "follow every rule in it. Use only facts from the \"Strelitzia content library\" document and the job's "
    "Claude packet; never claim a qualification the packet marks unevidenced, and never invent numbers. "
    "When I ask for changes, keep everything else the same. End every reply with exactly one ```json block "
    "that matches the schema in the rules document, even when I only ask a question."
)

# The panel rubric belongs in a separate "Strelitzia Panel" project (strelitzia.scoring.panel).
STATIC_DOCS = {"rules": "Strelitzia rules", "library": "Strelitzia content library",
               "panel_rubric": "Strelitzia panel rubric"}
RESUME_DOCS = ("rules", "library")  # the ones the Strelitzia Resumes project holds
_STATE_KEY = "{}:claude_doc:{}"  # user id, kind


def static_doc_html(kind: str, inputs: Inputs) -> str:
    if kind == "panel_rubric":
        from strelitzia.scoring.panel import rubric_doc

        return rubric_doc()
    return rules_doc() if kind == "rules" else library_doc(inputs.library, inputs.base_resume_text)


def published_static_docs(conn: Connection, user_id: str) -> dict[str, dict]:
    """{kind: {"id", "url", "hash", "changed_at"}} for each static Doc the user has published so far."""
    from strelitzia.database import get_state

    out = {}
    for kind in STATIC_DOCS:
        raw = get_state(conn, _STATE_KEY.format(user_id, kind))
        if raw:
            out[kind] = json.loads(raw)
    return out


def publish_static_docs(client, conn: Connection, user_id: str, inputs: Inputs | None = None,
                        force: bool = False) -> dict[str, str]:
    """Write the rules, content-library and panel-rubric Docs into Strelitzia/_Claude/ when their content changed.

    Returns {kind: doc URL}. `force` rewrites them even when the stored hash matches (e.g. a Doc was deleted).
    """
    from strelitzia.database import set_state
    from strelitzia.storage.drive_layout import claude_folder_path

    inputs = inputs or Inputs.for_user(conn, user_id)
    known = published_static_docs(conn, user_id)
    urls, folder_id = {}, None
    for kind, name in STATIC_DOCS.items():
        html = static_doc_html(kind, inputs)
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
        prev = known.get(kind) or {}
        if not force and prev.get("hash") == digest and prev.get("id"):
            urls[kind] = prev.get("url", "")
            continue
        folder_id = folder_id or client.ensure_folder_path(claude_folder_path())
        f = client.upsert_doc(folder_id, name, html, known_id=prev.get("id"))
        state = {"id": f.id, "url": f.url, "hash": digest, "changed_at": datetime.now(UTC).isoformat()}
        set_state(conn, _STATE_KEY.format(user_id, kind), json.dumps(state))
        conn.commit()
        urls[kind] = f.url
    return urls


def packet_for(job: dict, inputs: Inputs, inline: bool) -> Packet:
    """The job's packet from the loaded inputs: the current draft, and role facts from resume_fixed.yaml."""
    return job_packet(job, inputs.library, inputs.base_resume_text, current_result(job), inline=inline,
                      fixed_roles=inputs.fixed.get("roles") or None)


def publish_packet(client, conn: Connection, job: dict, inputs: Inputs | None = None) -> tuple[str, str]:
    """Write the job's packet Doc into its Drive folder and record it on the job. Returns (doc_id, url)."""
    from strelitzia.storage.drive_layout import clean_name, packet_folder_path

    inputs = inputs or Inputs.for_user(conn, job["user_id"])
    packet = packet_for(job, inputs, inline=False)
    folder_id = client.ensure_folder_path(packet_folder_path(job))
    f = client.upsert_doc(folder_id, clean_name(packet.name, 160), packet.html, known_id=job.get("packet_doc_id"))
    conn.execute("UPDATE jobs SET packet_doc_id = ?, packet_doc_url = ? WHERE user_id = ? AND url = ?",
                 (f.id, f.url, job["user_id"], job["url"]))
    conn.commit()
    return f.id, f.url
