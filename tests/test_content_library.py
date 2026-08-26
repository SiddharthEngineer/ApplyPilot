"""Tests for the content library parser."""

import textwrap
from pathlib import Path

import pytest

from applypilot.scoring.content_library import (
    ContentLibrary,
    _parse_angle_tags,
    _parse_dates,
    _parse_project,
    parse_content_library,
)

REAL_LIBRARY = Path(__file__).resolve().parent.parent / "personal" / "content_library.md"


# ── Angle tag parsing ─────────────────────────────────────────────────────


class TestParseAngleTags:
    def test_simple_comma_separated(self):
        assert _parse_angle_tags("DEVOPS, PIPELINE, LEADERSHIP") == [
            "DEVOPS",
            "PIPELINE",
            "LEADERSHIP",
        ]

    def test_with_parenthetical_explanations(self):
        raw = (
            "DEVOPS (orchestration, containers, migration ownership), "
            "PIPELINE (end-to-end release ownership), "
            "LEADERSHIP (cross-org point of contact), "
            "DATA-QUALITY (QC/validation via Superset)."
        )
        result = _parse_angle_tags(raw)
        assert result == ["DEVOPS", "PIPELINE", "LEADERSHIP", "DATA-QUALITY"]

    def test_single_tag(self):
        assert _parse_angle_tags("DS/ML") == ["DS/ML"]

    def test_trailing_period(self):
        assert _parse_angle_tags("DS/ML.") == ["DS/ML"]

    def test_trailing_dash(self):
        assert _parse_angle_tags("DS/ML. ---") == ["DS/ML"]

    def test_mixed_case_normalized_to_upper(self):
        assert _parse_angle_tags("devops, Pipeline, LeAdErShIp") == [
            "DEVOPS",
            "PIPELINE",
            "LEADERSHIP",
        ]


# ── Date parsing ──────────────────────────────────────────────────────────


class TestParseDates:
    def test_role_header_with_dates(self):
        header = "## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)"
        assert _parse_dates(header) == "Sep 2025–Present"

    def test_project_header_with_dates(self):
        header = "### PatentsView Data Pipeline Lead (Nov 2025–present)"
        assert _parse_dates(header) == "Nov 2025–present"

    def test_no_dates(self):
        assert _parse_dates("## CURRENT ROLE — Data Science Associate, AIR") == ""

    def test_intern_header(self):
        header = "### d-blink Testing for PatentsView (Summer 2022)"
        assert _parse_dates(header) == "Summer 2022"


# ── Project parsing ───────────────────────────────────────────────────────


class TestParseProject:
    def test_standard_project(self):
        lines = [
            "### PatentsView Data Pipeline Lead (Nov 2025–present)",
            "- **Context:** PatentsView is a federally supported patent database.",
            "- **Scope/Scale:** Managed the full data release.",
            "- **Tools & Actions:** Airflow for orchestration.",
            "- **Outcome/Metrics:** Successful public release on schedule.",
            "- **Angles:** DEVOPS, PIPELINE, LEADERSHIP.",
        ]
        proj = _parse_project(lines, "## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)")
        assert proj is not None
        assert proj.name == "PatentsView Data Pipeline Lead"
        assert proj.dates == "Nov 2025–present"
        assert "PatentsView" in proj.context
        assert "full data release" in proj.scope_scale
        assert "Airflow" in proj.tools_actions
        assert "Successful public release" in proj.outcome_metrics
        assert proj.angles == ["DEVOPS", "PIPELINE", "LEADERSHIP"]

    def test_combined_context_scope_field(self):
        lines = [
            "### PatentsView Data Quality Lead — earlier phase (Aug 2024–May 2025)",
            "- **Context/Scope:** Same initiative as above.",
            "- **Tools & Actions:** Designed sampling strategy.",
            "- **Angles:** DATA-QUALITY, LEADERSHIP, DS/ML.",
        ]
        proj = _parse_project(lines, "## PRIOR ROLE — Data Science Assistant, AIR (Jun 2023–Sep 2025)")
        assert proj is not None
        assert "Same initiative" in proj.context
        assert proj.scope_scale == ""  # Context/Scope maps to context, not scope_scale
        assert "sampling strategy" in proj.tools_actions
        assert proj.angles == ["DATA-QUALITY", "LEADERSHIP", "DS/ML"]

    def test_empty_lines_skipped(self):
        lines = [
            "### Test Project (2024–2025)",
            "",
            "- **Context:** Some context.",
            "",
            "- **Angles:** DEVOPS.",
        ]
        proj = _parse_project(lines, "## CURRENT ROLE — Test (2024–Present)")
        assert proj is not None
        assert proj.context == "Some context."
        assert proj.angles == ["DEVOPS"]

    def test_multi_line_field_value(self):
        lines = [
            "### Test Project (2024–2025)",
            "- **Tools & Actions:** Built with Dagster for orchestration;",
            "coordinated video/audio processing, transcription,",
            "and NLP stages.",
            "- **Angles:** PIPELINE.",
        ]
        proj = _parse_project(lines, "## CURRENT ROLE — Test (2024–Present)")
        assert proj is not None
        assert "Dagster" in proj.tools_actions
        assert "NLP stages" in proj.tools_actions

    def test_empty_input_returns_none(self):
        assert _parse_project([], "role") is None

    def test_no_header_returns_none(self):
        assert _parse_project(["just some text"], "role") is None


