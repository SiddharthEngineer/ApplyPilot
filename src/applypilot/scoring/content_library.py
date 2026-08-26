"""Content Library parser: reads personal/content_library.md into structured data.

The content library is a structured bank of raw project facts (Context / Scope /
Tools / Outcome / Angles) organized under role headers. An LLM selects the 5-7
most relevant projects for each job, writes one bullet per project from raw facts,
and outputs a role-grouped resume.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Project:
    """A single project entry from the content library."""

    name: str
    role_header: str
    dates: str
    context: str = ""
    scope_scale: str = ""
    tools_actions: str = ""
    outcome_metrics: str = ""
    angles: list[str] = field(default_factory=list)


@dataclass
class RoleSection:
    """A role section containing one or more projects."""

    title: str
    dates: str
    projects: list[Project] = field(default_factory=list)


@dataclass
class ContentLibrary:
    """The parsed content library."""

    roles: list[RoleSection] = field(default_factory=list)
    all_angles: set[str] = field(default_factory=set)


def _parse_dates(header: str) -> str:
    """Extract the date portion from a role or project header.

    Role headers: '## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)'
    Project headers: '### PatentsView Data Pipeline Lead (Nov 2025–present)'
    """
    m = re.search(r"\(([^)]+)\)\s*$", header)
    return m.group(1).strip() if m else ""


def _parse_angle_tags(raw: str) -> list[str]:
    """Parse angle tags from the Angles field value.

    Input: 'DEVOPS (orchestration, containers, migration ownership), PIPELINE ...'
    Output: ['DEVOPS', 'PIPELINE', 'LEADERSHIP', 'DATA-QUALITY']

    Handles parenthetical explanations that contain commas.
    """
    tags = []
    depth = 0
    current: list[str] = []

    for ch in raw:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            tag = "".join(current).strip().split("(")[0].strip()
            # Strip trailing punctuation/dashes from the tag
            tag = re.sub(r"[\s.\u2014\u2013-]+$", "", tag).upper()
            if tag:
                tags.append(tag)
            current = []
        else:
            current.append(ch)

    # Flush last tag
    if current:
        tag = "".join(current).strip().split("(")[0].strip()
        tag = re.sub(r"[\s.\u2014\u2013-]+$", "", tag).upper()
        if tag:
            tags.append(tag)

    return tags


def _parse_project(lines: list[str], role_header: str) -> Project | None:
    """Parse a single project from its accumulated lines.

    Args:
        lines: All lines belonging to this project (including the ### header).
        role_header: The role header string this project belongs to.

    Returns:
        A Project instance, or None if parsing fails.
    """
    if not lines:
        return None

    # Extract project name and dates from the ### header
    header = lines[0]
    m = re.match(r"###\s+(.+)", header)
    if not m:
        return None
    raw_name = m.group(1).strip()
    dates = _parse_dates(raw_name)
    # Remove trailing date parenthetical to get the project name
    name = re.sub(r"\s*\([^)]*\)\s*$", "", raw_name).strip()

    # Parse fields from remaining lines
    context = ""
    scope_scale = ""
    tools_actions = ""
    outcome_metrics = ""
    angles: list[str] = []

    field_pattern = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)")

    current_field = None
    current_value_parts: list[str] = []

    def _flush():
        nonlocal context, scope_scale, tools_actions, outcome_metrics, angles
        if current_field and current_value_parts:
            value = " ".join(current_value_parts).strip()
            normalized = current_field.lower().replace("/", "_").replace(" ", "_")
            if normalized in ("context", "context_scope"):
                context = value
            elif normalized in ("scope_scale", "scope"):
                scope_scale = value
            elif normalized in ("tools_actions", "tools_&_actions", "tools_actions"):
                tools_actions = value
            elif normalized in ("outcome_metrics", "outcome", "outcome/metrics"):
                outcome_metrics = value
            elif normalized == "angles":
                angles = _parse_angle_tags(value)

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue

        m = field_pattern.match(stripped)
        if m:
            _flush()
            current_field = m.group(1)
            current_value_parts = [m.group(2)] if m.group(2) else []
        elif current_field:
            # Continuation of previous field
            current_value_parts.append(stripped)

    _flush()

    return Project(
        name=name,
        role_header=role_header,
        dates=dates,
        context=context,
        scope_scale=scope_scale,
        tools_actions=tools_actions,
        outcome_metrics=outcome_metrics,
        angles=angles,
    )


def parse_content_library(path: str | Path) -> ContentLibrary:
    """Parse a content_library.md file into structured data.

    Args:
        path: Path to the content_library.md file.

    Returns:
        A ContentLibrary instance with all roles and projects.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Find where role sections start (skip README section)
    first_role_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^## (CURRENT|PRIOR) ROLE\b", line):
            first_role_idx = i
            break

    if first_role_idx is None:
        return ContentLibrary()

    # Split lines into role sections
    role_sections: list[tuple[str, list[str]]] = []
    current_role_header = ""
    current_lines: list[str] = []

    for line in lines[first_role_idx:]:
        if re.match(r"^## (CURRENT|PRIOR) ROLE\b", line):
            if current_role_header:
                role_sections.append((current_role_header, current_lines))
            current_role_header = line.strip()
            current_lines = []
        elif re.match(r"^## \w", line):
            # Hit a non-role ## header (e.g., ## Maintenance notes) — stop
            if current_role_header:
                role_sections.append((current_role_header, current_lines))
            current_role_header = ""
            current_lines = []
        else:
            current_lines.append(line)

    if current_role_header:
        role_sections.append((current_role_header, current_lines))

    # Parse each role section into RoleSection + Projects
    roles: list[RoleSection] = []
    all_angles: set[str] = set()

    for role_header, section_lines in role_sections:
        # Extract role title and dates from header
        role_title = re.sub(r"^##\s+(?:CURRENT|PRIOR)\s+ROLE\s*[—–-]\s*", "", role_header).strip()
        role_dates = _parse_dates(role_header)

        # Split by ### headers to get individual projects
        projects: list[Project] = []
        current_project_lines: list[str] = []

        for line in section_lines:
            if re.match(r"^###\s+", line):
                if current_project_lines:
                    proj = _parse_project(current_project_lines, role_header)
                    if proj:
                        projects.append(proj)
                        all_angles.update(proj.angles)
                current_project_lines = [line]
            else:
                current_project_lines.append(line)

        if current_project_lines:
            proj = _parse_project(current_project_lines, role_header)
            if proj:
                projects.append(proj)
                all_angles.update(proj.angles)

        roles.append(RoleSection(title=role_title, dates=role_dates, projects=projects))

    return ContentLibrary(roles=roles, all_angles=all_angles)
