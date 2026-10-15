"""Resume and cover letter validation: banned words, fabrication detection, structural checks.

All validation is profile-driven -- no hardcoded personal data. The validator receives
a profile dict (from strelitzia.config.load_profile()) and validates against the user's
actual skills, companies, projects, and school.

Validation modes
----------------
strict  -- banned words = hard errors that trigger retries (original behavior)
normal  -- banned words = warnings only; fabrication/structure = errors (default)
lenient -- banned words ignored; only fabrication and required structure checked
"""

import html as html_mod
import logging
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strelitzia.scoring.content_library import ContentLibrary
    from strelitzia.scoring.resume_model import TailoredResume
    from strelitzia.scoring.targets import EvidenceMap

log = logging.getLogger(__name__)


# ── Universal Constants (not personal data) ───────────────────────────────

BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
    # Cover-letter-specific additions
    "this demonstrates", "this reflects", "i have experience with",
    "furthermore", "additionally", "moreover",
]

LLM_LEAK_PHRASES: list[str] = [
    "i am sorry", "i apologize", "i will try", "let me try",
    "i am at a loss", "i am truly sorry", "apologies for",
    "i keep fabricating", "i will have to admit", "one final attempt",
    "one last time", "if it fails again", "persistent errors",
    "i am having difficulty", "i made an error", "my mistake",
    "here is the corrected", "here is the revised", "here is the updated",
    "here is my", "below is the", "as requested",
    "note:", "disclaimer:", "important:",
    "i have rewritten", "i have removed", "i have fixed",
    "i have replaced", "i have updated", "i have corrected",
    "per your feedback", "based on your feedback", "as per the instructions",
    "the following resume", "the resume below",
    "the following cover letter", "the letter below",
]

# Known fabrication markers: completely unrelated tools/languages.
# Reasonable stretches (K8s, Terraform, Redis, Kafka etc.) are ALLOWED.
FABRICATION_WATCHLIST: set[str] = {
    # Languages with zero relation to the candidate's stack
    "c#", "c++", "golang", "rust", "ruby",
    "kotlin", "swift", "scala", "matlab",
    # Frameworks for wrong languages
    "spring", "django", "rails", "angular", "vue", "svelte",
    # Hard lies: certifications can't be stretched
    "certif", "certified", "pmp", "scrum master", "aws certified",
}

REQUIRED_SECTIONS: set[str] = {"SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}


# ── Helpers ───────────────────────────────────────────────────────────────

def _build_skills_set(profile: dict) -> set[str]:
    """Build the set of allowed skills from the profile's skills_boundary."""
    boundary = profile.get("skills_boundary", {})
    allowed: set[str] = set()
    for category in boundary.values():
        if isinstance(category, list):
            allowed.update(s.lower().strip() for s in category)
        elif isinstance(category, set):
            allowed.update(s.lower().strip() for s in category)
    return allowed


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues instead of rejecting."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")   # em dash -> comma
    text = text.replace("\u2013", "-")    # en dash -> hyphen
    text = text.replace("\u201c", '"').replace("\u201d", '"')   # smart double quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")   # smart single quotes
    return text.strip()


# ── JSON Field Validation ─────────────────────────────────────────────────

