"""Structured tailored-resume model, shaped like the user's own resume.

The LLM fills only the variable parts (bullets per role, which roles appear, skill
ordering). Every bullet cites the content-library projects its facts came from, so
claims can be traced and checked mechanically. Header and education are never part
of this model: code supplies them at render time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from applypilot.scoring.content_library import ContentLibrary


@dataclass
class Bullet:
    text: str
    project_ids: list[str] = field(default_factory=list)  # content-library project slugs


@dataclass
class RoleEntry:
    role_key: str  # matches a content-library RoleSection.key
    title: str
    company: str
    dates: str
    tagline: str | None
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class TailoredResume:
    roles: list[RoleEntry] = field(default_factory=list)
    skills: dict[str, str] = field(default_factory=dict)  # ordered category -> items
    dropped_roles: list[str] = field(default_factory=list)

    @classmethod
    def from_llm_json(
        cls,
        data: dict,
        library: ContentLibrary,
        fixed_roles: dict[str, dict] | None = None,
    ) -> TailoredResume:
        """Build and validate a TailoredResume from the LLM's JSON.

        Args:
            data: Parsed JSON: {"roles": [{"role_key", "bullets": [{"text", "project_ids"}]}],
                "skills": [{"category", "items"}] or {category: items}, "dropped_roles": [...]}.
            library: The content library the bullets must cite.
            fixed_roles: Optional role_key -> {"title", "company", "dates", "tagline"} from the
                base resume. When given, these override whatever the JSON says, so the LLM
                can't change titles or dates.

        Raises:
            ValueError: unknown role_key, unknown project id, or a project cited under a role
                it doesn't belong to.
        """
        if not isinstance(data, dict):
            raise ValueError("resume JSON must be an object")  # noqa: TRY004 — ValueError = retryable validation
        fixed_roles = fixed_roles or {}

        roles: list[RoleEntry] = []
        for raw in data.get("roles") or []:
            key = (raw or {}).get("role_key", "")
            section = library.role_by_key(key)
            if section is None:
                known = ", ".join(r.key for r in library.roles)
                raise ValueError(f"unknown role_key {key!r} (known: {known})")
            own = {p.slug for p in section.projects}

            bullets: list[Bullet] = []
            for b in raw.get("bullets") or []:
                if isinstance(b, str):
                    b = {"text": b, "project_ids": []}
                text = str(b.get("text", "")).strip()
                if not text:
                    continue
                ids = [str(i) for i in (b.get("project_ids") or [])]
                for pid in ids:
                    if library.project_by_slug(pid) is None:
                        raise ValueError(f"unknown project id {pid!r} in role {key!r}")
                    if pid not in own:
                        raise ValueError(f"project {pid!r} does not belong to role {key!r}")
                bullets.append(Bullet(text=text, project_ids=ids))

            info = {**raw, **fixed_roles.get(key, {})}
            roles.append(RoleEntry(
                role_key=key,
                title=info.get("title") or section.title.split(",")[0].strip(),
                company=info.get("company") or "",
                dates=info.get("dates") or section.dates,
                tagline=info.get("tagline") or None,
                bullets=bullets,
            ))

        skills_raw = data.get("skills") or {}
        if isinstance(skills_raw, list):
            skills = {
                str(s.get("category", "")).strip(): str(s.get("items", "")).strip()
                for s in skills_raw
                if isinstance(s, dict) and s.get("category")
            }
        else:
            skills = {str(k).strip(): str(v).strip() for k, v in skills_raw.items()}

        dropped = [str(k) for k in data.get("dropped_roles") or []]
        for key in dropped:
            if library.role_by_key(key) is None:
                raise ValueError(f"unknown role_key {key!r} in dropped_roles")

        return cls(roles=roles, skills=skills, dropped_roles=dropped)

    def to_dict(self) -> dict:
        """Plain-dict form in the same shape from_llm_json reads (skills as an ordered list)."""
        return {
            "roles": [asdict(r) for r in self.roles],
            "skills": [{"category": k, "items": v} for k, v in self.skills.items()],
            "dropped_roles": list(self.dropped_roles),
        }

    def to_json(self, **extra) -> str:
        """JSON for the per-job sidecar file. Extra keys (e.g. job_url) are added at top level."""
        return json.dumps({**self.to_dict(), **extra}, indent=2, ensure_ascii=False)
