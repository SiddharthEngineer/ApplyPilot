"""Bullet ranges per role (tailor._role_slots), shaped like the base resume. Used by the workbench packet and checks."""

from strelitzia.scoring.content_library import ContentLibrary, Project, RoleSection
from strelitzia.scoring.resume_model import Bullet, RoleEntry, TailoredResume
from strelitzia.scoring.tailor import _role_slots


def _project(name: str) -> Project:
    return Project(name=name, role_header="## ROLE", dates="2024", context="c", scope_scale="s",
                   tools_actions="t", outcome_metrics="o", angles=["PIPELINE"])


def _library() -> ContentLibrary:
    return ContentLibrary(roles=[
        RoleSection(title="Data Science Associate, AIR", dates="Sep 2025-Present", projects=[_project("Pipeline")]),
        RoleSection(title="Data Science Assistant, AIR", dates="Jun 2023-Sep 2025", projects=[_project("OCR")]),
        RoleSection(title="Intern, Globex", dates="2022", projects=[_project("Churn")]),
    ]).ensure_keys()


def test_ranges_follow_the_base_resume():
    lib = _library()
    base = TailoredResume(roles=[
        RoleEntry("data-science-associate-air", "Data Science Associate", "AIR", "2025", None,
                  [Bullet(f"b{i}") for i in range(6)]),
        RoleEntry("data-science-assistant-air", "Data Science Assistant", "AIR", "2023", None, [Bullet("b")]),
    ], skills={})
    assert _role_slots(lib, base) == {
        "data-science-associate-air": (4, 6),  # the base resume's count, down to two fewer
        "data-science-assistant-air": (0, 1),
        "intern-globex": (0, 2),  # not on the base resume
    }


def test_most_recent_role_needs_a_bullet():
    lib = _library()
    base = TailoredResume(roles=[
        RoleEntry("data-science-associate-air", "Data Science Associate", "AIR", "2025", None, [Bullet("b")]),
    ], skills={})
    assert _role_slots(lib, base)["data-science-associate-air"] == (1, 1)


def test_without_a_base_resume():
    assert _role_slots(_library()) == {
        "data-science-associate-air": (2, 5), "data-science-assistant-air": (0, 3), "intern-globex": (0, 3),
    }
