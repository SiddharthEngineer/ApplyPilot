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
from dataclasses import dataclass, field

from strelitzia.database import job_company
from strelitzia.scoring.content_library import ContentLibrary
from strelitzia.scoring.targets import EvidenceMap, evidence_map
from strelitzia.scoring.template import fixed_roles_from_resume, parse_base_resume
from strelitzia.scoring.validator import BANNED_WORDS

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
        "combine numbers. The same goes for the cover letter and the projects in its evidence_slugs.",
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
