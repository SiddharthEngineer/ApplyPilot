"""Strelitzia first-time setup wizard.

Interactive flow that creates ~/.strelitzia/ with:
  - resume.txt (and optionally resume.pdf) OR content_library.md
  - profile.json
  - searches.yaml
  - .env (Claude API key and monthly LLM budget)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from strelitzia.config import (
    APP_DIR,
    CONTENT_LIBRARY_PATH,
    ENV_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    RESUME_REFERENCE_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
    load_search_config,
    set_restricted_permissions,
)

console = Console()


# ---------------------------------------------------------------------------
# Helpers for pre-filling from existing config
# ---------------------------------------------------------------------------


def _load_existing_profile() -> dict | None:
    """Load existing profile.json if it exists, else None."""
    if PROFILE_PATH.exists():
        try:
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _load_existing_env() -> dict[str, str]:
    """Parse ~/.strelitzia/.env into a dict of key=value pairs."""
    if not ENV_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _str_to_bool(val: object, default: bool = True) -> bool:
    """Convert a string/bool value to bool for Confirm.ask defaults."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        if val.lower() in ("yes", "true", "1"):
            return True
        if val.lower() in ("no", "false", "0"):
            return False
    return default


def _join_list(val: object) -> str:
    """Join a list to a comma-separated string, or return str(val)."""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if val:
        return str(val)
    return ""


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def _setup_resume() -> None:
    """Prompt for resume source and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nChoose how you want to prepare resumes for tailoring."))

    # Ask workflow preference
    console.print("\n[bold]Which resume source do you want to use?[/bold]")
    console.print("  1) Traditional resume (.txt or .pdf) — existing workflow")
    console.print("  2) Content library (structured project facts) — new workflow")

    while True:
        choice = Prompt.ask("Select", choices=["1", "2"], default="1")
        if choice in ("1", "2"):
            break
        console.print("[red]Please enter 1 or 2.[/red]")

    if choice == "1":
        _setup_traditional_resume()
    else:
        _setup_content_library()

    # Optional: PDF formatting reference
    console.print("\n[bold]Do you have a PDF resume to use as a formatting reference?[/bold]")
    console.print("[dim]This is used by Strelitzia when generating output PDFs (optional).[/dim]")
    if Confirm.ask("Add formatting reference PDF?", default=False):
        _setup_pdf_reference()


def _setup_traditional_resume() -> None:
    """Set up traditional resume workflow (.txt or .pdf)."""
    console.print("\n[bold]Traditional Resume Setup[/bold]")
    console.print("Point to your master resume file (.txt or .pdf).")

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


def _setup_content_library() -> None:
    """Set up content library workflow."""
    console.print("\n[bold]Content Library Setup[/bold]")
    console.print("The content library is a structured bank of raw project facts.")
    console.print("Strelitzia will select relevant projects and write bullets for each job.\n")

    while True:
        path_str = Prompt.ask("Path to your content_library.md file")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        if not src.name.endswith(".md"):
            console.print("[yellow]Warning: File doesn't have .md extension. Continue anyway?[/yellow]")
            if not Confirm.ask("Continue?", default=True):
                continue

        shutil.copy2(src, CONTENT_LIBRARY_PATH)
        console.print(f"[green]✓ Content library copied to {CONTENT_LIBRARY_PATH}[/green]")
        break

    console.print(
        "\n[dim]Tip: resumes are tailored from your content library in Claude: open a job in the dashboard → "
        "[bold]Open in Claude[/bold] (one-time setup at /app/claude-setup).[/dim]"
    )


def _setup_pdf_reference() -> None:
    """Set up optional PDF formatting reference."""
    while True:
        path_str = Prompt.ask("Path to your formatting reference PDF")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        if src.suffix.lower() != ".pdf":
            console.print("[red]Please provide a PDF file.[/red]")
            continue

        shutil.copy2(src, RESUME_REFERENCE_PATH)
        console.print(f"[green]✓ Formatting reference copied to {RESUME_REFERENCE_PATH}[/green]")
        break


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def _setup_profile(existing: dict | None = None) -> dict:
    """Walk through profile questions and return a nested profile dict.

    If *existing* is provided (a previously-saved profile), each prompt
    defaults to the saved value so the user can simply press Enter to keep it.
    """
    console.print(
        Panel(
            "[bold]Step 2: Profile[/bold]\nTell Strelitzia about yourself. This powers scoring, tailoring, and auto-fill."
        )
    )

    ex = existing or {}
    personal = ex.get("personal", {})
    wa = ex.get("work_authorization", {})
    comp = ex.get("compensation", {})
    exp = ex.get("experience", {})
    sb = ex.get("skills_boundary", {})
    rf = ex.get("resume_facts", {})
    avail = ex.get("availability", {})

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    full_name = Prompt.ask("Full name", default=personal.get("full_name", ""))
    profile["personal"] = {
        "full_name": full_name,
        "preferred_name": Prompt.ask(
            "Preferred/nickname (leave blank to use first name)", default=personal.get("preferred_name", "")
        ),
        "email": Prompt.ask("Email address", default=personal.get("email", "")),
        "phone": Prompt.ask("Phone number", default=personal.get("phone", "")),
        "city": Prompt.ask("City", default=personal.get("city", "")),
        "province_state": Prompt.ask(
            "Province/State (e.g. Ontario, California)", default=personal.get("province_state", "")
        ),
        "country": Prompt.ask("Country", default=personal.get("country", "")),
        "postal_code": Prompt.ask("Postal/ZIP code", default=personal.get("postal_code", "")),
        "address": Prompt.ask(
            "Street address (optional, used for form auto-fill)", default=personal.get("address", "")
        ),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=personal.get("linkedin_url", "")),
        "github_url": Prompt.ask("GitHub URL (optional)", default=personal.get("github_url", "")),
        "portfolio_url": Prompt.ask("Portfolio URL (optional)", default=personal.get("portfolio_url", "")),
        "website_url": Prompt.ask("Personal website URL (optional)", default=personal.get("website_url", "")),
    }

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized_to_work": Confirm.ask(
            "Are you legally authorized to work in your target country?",
            default=_str_to_bool(wa.get("legally_authorized_to_work"), True),
        ),
        "require_sponsorship": Confirm.ask(
            "Will you now or in the future need sponsorship?",
            default=_str_to_bool(wa.get("require_sponsorship"), False),
        ),
        "work_permit_type": Prompt.ask(
            "Work permit type (e.g. Citizen, PR, Open Work Permit — leave blank if N/A)",
            default=wa.get("work_permit_type", ""),
        ),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    existing_range_min = comp.get("salary_range_min", "")
    existing_range_max = comp.get("salary_range_max", "")
    if existing_range_min and existing_range_max and existing_range_min != existing_range_max:
        default_range = f"{existing_range_min}-{existing_range_max}"
    elif existing_range_min:
        default_range = str(existing_range_min)
    else:
        default_range = ""

    salary = Prompt.ask("Expected annual salary (number)", default=comp.get("salary_expectation", ""))
    salary_currency = Prompt.ask("Currency", default=comp.get("salary_currency", "USD"))
    salary_range = Prompt.ask("Acceptable range (e.g. 80000-120000)", default=default_range)
    range_parts = salary_range.split("-") if "-" in salary_range else [salary, salary]
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_parts[0].strip(),
        "salary_range_max": range_parts[1].strip() if len(range_parts) > 1 else range_parts[0].strip(),
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    current_title = Prompt.ask("Current/most recent job title", default=exp.get("current_title", ""))
    target_role = Prompt.ask(
        "Target role (what you're applying for, e.g. 'Senior Backend Engineer')",
        default=exp.get("target_role", current_title),
    )
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask(
            "Years of professional experience", default=exp.get("years_of_experience_total", "")
        ),
        "education_level": Prompt.ask(
            "Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=exp.get("education_level", "")
        ),
        "current_title": current_title,
        "target_role": target_role,
    }

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (comma-separated)")
    langs = Prompt.ask(
        "Programming languages", default=_join_list(sb.get("programming_languages", sb.get("languages", [])))
    )
    frameworks = Prompt.ask("Frameworks & libraries", default=_join_list(sb.get("frameworks", [])))
    tools = Prompt.ask("Tools & platforms (e.g. Docker, AWS, Git)", default=_join_list(sb.get("tools", [])))
    profile["skills_boundary"] = {
        "programming_languages": [s.strip() for s in langs.split(",") if s.strip()],
        "frameworks": [s.strip() for s in frameworks.split(",") if s.strip()],
        "tools": [s.strip() for s in tools.split(",") if s.strip()],
    }

    # -- Resume Facts (preserved truths for tailoring) --
    console.print("\n[bold cyan]Resume Facts[/bold cyan]")
    console.print("[dim]These are preserved exactly during resume tailoring — the AI will never change them.[/dim]")
    companies = Prompt.ask(
        "Companies to always keep (comma-separated)", default=_join_list(rf.get("preserved_companies", []))
    )
    projects = Prompt.ask(
        "Projects to always keep (comma-separated)", default=_join_list(rf.get("preserved_projects", []))
    )
    school = Prompt.ask("School name(s) to preserve", default=rf.get("preserved_school", ""))
    metrics = Prompt.ask(
        "Real metrics to preserve (e.g. '99.9% uptime, 50k users')", default=_join_list(rf.get("real_metrics", []))
    )
    profile["resume_facts"] = {
        "preserved_companies": [s.strip() for s in companies.split(",") if s.strip()],
        "preserved_projects": [s.strip() for s in projects.split(",") if s.strip()],
        "preserved_school": school.strip(),
        "real_metrics": [s.strip() for s in metrics.split(",") if s.strip()],
    }

    # -- EEO Voluntary (defaults) --
    eeo = ex.get("eeo_voluntary", {})
    profile["eeo_voluntary"] = {
        "gender": eeo.get("gender", "Decline to self-identify"),
        "race_ethnicity": eeo.get("race_ethnicity", "Decline to self-identify"),
        "veteran_status": eeo.get("veteran_status", "Decline to self-identify"),
        "disability_status": eeo.get("disability_status", "Decline to self-identify"),
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask(
            "Earliest start date", default=avail.get("earliest_start_date", "Immediately")
        ),
    }

    # Save
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    set_restricted_permissions(PROFILE_PATH)
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------


def _setup_searches(existing: dict | None = None) -> None:
    """Generate a searches.yaml from user input.

    If *existing* is provided (a previously-saved searches config), each
    prompt defaults to the saved value so the user can simply press Enter.
    """
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    ex = existing or {}
    defaults = ex.get("defaults", {})
    existing_queries = [q["query"] for q in ex.get("queries", []) if isinstance(q, dict) and "query" in q]
    existing_accept = ex.get("location_accept", [])

    location = Prompt.ask(
        "Primary target location (e.g. 'Chicago', 'New York, NY')",
        default=defaults.get("location", "Chicago"),
    )
    distance_str = Prompt.ask(
        "Search radius in miles (0 for remote-only)",
        default=str(defaults.get("distance", "0")),
    )
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    # Location accept patterns
    console.print(
        "\n[bold]Location filtering[/bold]\n"
        "List all location strings Strelitzia should accept when filtering jobs.\n"
        "[dim]Jobs matching any pattern below (or marked remote) will be kept.[/dim]"
    )
    default_accept = ", ".join(existing_accept) if existing_accept else f"{location}, Remote, US"
    accept_raw = Prompt.ask(
        "Location patterns to accept (comma-separated)",
        default=default_accept,
    )
    accept_patterns = [a.strip() for a in accept_raw.split(",") if a.strip()]

    default_roles = ", ".join(existing_queries) if existing_queries else ""
    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')",
        default=default_roles,
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build YAML content
    lines = [
        "# Strelitzia search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "  site_fail_threshold: 3",
        "  linkedin_fetch_description: false",
        "",
        "# glassdoor and zip_recruiter only work with a proxy (set PROXY); google is unsupported.",
        "sites:",
        "  - indeed",
        "  - linkedin",
        "",
        "location_accept:",
    ]
    for pattern in accept_patterns:
        lines.append(f'  - "{pattern}"')

    lines += [
        "",
        "locations:",
        f'  - location: "{location}"',
        f"    remote: {str(distance == 0).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------


# Settings from the LLM providers Strelitzia used before Claude (migration note: init drops them from .env).
_LEGACY_LLM_KEYS = frozenset({
    "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY", "LLM_URL", "LLM_API_KEY", "LLM_MODEL",
    "LLM_RPM_LIMIT", "LLM_RPD_LIMIT", "LLM_RPM_LIMITS", "LLM_RPD_LIMITS", "LLM_RPM_WINDOW",
    "LLM_FALLBACK_MODEL", "LLM_FALLBACK_COOLDOWN", "EXTRACT_BATCH_SIZE",
})


def _is_legacy_llm_setting(key: str, value: str) -> bool:
    """A pre-Claude provider setting, or a per-stage model override that isn't a Claude model."""
    key = key.removeprefix("export ").strip()
    value = value.split(" #")[0].strip().strip("'\"")
    if key in _LEGACY_LLM_KEYS:
        return True
    return key.startswith("LLM_") and key.endswith("_MODEL") and not value.startswith("claude-")


