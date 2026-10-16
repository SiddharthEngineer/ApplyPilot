"""A user's profile, resume and content library (table `user_profiles`, created by `database._ensure_tables`).

Scoring reads the resume text, the workbench the content library and the profile. The profile's own fields are what
the Profile page edits; everything else from the old profile.json (`resume_facts`, `work_authorization`, ...) and
resume_fixed.yaml (under "resume_fixed") stays in `extras`, because the resume template and validator use it.
`as_profile_json()` rebuilds the profile.json shape those modules read.

On first start the owner's files in ~/.strelitzia are imported into the owner's row (`import_owner_files`).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime

import yaml

from strelitzia import config, users
from strelitzia.enrichment.posting_model import EDUCATION_LEVELS

log = logging.getLogger(__name__)

# What a user can pick on the Profile page ("unknown" is for postings that don't say).
PROFILE_EDUCATION_LEVELS: tuple[str, ...] = tuple(e for e in EDUCATION_LEVELS if e != "unknown")
# profile.json's free-text education_level, matched in this order.
_EDUCATION_WORDS = (("phd", ("phd", "ph.d", "doctor")), ("masters", ("master", "msc", "m.sc", "mba")),
                    ("bachelors", ("bachelor", "bsc", "b.sc", "b.a", "b.s", "undergrad")),
                    ("associate", ("associate",)), ("high_school", ("high school", "secondary", "ged")),
                    ("none", ("none",)))
PDF_MIME = "application/pdf"


@dataclass
class UserProfile:
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    email: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    location: str = ""
    current_job_title: str = ""
    years_experience: float | None = None  # in the current field
    education_level: str | None = None  # one of PROFILE_EDUCATION_LEVELS
    skills: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)
    # Stored in their own columns, not in data_json.
    content_library_md: str = ""
    resume_text: str = ""
    resume_file_name: str | None = None
    resume_mime: str | None = None
    resume_file: bytes | None = None

    def data(self) -> dict:
        """The fields kept in data_json."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name in _DATA_FIELDS}

    @classmethod
    def from_data(cls, data: dict | None) -> UserProfile:
        data = data if isinstance(data, dict) else {}
        out = cls()
        for name in _DATA_FIELDS:
            if name in data and data[name] is not None:
                setattr(out, name, data[name])
        out.skills = [str(s).strip() for s in out.skills or [] if str(s).strip()] \
            if isinstance(out.skills, list) else []
        out.extras = out.extras if isinstance(out.extras, dict) else {}
        out.years_experience = _number(out.years_experience)
        out.education_level = out.education_level if out.education_level in PROFILE_EDUCATION_LEVELS else None
        return out

    @property
    def full_name(self) -> str:
        return " ".join(x for x in (self.first_name.strip(), self.last_name.strip()) if x)

    def as_profile_json(self) -> dict:
        """The profile in profile.json's shape (`personal`, `experience`, `skills_boundary`, plus the extras)."""
        out = copy.deepcopy({k: v for k, v in self.extras.items() if k != "resume_fixed"})
        personal = out.setdefault("personal", {})
        personal.update(full_name=self.full_name, email=self.email, phone=self.phone,
                        linkedin_url=self.linkedin_url, github_url=self.github_url,
                        city=self.location, province_state="")
        experience = out.setdefault("experience", {})
        years = self.years_experience
        experience.update(
            current_job_title=self.current_job_title,
            years_of_experience_total="" if years is None else f"{years:g}",
            education_level=self.education_level or "",
        )
        out["skills_boundary"] = {"skills": list(self.skills)}
        return out


_COLUMN_FIELDS = ("content_library_md", "resume_text", "resume_file_name", "resume_mime", "resume_file")
_DATA_FIELDS = tuple(f.name for f in fields(UserProfile) if f.name not in _COLUMN_FIELDS)


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group()) if match else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _user(conn, user_id: str | None) -> str:
    return user_id or users.owner(conn).id


def has_profile(conn, user_id: str) -> bool:
    return conn.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone() is not None


def resume_text(conn, user_id: str | None = None) -> str:
    """The user's resume text ("" when there is none yet), without loading the rest of the profile."""
    row = conn.execute("SELECT resume_text FROM user_profiles WHERE user_id = ?", (_user(conn, user_id),)).fetchone()
    return (row[0] or "") if row else ""


def load_profile(conn, user_id: str | None = None) -> UserProfile:
    """The user's profile (the owner's by default), or an empty one."""
    row = conn.execute(
        "SELECT data_json, resume_text, content_library_md, resume_file_name, resume_mime, resume_file "
        "FROM user_profiles WHERE user_id = ?", (_user(conn, user_id),)).fetchone()
    if row is None:
        return UserProfile()
    try:
        data = json.loads(row[0]) if row[0] else {}
    except ValueError:
        data = {}
    out = UserProfile.from_data(data)
    out.resume_text, out.content_library_md = row[1] or "", row[2] or ""
    out.resume_file_name, out.resume_mime = row[3], row[4]
    out.resume_file = bytes(row[5]) if row[5] is not None else None
    return out