def validate_json_fields(
    data: dict, profile: dict, mode: str = "normal", source: str = "resume",
) -> dict:
    """Validate individual JSON fields from an LLM-generated tailored resume.

    Args:
        data:    Parsed JSON from the LLM (title, summary, skills, experience, projects, education).
        profile: User profile dict from load_profile().
        mode:    Validation strictness — "strict", "normal", or "lenient".
                 strict  → banned words are errors (trigger retries)
                 normal  → banned words are warnings (no retry)
                 lenient → banned words ignored entirely
        source:  Content source — "resume" (default) or "content-library".
                 When "content-library", preserved-companies check is relaxed
                 (the LLM may legitimately drop irrelevant roles/companies).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required keys — always checked regardless of mode
    for key in ("title", "summary", "skills", "experience", "projects", "education"):
        if key not in data or not data[key]:
            errors.append(f"Missing required field: {key}")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}

    # Collect all text for bulk checks
    all_text_parts: list[str] = [data["summary"]]

    # Skills: check for fabrication (always enforced)
    if isinstance(data["skills"], dict):
        skills_text = " ".join(str(v) for v in data["skills"].values()).lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_text:
                errors.append(f"Fabricated skill: '{fake}'")

    # Experience: preserved companies must be present (resume mode only)
    # In content-library mode, the LLM may legitimately drop irrelevant roles/companies.
    resume_facts = profile.get("resume_facts", {})
    preserved_companies = resume_facts.get("preserved_companies", [])

    if isinstance(data["experience"], list):
        if source != "content-library":
            for company in preserved_companies:
                has_company = any(
                    company.lower() in str(e.get("header", "")).lower()
                    for e in data["experience"]
                )
                if not has_company:
                    errors.append(f"Company '{company}' missing from experience")
        for entry in data["experience"]:
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

    # Projects: collect bullets
    if isinstance(data["projects"], list):
        for entry in data["projects"]:
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

    # Education: preserved school must be present (always enforced)
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school:
        edu = str(data.get("education", ""))
        if preserved_school.lower() not in edu.lower():
            errors.append(f"Education '{preserved_school}' missing")

    # Bulk text checks
    all_text = " ".join(all_text_parts).lower()

    # LLM self-talk is always an error regardless of mode (indicates broken output)
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in all_text]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # Banned filler words — severity depends on mode
    if mode != "lenient":
        found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", all_text)]
        if found_banned:
            msg = f"Banned words: {', '.join(found_banned[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:  # normal
                warnings.append(msg)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


# ── TailoredResume (template mode) Validation ─────────────────────────────

def validate_resume_model(
    resume: "TailoredResume", profile: dict, mode: str = "normal",
    slots: dict[str, tuple[int, int]] | None = None,
    known_text: str = "",
) -> dict:
    """Layer-1 checks for a template-mode TailoredResume (content-library source).

    Args:
        resume:  The parsed TailoredResume.
        profile: User profile dict.
        mode:    "strict" | "normal" | "lenient" (banned-word severity, as in validate_json_fields).
        slots:   role_key -> (min, max) bullets. A role with min > 0 must appear; counts must be in range.
        known_text: The candidate's real material (base resume skills, library facts). Watchlist
            terms found here or in the profile's skills_boundary are not fabrication (e.g. Vue, Django).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not resume.roles:
        errors.append("No roles in output")
    if not resume.skills:
        errors.append("Missing required field: skills")

    skills_text = " ".join(resume.skills.values()).lower()
    known = known_text.lower() + " " + " ".join(_build_skills_set(profile))
    for fake in FABRICATION_WATCHLIST:
        if len(fake) > 2 and fake not in known and re.search(r"\b" + re.escape(fake) + r"\b", skills_text):
            errors.append(f"Fabricated skill: '{fake}'")

    if slots:
        present = {r.role_key: len(r.bullets) for r in resume.roles}
        for key, (lo, hi) in slots.items():
            n = present.get(key)
            if n is None:
                if lo > 0:
                    errors.append(f"Role '{key}' is required ({lo}-{hi} bullets) but missing")
            elif not lo <= n <= hi:
                errors.append(f"Role '{key}' has {n} bullets; it needs {lo}-{hi}")

    all_text = " ".join(b.text for r in resume.roles for b in r.bullets).lower()
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in all_text]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    if mode != "lenient":
        found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", all_text)]
        if found_banned:
            msg = f"Banned words: {', '.join(found_banned[:5])}"
            (errors if mode == "strict" else warnings).append(msg)

    return {"passed": not errors, "errors": errors, "warnings": warnings}


# Numbers as they appear in prose: 45, 1,200, 0.899, 90%. Not digits inside names (EC2, S3, Q3).
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d[\d,.]*%?")


def _numbers(text: str) -> list[str]:
    """Numeric tokens in text, normalized: no thousands commas, no trailing punctuation or %."""
    out = []
    for tok in _NUMBER.findall(text):
        tok = tok.rstrip("%").rstrip(".,").replace(",", "")
        if tok:
            out.append(tok)
    return out


def _number_in(num: str, facts: str) -> bool:
    """True if num appears in facts as a whole number (so '3' doesn't match '30' or '0.3')."""
    return re.search(rf"(?<![\d.]){re.escape(num)}(?![\d]|\.\d)", facts) is not None