def _no_limit(budget: str) -> bool:
    """Empty or zero: no monthly budget (the line is left out of .env)."""
    try:
        return float(budget or 0) <= 0
    except ValueError:
        return False


def _setup_ai_features(existing_env: dict[str, str] | None = None) -> None:
    """Ask for the Claude API key and the monthly LLM budget, then write ~/.strelitzia/.env.

    If *existing_env* is provided (parsed from a previous .env), each prompt defaults to the saved value so the
    user can simply press Enter to keep it. Other settings in the file are kept; old-provider settings are dropped.
    """
    console.print(
        Panel(
            "[bold]Step 4: AI Features (optional)[/bold]\n"
            "The Claude API powers job scoring, resume tailoring, and cover letters.\n"
            "Without this, you can still discover and enrich jobs."
        )
    )

    env = existing_env or {}
    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]strelitzia init[/bold].[/dim]")
        return

    console.print(
        "Strelitzia calls the Claude API with your own key (Haiku for bulk stages, Sonnet for tailoring).\n"
        "Create one at [bold]https://console.anthropic.com[/bold] (Settings > API keys)."
    )
    existing_key = env.get("ANTHROPIC_API_KEY", "")
    api_key = Prompt.ask(
        "Anthropic API key" + (" (Enter keeps the saved key)" if existing_key else ""),
        password=True, default=existing_key, show_default=False,
    ) or existing_key
    saved_budget = env.get("LLM_MONTHLY_BUDGET_USD", "20").split("#")[0].strip()
    budget = Prompt.ask("Monthly LLM budget in USD (a run stops before passing it; 0 = no limit)",
                        default=saved_budget or "20").strip()

    dropped = sorted(k for k, v in env.items() if _is_legacy_llm_setting(k, v))
    kept = {k: v for k, v in env.items()
            if k not in dropped and k not in ("ANTHROPIC_API_KEY", "LLM_MONTHLY_BUDGET_USD")}
    env_lines = ["# Strelitzia configuration", "", f"ANTHROPIC_API_KEY={api_key}"]
    if not _no_limit(budget):
        env_lines.append(f"LLM_MONTHLY_BUDGET_USD={budget}")
    env_lines += [f"{k}={v}" for k, v in kept.items()]

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    set_restricted_permissions(ENV_PATH)
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")
    if dropped:
        console.print(f"[dim]Removed settings for the previous LLM providers: {', '.join(dropped)}[/dim]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def run_wizard() -> None:
    """Run the full interactive setup wizard."""
    # Load any existing configs so prompts can pre-fill saved values
    existing_profile = _load_existing_profile()
    existing_searches: dict | None = None
    if SEARCH_CONFIG_PATH.exists():
        try:
            existing_searches = load_search_config()
        except (OSError, ValueError):
            existing_searches = None
    existing_env = _load_existing_env()

    is_rerun = existing_profile is not None

    console.print()
    if is_rerun:
        console.print(
            Panel.fit(
                "[bold green]Strelitzia Reconfigure[/bold green]\n\n"
                "Existing values are pre-filled — press Enter to keep them.\n"
                f"  [cyan]{APP_DIR}[/cyan]\n\n"
                "You can re-run this anytime with [bold]strelitzia init[/bold].",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold green]Strelitzia Setup Wizard[/bold green]\n\n"
                "This will create your configuration at:\n"
                f"  [cyan]{APP_DIR}[/cyan]\n\n"
                "You can re-run this anytime with [bold]strelitzia init[/bold].",
                border_style="green",
            )
        )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    _setup_profile(existing=existing_profile)
    console.print()

    # Step 3: Search config
    _setup_searches(existing=existing_searches)
    console.print()

    # Step 4: AI features (optional LLM)
    _setup_ai_features(existing_env=existing_env)
    console.print()

    # Done — show tier status
    from strelitzia.config import TIER_COMMANDS, TIER_LABELS, get_tier

    tier = get_tier()

    tier_lines: list[str] = []
    for t in sorted(TIER_LABELS):
        label = TIER_LABELS[t]
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS[t])
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]strelitzia init[/bold]).[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n" + "\n".join(tier_lines) + unlock_hint,
            border_style="green",
        )
    )