def save_profile(conn, user_id: str | None, profile: UserProfile) -> None:
    """Save the profile fields, the resume text and the content library (the uploaded file is left as it is)."""
    conn.execute(
        "INSERT INTO user_profiles (user_id, data_json, resume_text, content_library_md, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET data_json = excluded.data_json, "
        "resume_text = excluded.resume_text, content_library_md = excluded.content_library_md, "
        "updated_at = excluded.updated_at",
        (_user(conn, user_id), json.dumps(profile.data(), ensure_ascii=False), profile.resume_text,
         profile.content_library_md, _now()),
    )
    conn.commit()


def save_resume_file(conn, user_id: str | None, name: str, mime: str, data: bytes, text: str) -> None:
    """Store an uploaded resume file and the text taken from it (which scoring reads)."""
    conn.execute(
        "INSERT INTO user_profiles (user_id, data_json, resume_text, resume_file, resume_file_name, resume_mime, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
        "resume_text = excluded.resume_text, resume_file = excluded.resume_file, "
        "resume_file_name = excluded.resume_file_name, resume_mime = excluded.resume_mime, "
        "updated_at = excluded.updated_at",
        (_user(conn, user_id), json.dumps(UserProfile().data()), text, data, name, mime, _now()),
    )
    conn.commit()


# ── Import of the owner's ~/.strelitzia files ────────────────────────────

def _education(text) -> str | None:
    text = str(text or "").strip().lower()
    if text in PROFILE_EDUCATION_LEVELS:
        return text
    for level, words in _EDUCATION_WORDS:
        if any(w in text for w in words):
            return level
    return None


def from_profile_json(data: dict) -> UserProfile:
    """Map a profile.json document into a UserProfile; the keys that aren't fields are kept in `extras`."""
    extras = copy.deepcopy(data) if isinstance(data, dict) else {}
    personal = extras.get("personal") if isinstance(extras.get("personal"), dict) else {}
    experience = extras.get("experience") if isinstance(extras.get("experience"), dict) else {}
    boundary = extras.pop("skills_boundary", None)
    first, _, last = str(personal.pop("full_name", "") or "").strip().partition(" ")
    location = ", ".join(str(x).strip() for x in (personal.pop("city", ""), personal.pop("province_state", ""))
                         if x and str(x).strip())
    skills: list[str] = []
    for values in (boundary or {}).values() if isinstance(boundary, dict) else []:
        for s in values if isinstance(values, list) else []:
            if str(s).strip() and str(s).strip() not in skills:
                skills.append(str(s).strip())
    return UserProfile(
        first_name=first, last_name=last.strip(),
        phone=str(personal.pop("phone", "") or ""), email=str(personal.pop("email", "") or ""),
        linkedin_url=str(personal.pop("linkedin_url", "") or ""),
        github_url=str(personal.pop("github_url", "") or ""),
        location=location,
        current_job_title=str(experience.pop("current_job_title", "") or ""),
        years_experience=_number(experience.pop("years_of_experience_total", None) or None),
        education_level=_education(experience.pop("education_level", "")),
        skills=skills, extras=extras,
    )


def _read(path) -> str | None:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except OSError as e:
        log.warning("profile import: can't read %s (%s)", path, e)
        return None


def import_owner_files(conn) -> bool:
    """Import profile.json, resume.txt (and resume.pdf), content_library.md and resume_fixed.yaml from the app
    directory into the owner's profile, once: only when the owner has no profile row and at least one file exists.

    Returns whether anything was imported.
    """
    owner_id = users.owner(conn).id
    if has_profile(conn, owner_id):
        return False
    profile_json, resume = _read(config.PROFILE_PATH), _read(config.RESUME_PATH)
    library, fixed = _read(config.CONTENT_LIBRARY_PATH), _read(config.RESUME_FIXED_PATH)
    pdf = config.RESUME_PDF_PATH if config.RESUME_PDF_PATH.exists() else None
    if profile_json is None and resume is None and library is None and fixed is None and pdf is None:
        return False
    try:
        profile = from_profile_json(json.loads(profile_json)) if profile_json else UserProfile()
    except ValueError as e:
        log.warning("profile import: %s isn't valid JSON (%s)", config.PROFILE_PATH, e)
        profile = UserProfile()
    if fixed:
        try:
            profile.extras["resume_fixed"] = yaml.safe_load(fixed) or {}
        except yaml.YAMLError as e:
            log.warning("profile import: %s isn't valid YAML (%s)", config.RESUME_FIXED_PATH, e)
    profile.resume_text, profile.content_library_md = resume or "", library or ""
    save_profile(conn, owner_id, profile)
    if pdf is not None:
        try:
            save_resume_file(conn, owner_id, pdf.name, PDF_MIME, pdf.read_bytes(), profile.resume_text)
        except OSError as e:
            log.warning("profile import: can't read %s (%s)", pdf, e)
    log.info("Imported the owner's profile files from %s into the database.", config.APP_DIR)
    return True

