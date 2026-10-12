"""ApplyPilot configuration: paths, platform detection, user data."""

import os
import stat
from pathlib import Path

# User data directory — all user-specific files live here
APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))

# Core paths
DB_PATH = APP_DIR / "applypilot.db"
# Postgres job store (postgresql://user:pw@host:port/db). Unset = the SQLite file at DB_PATH.
DATABASE_URL = os.environ.get("APPLYPILOT_DATABASE_URL")
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
RESUME_REFERENCE_PATH = APP_DIR / "resume_reference.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
CONTENT_LIBRARY_PATH = APP_DIR / "content_library.md"
RESUME_TEMPLATE_PATH = APP_DIR / "resume_template.html"
RESUME_FIXED_PATH = APP_DIR / "resume_fixed.yaml"
TEMPLATE_PREVIEW_PATH = APP_DIR / "template_preview.pdf"
ENV_PATH = APP_DIR / ".env"
# Per-model LLM rate limits; overrides the packaged CONFIG_DIR / "llm_limits.yaml"
LLM_LIMITS_PATH = APP_DIR / "llm_limits.yaml"
GOOGLE_CLIENT_SECRET_PATH = APP_DIR / "google_client_secret.json"
GOOGLE_TOKEN_PATH = APP_DIR / "google_token.json"

# Generated output
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"

def ensure_dirs():
    """Create all required directories."""
    for d in [APP_DIR, TAILORED_DIR, COVER_LETTER_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    # Owner-only permissions on the app directory
    try:
        APP_DIR.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except (OSError, AttributeError):
        pass


def set_restricted_permissions(path: Path) -> None:
    """Set file permissions to owner-only read/write (0o600).

    No-op on platforms that don't support Unix permissions (e.g., Windows
    without NTFS ACLs). Errors are logged but not raised.
    """
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, AttributeError):
        pass


def drive_sync_enabled() -> bool:
    """Move PDFs to Google Drive after the tailor and cover stages (DRIVE_SYNC=1 and a saved token)."""
    flag = os.environ.get("DRIVE_SYNC", "").strip().lower() in ("1", "true", "yes", "on")
    return flag and GOOGLE_TOKEN_PATH.exists()


def load_profile() -> dict:
    """Load user profile from ~/.applypilot/profile.json."""
    import json
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"Profile not found at {PROFILE_PATH}. Run `applypilot init` first."
        )
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml
    if not SEARCH_CONFIG_PATH.exists():
        # Fall back to package-shipped example
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8"))
        return {}
    return yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8"))


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, blocked, base URLs)."""
    import yaml
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_tailor_attempts": 5,
    "llm_rpm_limit": 12,
    "llm_discovery_model": "gemini-3.1-flash-lite",
}


def extract_batch_size() -> int:
    """Jobs per LLM request in the extract stage (EXTRACT_BATCH_SIZE, default 5)."""
    try:
        return max(1, int(os.environ.get("EXTRACT_BATCH_SIZE", "5")))
    except ValueError:
        return 5


def database_url() -> str | None:
    """The Postgres URL from APPLYPILOT_DATABASE_URL, read at call time so a .env loaded later counts."""
    return os.environ.get("APPLYPILOT_DATABASE_URL") or None


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM API key
    """
    load_env()

    has_llm = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    if not has_llm:
        return 1

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1 or 2).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console
    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")):
        missing.append("LLM API key — run [bold]applypilot init[/bold] or set GEMINI_API_KEY")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