# ── Full library parsing ──────────────────────────────────────────────────


class TestParseContentLibrary:
    def test_parse_real_library(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        assert isinstance(lib, ContentLibrary)
        assert len(lib.roles) == 4

        # Current role
        current = lib.roles[0]
        assert "Data Science Associate" in current.title
        assert len(current.projects) == 7

        # Prior role (assistant)
        prior = lib.roles[1]
        assert "Data Science Assistant" in prior.title
        assert len(prior.projects) == 7

        # Intern role
        intern = lib.roles[2]
        assert "Intern" in intern.title
        assert len(intern.projects) == 2

        # CapConnect+ role
        capconnect = lib.roles[3]
        assert "CapConnect+" in capconnect.title
        assert len(capconnect.projects) == 3

        total = sum(len(r.projects) for r in lib.roles)
        assert total == 19

    def test_all_angles_collected(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        expected_angles = {"DEVOPS", "PIPELINE", "LEADERSHIP", "DATA-QUALITY", "CS/SWE", "DS/ML"}
        assert expected_angles.issubset(lib.all_angles)

    def test_project_names_from_real_library(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        all_names = [p.name for r in lib.roles for p in r.projects]
        assert "PatentsView Data Pipeline Lead" in all_names
        assert "CAFE Pipeline Engineer" in all_names
        assert "AIM-HI Opportunity Fit Platform Developer" in all_names
        assert "CIPPIA" in all_names
        assert "d-blink Testing for PatentsView" in all_names
        assert "LinkedIn / Social Media Leadership" in all_names

    def test_project_dates_populated(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        # Every project should have non-empty dates
        for role in lib.roles:
            for proj in role.projects:
                assert proj.dates, f"Project '{proj.name}' has no dates"

    def test_project_contexts_non_empty(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        for role in lib.roles:
            for proj in role.projects:
                assert proj.context, f"Project '{proj.name}' has no context"

    def test_project_tools_non_empty(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        for role in lib.roles:
            for proj in role.projects:
                assert proj.tools_actions, f"Project '{proj.name}' has no tools_actions"

    def test_project_angles_non_empty(self):
        if not REAL_LIBRARY.exists():
            pytest.skip("content_library.md not found")
        lib = parse_content_library(REAL_LIBRARY)

        for role in lib.roles:
            for proj in role.projects:
                assert proj.angles, f"Project '{proj.name}' has no angles"


# ── Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_role_sections(self, tmp_path):
        md = tmp_path / "empty.md"
        md.write_text("# Just a title\n\nSome content.\n", encoding="utf-8")
        lib = parse_content_library(md)
        assert lib.roles == []
        assert lib.all_angles == set()

    def test_role_with_no_projects(self, tmp_path):
        md = tmp_path / "no_projects.md"
        md.write_text(
            "## CURRENT ROLE — Test Role (Jan 2024–Present)\n\nNo projects listed.\n",
            encoding="utf-8",
        )
        lib = parse_content_library(md)
        assert len(lib.roles) == 1
        assert lib.roles[0].projects == []

    def test_maintenance_notes_skipped(self, tmp_path):
        md = tmp_path / "maintenance.md"
        md.write_text(
            textwrap.dedent("""\
                ## CURRENT ROLE — Test (2024–Present)

                ### Project A (2024–2025)

                - **Context:** Context here.
                - **Angles:** DEVOPS.

                ## Maintenance notes

                - Keep this updated.
            """),
            encoding="utf-8",
        )
        lib = parse_content_library(md)
        assert len(lib.roles) == 1
        assert len(lib.roles[0].projects) == 1
        assert lib.roles[0].projects[0].name == "Project A"
