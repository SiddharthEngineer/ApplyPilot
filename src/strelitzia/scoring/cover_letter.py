"""Cover letters: rendering the letter Claude writes in the workbench, and the evidence it cites.

The letter's text comes from a pasted Claude reply (strelitzia.scoring.workbench); this module renders it
through its own HTML template to a PDF, and builds the evidence block from a tailored resume's sidecar.
"""

import json
from datetime import datetime
from pathlib import Path

from strelitzia import config
from strelitzia.config import CONTENT_LIBRARY_PATH
from strelitzia.scoring.content_library import parse_content_library


# ── Rendering ────────────────────────────────────────────────────────────

GREETING = "Dear Hiring Manager,"
DEFAULT_TEMPLATE_PATH = config.CONFIG_DIR / "cover_letter_template.default.html"


def sign_off_name(profile: dict) -> str:
    personal = profile.get("personal", {})
    return personal.get("preferred_name") or personal.get("full_name", "")


def letter_text(paragraphs: list[str], profile: dict) -> str:
    """The letter as plain text: greeting, paragraphs, sign-off."""
    body = "\n\n".join(p.strip() for p in paragraphs if p.strip())
    return f"{GREETING}\n\n{body}\n\n{sign_off_name(profile)}\n"


def letter_html(paragraphs: list[str], profile: dict, job: dict, template_path: Path | None = None,
                today: datetime | None = None, fixed: dict | None = None) -> str:
    """The letter through the user's cover-letter template, else the packaged one."""
    from jinja2 import Environment, StrictUndefined

    from strelitzia.database import job_company
    from strelitzia.scoring.template import build_contact, load_fixed

    if template_path is None:
        user = config.COVER_LETTER_TEMPLATE_PATH
        template_path = user if user.exists() else DEFAULT_TEMPLATE_PATH
    header = (fixed if fixed is not None else load_fixed()).get("header") or {}
    today = today or datetime.now()
    env = Environment(autoescape=True, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    return env.from_string(Path(template_path).read_text(encoding="utf-8")).render(
        name=sign_off_name(profile),
        full_name=header.get("name") or profile.get("personal", {}).get("full_name", ""),
        contact=header.get("contact") or build_contact(profile),
        date=f"{today:%B} {today.day}, {today.year}",
        company=job_company(job),
        title=job.get("title") or "",
        greeting=GREETING,
        paragraphs=[p.strip() for p in paragraphs if p.strip()],
    )


def render_pdf(paragraphs: list[str], profile: dict, job: dict, path: Path | str,
               template_path: Path | None = None, fixed: dict | None = None) -> Path:
    """Render the letter to a PDF through its HTML template (Playwright). Returns the PDF path."""
    from strelitzia.scoring import pdf as pdf_mod

    path = Path(path)
    pdf_mod.render_pdf(letter_html(paragraphs, profile, job, template_path, fixed=fixed), str(path))
    return path


# ── Evidence ─────────────────────────────────────────────────────────────

def tailored_evidence(job: dict, library_path: Path | None = None) -> str | None:
    """Evidence block from the job's tailored-resume sidecar (<job>.json), if there is one.

    Lists the selected bullets by role, then the content-library facts of every project they
    cite, so the cover letter cites the same projects as the resume. Returns None when the job
    has no sidecar or the content library is missing.
    """
    path = job.get("tailored_resume_path")
    if not path:
        return None
    sidecar = Path(path).with_suffix(".json")
    library_path = Path(library_path) if library_path else CONTENT_LIBRARY_PATH
    if not sidecar.exists() or not library_path.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    library = parse_content_library(library_path)

    lines = ["SELECTED BULLETS (on the resume sent with this letter):"]
    cited: list[str] = []
    for role in data.get("roles", []):
        head = " at ".join(x for x in (role.get("title"), role.get("company")) if x)
        lines.append(f"{head} ({role.get('dates', '')})")
        for b in role.get("bullets", []):
            lines.append(f"- {b.get('text', '')}")
            cited.extend(pid for pid in b.get("project_ids", []) if pid not in cited)
    lines += ["", "SOURCE PROJECT FACTS:"]
    for pid in cited:
        proj = library.project_by_slug(pid)
        if proj:
            lines.append(f"- {proj.name}: {proj.facts()}")
    return "\n".join(lines)