def validate_provenance(resume: "TailoredResume", library: "ContentLibrary") -> list[str]:
    """Check every number in every bullet against the facts of the projects the bullet cites.

    Returns one error string per problem: a bullet citing no projects, or a number (including
    either side of an 'a → b' pair) missing from the cited projects' concatenated facts.
    """
    errors: list[str] = []
    for role in resume.roles:
        for b in role.bullets:
            short = b.text if len(b.text) <= 60 else b.text[:57] + "..."
            if not b.project_ids:
                errors.append(f"Bullet cites no project_ids: '{short}'")
                continue
            projects = [library.project_by_slug(pid) for pid in b.project_ids]
            facts = " ".join(p.facts() for p in projects if p).replace(",", "")
            # Both sides of an 'a → b' pair are ordinary numeric tokens, so each is checked too.
            missing = [n for n in dict.fromkeys(_numbers(b.text)) if not _number_in(n, facts)]
            if missing:
                errors.append(
                    f"Number(s) {', '.join(missing)} not in the facts of {', '.join(b.project_ids)}: '{short}'"
                )
    return errors


# ── Full Resume Text Validation ───────────────────────────────────────────

def validate_tailored_resume(
    text: str, profile: dict, original_text: str = "", source: str = "resume",
) -> dict:
    """Programmatic validation of a tailored resume against the user's profile.

    Args:
        text: The tailored resume text to validate.
        profile: User profile dict from load_profile().
        original_text: The original base resume text (for fabrication comparison).
        source: Content source — "resume" (default) or "content-library".
                When "content-library", preserved-companies and preserved-projects
                checks are relaxed (the LLM may legitimately drop irrelevant items).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    personal = profile.get("personal", {})
    resume_facts = profile.get("resume_facts", {})

    # 1. Check required sections exist (flexible matching)
    section_variants: dict[str, list[str]] = {
        "SUMMARY": ["summary", "professional summary", "profile"],
        "TECHNICAL SKILLS": ["technical skills", "skills", "tech stack", "core skills", "technologies"],
        "EXPERIENCE": ["experience", "work experience", "professional experience"],
        "PROJECTS": ["projects", "personal projects", "key projects", "selected projects"],
        "EDUCATION": ["education", "academic background"],
    }
    for section, variants in section_variants.items():
        if not any(v in text_lower for v in variants):
            errors.append(f"Missing required section: {section} (or variant)")

    # 2. Check name preserved (warn, don't error -- we can inject it)
    full_name = personal.get("full_name", "")
    if full_name and full_name.lower() not in text_lower:
        warnings.append(f"Name '{full_name}' missing -- will be injected")

    # 3. Check companies preserved (resume mode only)
    # In content-library mode, the LLM may legitimately drop irrelevant roles/companies.
    if source != "content-library":
        for company in resume_facts.get("preserved_companies", []):
            if company.lower() not in text_lower:
                errors.append(f"Company '{company}' missing -- cannot remove real experience")

    # 4. Check projects preserved (resume mode only)
    # In content-library mode, projects are selected from the library, not preserved.
    if source != "content-library":
        for project in resume_facts.get("preserved_projects", []):
            if project.lower() not in text_lower:
                warnings.append(f"Project '{project}' not found -- may have been renamed")

    # 5. Check school preserved
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school and preserved_school.lower() not in text_lower:
        errors.append(f"Education '{preserved_school}' missing")

    # 6. Check contact info preserved (warn, don't error -- we can inject)
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    if email and email.lower() not in text_lower:
        warnings.append("Email missing -- will be injected")
    if phone and phone not in text:
        warnings.append("Phone missing -- will be injected")

    # 7. Scan TECHNICAL SKILLS section for fabricated tools
    skills_start = text_lower.find("technical skills")
    skills_end = text_lower.find("experience", skills_start) if skills_start != -1 else -1
    if skills_start != -1 and skills_end != -1:
        skills_block = text_lower[skills_start:skills_end]
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_block:
                errors.append(f"FABRICATED SKILL in Technical Skills: '{fake}'")

    # 8. Scan full document for fabrication watchlist items not in original
    if original_text:
        original_lower = original_text.lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in text_lower and fake not in original_lower:
                warnings.append(f"New tool/skill appeared: '{fake}' (not in original)")

    # 9. Em dashes (should be auto-fixed by sanitize_text, but safety net)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 10. Banned words (word-boundary matching)
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:5])}")

    # 11. LLM self-talk leak detection
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 12. Duplicate section detection
    for section_name in ["summary", "experience", "education", "projects"]:
        count = text_lower.count(f"\n{section_name}\n") + text_lower.count(f"\n{section_name} \n")
        if text_lower.startswith(f"{section_name}\n"):
            count += 1
        if count > 1:
            errors.append(f"Section '{section_name}' appears {count} times.")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── Cover Letter Validation ──────────────────────────────────────────────

def validate_cover_letter(text: str, mode: str = "normal") -> dict:
    """Programmatic validation of a cover letter.

    Args:
        text: The cover letter text to validate.
        mode: Validation strictness — "strict", "normal", or "lenient".
              strict  → banned words are errors (trigger retries); word limit enforced
              normal  → banned words are warnings; word limit is soft (+25 words)
              lenient → banned words ignored; word count not checked

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    # 1. Em dashes — always an error (sanitize_text should have caught these)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 2. Banned words — severity depends on mode
    if mode != "lenient":
        found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
        if found:
            msg = f"Banned words: {', '.join(found[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:  # normal
                warnings.append(msg)

    # 3. Word count
    words = len(text.split())
    if mode == "strict" and words > 250:
        errors.append(f"Too long ({words} words). Max 250.")
    elif mode == "normal" and words > 275:
        warnings.append(f"Long ({words} words). Target 250.")
    # lenient: no word count check

    # 4. LLM self-talk — always an error regardless of mode
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 5. Must start with "Dear" — always checked (preamble should have been stripped)
    stripped = text.strip()
    if not stripped.lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


