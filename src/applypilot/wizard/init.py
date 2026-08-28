"""ApplyPilot first-time setup wizard.

Interactive flow that creates ~/.applypilot/ with:
  - resume.txt (and optionally resume.pdf) OR content_library.md
  - profile.json
  - searches.yaml
  - .env (LLM API key)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    APP_DIR,
    CONTENT_LIBRARY_PATH,
    ENV_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    RESUME_REFERENCE_PATH,
    SEARCH_CONFIG_PATH,
    SITE_PASSWORDS,
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
    """Parse ~/.applypilot/.env into a dict of key=value pairs."""
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
    console.print("[dim]This is used by ApplyPilot when generating output PDFs (optional).[/dim]")
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
    console.print("ApplyPilot will select relevant projects and write bullets for each job.\n")

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
        "\n[dim]Tip: Run [bold]applypilot run tailor --source content-library[/bold] to tailor resumes from your content library.[/dim]"
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
# Site Passwords
# ---------------------------------------------------------------------------


def _setup_site_passwords(existing: dict[str, str] | None = None) -> dict[str, str]:
    """Prompt for passwords per-ATS platform and return a site_passwords dict."""
    console.print(
        Panel(
            "[bold]Site Passwords[/bold]\n"
            "Different job sites use different ATS platforms, each with its own login.\n"
            "Enter the password you use for each platform (leave blank if not applicable)."
        )
    )

    sp = existing or {}
    site_passwords: dict[str, str] = {}
    for ats_key, ats_info in SITE_PASSWORDS.items():
        site_passwords[ats_key] = Prompt.ask(
            f"  {ats_info['description']}\n  Password",
            password=True,
            default=sp.get(ats_key, ""),
        )

    configured = [k for k, v in site_passwords.items() if v]
    if configured:
        console.print(f"[green]Configured passwords for: {', '.join(configured)}[/green]")
    else:
        console.print("[dim]No site passwords configured. You can add them later by editing profile.json.[/dim]")

    return site_passwords


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
            "[bold]Step 2: Profile[/bold]\nTell ApplyPilot about yourself. This powers scoring, tailoring, and auto-fill."
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

    # -- Site Passwords --
    profile["site_passwords"] = _setup_site_passwords(existing=ex.get("site_passwords"))

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
        "List all location strings ApplyPilot should accept when filtering jobs.\n"
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
        "# ApplyPilot search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "  site_fail_threshold: 1",
        "",
        "sites:",
        "  - indeed",
        "  - linkedin",
        "  - glassdoor",
        "  - google",
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


def _setup_ai_features(existing_env: dict[str, str] | None = None) -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration.

    If *existing_env* is provided (parsed from a previous .env), each prompt
    defaults to the saved value so the user can simply press Enter to keep it.
    """
    console.print(
        Panel(
            "[bold]Step 4: AI Features (optional)[/bold]\n"
            "An LLM powers job scoring, resume tailoring, and cover letters.\n"
            "Without this, you can still discover and enrich jobs."
        )
    )

    env = existing_env or {}
    has_existing_llm = any(env.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=has_existing_llm or True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]applypilot init[/bold].[/dim]")
        return

    # Detect provider from existing env
    if env.get("GEMINI_API_KEY"):
        detected_provider = "gemini"
    elif env.get("OPENAI_API_KEY"):
        detected_provider = "openai"
    elif env.get("LLM_URL"):
        detected_provider = "local"
    else:
        detected_provider = "gemini"

    console.print("Supported providers: [bold]Gemini[/bold] (recommended, free tier), OpenAI, local (Ollama/llama.cpp)")
    provider = Prompt.ask(
        "Provider",
        choices=["gemini", "openai", "local"],
        default=detected_provider,
    )

    env_lines = ["# ApplyPilot configuration", ""]

    if provider == "gemini":
        existing_key = env.get("GEMINI_API_KEY", "")
        api_key = Prompt.ask("Gemini API key (from aistudio.google.com)", default=existing_key)
        if not api_key and existing_key:
            api_key = existing_key
        model = Prompt.ask("Model", default=env.get("LLM_MODEL", "gemini-3.6-flash"))
        env_lines.append(f"GEMINI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "openai":
        existing_key = env.get("OPENAI_API_KEY", "")
        api_key = Prompt.ask("OpenAI API key", default=existing_key)
        if not api_key and existing_key:
            api_key = existing_key
        model = Prompt.ask("Model", default=env.get("LLM_MODEL", "gpt-4o-mini"))
        env_lines.append(f"OPENAI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "local":
        url = Prompt.ask("Local LLM endpoint URL", default=env.get("LLM_URL", "http://localhost:8080/v1"))
        model = Prompt.ask("Model name", default=env.get("LLM_MODEL", "local-model"))
        env_lines.append(f"LLM_URL={url}")
        env_lines.append(f"LLM_MODEL={model}")

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    set_restricted_permissions(ENV_PATH)
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------


def _setup_auto_apply() -> None:
    """Configure autonomous job application (requires Claude Code or OpenCode CLI)."""
    console.print(
        Panel(
            "[bold]Step 5: Auto-Apply (optional)[/bold]\n"
            "ApplyPilot can autonomously fill and submit job applications\n"
            "using Claude Code or OpenCode as the browser agent."
        )
    )

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes ApplyPilot generates.[/dim]")
        return

    # Check for both CLIs
    has_claude = shutil.which("claude") is not None
    has_opencode = shutil.which("opencode") is not None

    if has_claude and has_opencode:
        console.print("[green]Both Claude Code and OpenCode CLI detected.[/green]")
        console.print("[dim]You can choose which to use with --backend flag (default: claude).[/dim]")
    elif has_claude:
        console.print("[green]Claude Code CLI detected.[/green]")
        console.print("[dim]OpenCode is also available as an alternative (https://opencode.ai).[/dim]")
    elif has_opencode:
        console.print("[green]OpenCode CLI detected.[/green]")
    else:
        console.print(
            "[yellow]Neither Claude Code nor OpenCode CLI found on PATH.[/yellow]\n"
            "Install Claude Code from: [bold]https://claude.ai/code[/bold]\n"
            "Or install OpenCode from: [bold]https://opencode.ai[/bold]\n"
            "Auto-apply won't work until one is installed."
        )

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# ApplyPilot configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        set_restricted_permissions(ENV_PATH)
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


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
                "[bold green]ApplyPilot Reconfigure[/bold green]\n\n"
                "Existing values are pre-filled — press Enter to keep them.\n"
                f"  [cyan]{APP_DIR}[/cyan]\n\n"
                "You can re-run this anytime with [bold]applypilot init[/bold].",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold green]ApplyPilot Setup Wizard[/bold green]\n\n"
                "This will create your configuration at:\n"
                f"  [cyan]{APP_DIR}[/cyan]\n\n"
                "You can re-run this anytime with [bold]applypilot init[/bold].",
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

    # Step 5: Auto-apply (Claude Code detection)
    _setup_auto_apply()
    console.print()

    # Done — show tier status
    from applypilot.config import TIER_COMMANDS, TIER_LABELS, get_tier

    tier = get_tier()

    tier_lines: list[str] = []
    for t in range(1, 4):
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
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]applypilot init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: install Claude Code or OpenCode CLI + Chrome.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n" + "\n".join(tier_lines) + unlock_hint,
            border_style="green",
        )
    )
