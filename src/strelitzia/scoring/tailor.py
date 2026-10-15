"""Resume layout helpers shared by the Claude workbench: bullet ranges per role and the one-page fit loop.

Resumes are written in a Claude Pro chat (strelitzia.scoring.workbench); the API tailor and its judge are gone.
"""

from pathlib import Path

from strelitzia.scoring.content_library import ContentLibrary
from strelitzia.scoring.resume_model import TailoredResume


def _role_slots(
    library: ContentLibrary, base: TailoredResume | None = None,
) -> dict[str, tuple[int, int]]:
    """Bullet range per content-library role, shaped like the base resume.

    The max is the role's bullet count in the base resume; the min is two fewer (at least one
    for the most recent role). A role the base resume doesn't show gets 0-2. Without a base
    resume: most recent role 2-5, others 0-3.
    """
    library.ensure_keys()
    counts = {r.role_key: len(r.bullets) for r in base.roles} if base else {}
    slots: dict[str, tuple[int, int]] = {}
    for i, role in enumerate(library.roles):
        if not counts:
            slots[role.key] = (2, 5) if i == 0 else (0, 3)
        elif role.key in counts:
            hi = max(counts[role.key], 1)
            slots[role.key] = (max(1 if i == 0 else 0, hi - 2), hi)
        else:
            slots[role.key] = (0, 2)
    return slots


def fit_to_one_page(
    resume: TailoredResume,
    profile: dict,
    fixed: dict,
    slots: dict[str, tuple[int, int]],
    pdf_path: Path,
    max_iterations: int = 4,
) -> tuple[dict, list[str]]:
    """Render to PDF; while it overflows one page, drop a bullet and re-render.

    Each round drops the last bullet of the role with the most bullets above its minimum
    (ties go to the later, older role). Never goes below a role's minimum.

    Returns:
        (render_pdf info of the final render, texts of the dropped bullets). info["overflow"]
        is still True if the resume couldn't be made to fit.
    """
    from strelitzia.scoring import pdf as pdf_mod
    from strelitzia.scoring.template import render_resume

    dropped: list[str] = []
    info = pdf_mod.render_pdf(render_resume(resume, profile, fixed=fixed), str(pdf_path))
    for _ in range(max_iterations):
        if not info["overflow"]:
            break
        surplus = [
            (len(r.bullets) - slots.get(r.role_key, (0, 0))[0], i)
            for i, r in enumerate(resume.roles)
        ]
        best, idx = max(surplus, default=(0, -1))
        if best <= 0:
            break
        dropped.append(resume.roles[idx].bullets.pop().text)
        info = pdf_mod.render_pdf(render_resume(resume, profile, fixed=fixed), str(pdf_path))
    return info, dropped