# ── Claude workbench: checks on a pasted result ──────────────────────────

DASHES = ("—", "–")


def resume_parts(resume: "TailoredResume") -> list[tuple[str, str]]:
    """(where, text) for every piece of a resume that a model wrote: headline, bullets, skills."""
    parts = [("headline", resume.headline)] if resume.headline else []
    for role in resume.roles:
        for i, b in enumerate(role.bullets, 1):
            parts.append((f"{role.role_key} bullet {i}", b.text))
    parts += [(f"Skills ({cat})", items) for cat, items in resume.skills.items()]
    return parts


def validate_claims(resume: "TailoredResume", evidence: "EvidenceMap", known_text: str = "") -> list[str]:
    """Every posting target the resume states (bullets, headline, Skills) must be evidenced.

    Also extends the fabrication watchlist from the Skills section to bullets and the headline: a watchlist
    term the candidate's material (known_text: library facts and base resume) doesn't mention is an error.
    """
    from strelitzia.scoring.targets import matches

    errors: list[str] = []
    parts = resume_parts(resume)
    for e in evidence.unevidenced():
        if not e.target.is_term:
            continue
        where = [w for w, text in parts if matches(e.target, text, evidence.pairs)]
        if where:
            errors.append(f"'{e.target.text}' is not backed by your content library or base resume "
                          f"(found in {', '.join(where[:3])}); remove it")
    known = known_text.lower()
    for fake in sorted(FABRICATION_WATCHLIST):
        if len(fake) <= 2 or fake in known:
            continue
        where = [w for w, text in parts if re.search(r"\b" + re.escape(fake) + r"\b", text.lower())]
        if where:
            errors.append(f"'{fake}' is not in your content library or base resume (found in {', '.join(where[:3])})")
    return errors


def validate_skills_known(resume: "TailoredResume", known_text: str) -> list[str]:
    """Every item in the Skills section must appear in the library facts or the base resume."""
    from strelitzia.scoring.targets import matches

    errors = []
    for cat, items in resume.skills.items():
        for item in split_items(items):
            if not matches(item, known_text):
                errors.append(f"Skill '{item}' ({cat}) is not in your content library or base resume; remove it")
    return errors


def split_items(value: str) -> list[str]:
    """'Airflow, CI/CD (GitHub Actions), AWS (EC2, S3)' -> ['Airflow', 'CI/CD (GitHub Actions)', 'AWS (EC2, S3)']."""
    items, depth, cur = [], 0, ""
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if ch in ",;" and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    items.append(cur.strip())
    return [i for i in items if i]


def validate_wording(resume: "TailoredResume", bullet_max: int = 230) -> list[str]:
    """Dashes, banned words, LLM self-talk and over-long bullets in what the model wrote."""
    errors = []
    for where, text in resume_parts(resume):
        short = text if len(text) <= 60 else text[:57] + "..."
        if any(d in text for d in DASHES):
            errors.append(f"Em or en dash in {where}: '{short}'; use a comma, period or hyphen")
        found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text.lower())]
        if found:
            errors.append(f"Banned words in {where}: {', '.join(found[:5])}")
        leaks = [p for p in LLM_LEAK_PHRASES if p in text.lower()]
        if leaks:
            errors.append(f"Commentary in {where}: '{leaks[0]}'")
        if where.startswith(tuple(r.role_key for r in resume.roles)) and len(text) > bullet_max:
            errors.append(f"{where} is {len(text)} characters; at most {bullet_max}: '{short}'")
    return errors


def validate_letter(paragraphs: list[str], evidence_slugs: list[str], library: "ContentLibrary",
                    evidence: "EvidenceMap | None" = None, sign_off: str = "", max_words: int = 250) -> list[str]:
    """Cover-letter checks on a pasted letter: 3 paragraphs, the usual letter rules (strict), numbers
    backed by the facts of its evidence_slugs, and no unevidenced posting targets."""
    from strelitzia.scoring.targets import matches

    errors: list[str] = []
    paras = [p.strip() for p in paragraphs if p.strip()]
    if len(paras) != 3:
        errors.append(f"The cover letter has {len(paras)} paragraphs; it needs exactly 3")
    body = "\n\n".join(paras)
    words = len(body.split())
    if words > max_words:
        errors.append(f"The cover letter is {words} words; at most {max_words}")
    letter = f"Dear Hiring Manager,\n\n{body}\n\n{sign_off}".strip()
    errors += [f"Cover letter: {e}" for e in validate_cover_letter(letter, mode="strict")["errors"]
               if not e.startswith("Too long")]
    projects = [library.project_by_slug(s) for s in evidence_slugs]
    facts = " ".join(p.facts() for p in projects if p).replace(",", "")
    missing = [n for n in dict.fromkeys(_numbers(body)) if not _number_in(n, facts)]
    if missing:
        cited = ", ".join(evidence_slugs) or "no projects"
        errors.append(f"Cover letter number(s) {', '.join(missing)} are not in the facts of its evidence_slugs ({cited})")
    if evidence is not None:
        for e in evidence.unevidenced():
            if e.target.is_term and matches(e.target, body, evidence.pairs):
                errors.append(f"Cover letter mentions '{e.target.text}', which your content library and base "
                              "resume don't back; remove it")
    return errors


# ── Hidden text and injection (rendered HTML and PDF) ─────────────────────

# Instructions aimed at an AI screener. Screeners flag these and reject the application.
INJECTION_PHRASES: tuple[str, ...] = (
    "ignore previous", "ignore all previous", "ignore the above", "ai screener", "rank this candidate",
    "system prompt", "hire this candidate", "as an ai language model",
)
MIN_FONT_PT = 6.0
MIN_OPACITY = 0.5
MIN_CONTRAST = 1.5  # WCAG contrast ratio below which text is effectively invisible
_NAMED_COLORS = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "gray": (128, 128, 128),
                 "grey": (128, 128, 128), "silver": (192, 192, 192), "whitesmoke": (245, 245, 245),
                 "snow": (255, 250, 250), "ivory": (255, 255, 240), "navy": (0, 0, 128), "blue": (0, 0, 255)}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def _parse_color(value: str) -> tuple[int, int, int] | None:
    v = value.strip().lower()
    if v in _NAMED_COLORS:
        return _NAMED_COLORS[v]
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)", v)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    def lum(c):
        ch = [x / 255 for x in c]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _length_pt(value: str, base_pt: float = 10.0) -> float | None:
    m = re.fullmatch(r"(-?[\d.]+)\s*(pt|px|em|rem|%|in|cm|mm)?", value.strip().lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2) or "px"
    factor = {"pt": 1, "px": 0.75, "em": base_pt, "rem": base_pt, "%": base_pt / 100, "in": 72, "cm": 28.35,
              "mm": 2.835}[unit]
    return n * factor


def _declarations(block: str) -> dict[str, str]:
    out = {}
    for decl in block.split(";"):
        name, sep, value = decl.partition(":")
        if sep:
            out[name.strip().lower()] = value.replace("!important", "").strip()
    return out


def _hiding_reason(decls: dict[str, str], background: tuple[int, int, int]) -> str | None:
    """Why a set of CSS declarations would hide text, or None."""
    if decls.get("display", "").lower() == "none":
        return "display:none hides text"
    if decls.get("visibility", "").lower() in ("hidden", "collapse"):
        return "visibility:hidden hides text"
    try:
        if "opacity" in decls and float(decls["opacity"]) < MIN_OPACITY:
            return f"opacity {decls['opacity']} makes text nearly invisible"
    except ValueError:
        pass
    size = _length_pt(decls["font-size"]) if "font-size" in decls else None
    if size is not None and size < MIN_FONT_PT:
        return f"font-size {decls['font-size']} is below {MIN_FONT_PT:g}pt"
    color = decls.get("color", "").lower()
    if color == "transparent":
        return "transparent text"
    bg_value = decls.get("background-color") or decls.get("background", "").split(" ")[0]
    bg = _parse_color(bg_value) if bg_value else None
    fg = _parse_color(color) if color else None
    if fg and _contrast(fg, bg or background) < MIN_CONTRAST:
        return f"text color {color} is nearly the background color"
    for prop in ("left", "top", "right", "bottom", "text-indent", "margin-left", "margin-top"):
        pt = _length_pt(decls[prop]) if prop in decls else None
        if pt is not None and (pt <= -75 or pt >= 750):
            return f"{prop}: {decls[prop]} moves text off the page"
    return None


def _css_rules(html: str) -> list[tuple[str, dict[str, str]]]:
    rules = []
    for style in re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S | re.I):
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        for selector, block in re.findall(r"([^{}@]+)\{([^{}]*)\}", style):
            rules.append((selector.strip(), _declarations(block)))
    return rules


def _page_background(rules) -> tuple[int, int, int]:
    for selector, decls in rules:
        if {s.strip() for s in selector.split(",")} & {"body", "html"}:
            value = decls.get("background-color") or decls.get("background", "").split(" ")[0]
            if value and _parse_color(value):
                return _parse_color(value)
    return (255, 255, 255)


def _hidden_selectors(rules, background) -> tuple[set[str], set[str], set[str]]:
    """(tags, classes, ids) whose CSS rule hides text, from the last simple selector of each rule."""
    tags, classes, ids = set(), set(), set()
    for selector, decls in rules:
        if not _hiding_reason(decls, background):
            continue
        for sel in selector.split(","):
            last = sel.strip().split()[-1] if sel.strip() else ""
            for kind, name in re.findall(r"([.#]?)([\w-]+)", last):
                (classes if kind == "." else ids if kind == "#" else tags).add(name.lower())
    return tags, classes, ids


def _visible_and_inline(html: str) -> tuple[str, list[str]]:
    """The text a reader sees (hidden elements dropped), and errors for inline hiding."""
    from html.parser import HTMLParser

    rules = _css_rules(html)
    background = _page_background(rules)
    tags, classes, ids = _hidden_selectors(rules, background)
    errors: list[str] = []

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, bool]] = []
            self.text: list[str] = []

        def handle_starttag(self, tag, attrs):
            a = {k: v or "" for k, v in attrs}
            reason = _hiding_reason(_declarations(a.get("style", "")), background) if "style" in a else None
            if reason:
                errors.append(f"Inline style on <{tag}>: {reason}")
            if "hidden" in a or a.get("aria-hidden") == "true":
                reason = reason or "hidden attribute"
                errors.append(f"<{tag}> is hidden by an attribute")
            hidden = bool(reason) or tag in tags or tag in ("style", "script", "head", "title") \
                or bool(set(a.get("class", "").lower().split()) & classes) or a.get("id", "").lower() in ids
            if tag not in _VOID_TAGS:
                parent = self.stack[-1][1] if self.stack else False
                self.stack.append((tag, parent or hidden))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

        def handle_data(self, data):
            if not (self.stack and self.stack[-1][1]):
                self.text.append(data)

    p = P()
    p.feed(html)
    return " ".join(p.text), errors


def visible_text(html: str) -> str:
    return _visible_and_inline(html)[0]


def validate_visible(html: str) -> list[str]:
    """Errors for hidden text (color near the background, < 6pt, display:none, visibility:hidden, opacity
    < 0.5, off-page positioning, the hidden attribute) and for instructions aimed at AI screeners."""
    rules = _css_rules(html)
    background = _page_background(rules)
    errors = []
    for selector, decls in rules:
        if selector.lower().startswith("@"):
            continue
        reason = _hiding_reason(decls, background)
        if reason:
            errors.append(f"CSS rule '{selector}': {reason}")
    text, inline = _visible_and_inline(html)
    errors += inline
    all_text = re.sub(r"<[^>]+>", " ", re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I))
    lowered = " ".join(html_mod.unescape(all_text).lower().split())
    errors += [f"Instruction aimed at screeners: '{p}'" for p in INJECTION_PHRASES if p in lowered]
    return errors


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()))


def validate_pdf_text(pdf_path, html: str) -> list[str]:
    """The PDF's extracted text may contain no words that aren't visible in the HTML it was rendered from."""
    from pypdf import PdfReader

    extracted = " ".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    extra = sorted(_tokens(extracted) - _tokens(visible_text(html)))
    if extra:
        return [f"The PDF contains text that isn't visible: {', '.join(extra[:10])}"]
    return []
