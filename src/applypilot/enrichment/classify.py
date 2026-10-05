"""Keyword heuristics for role category, work mode and location (no LLM).

These fill the dashboard filter columns for jobs the `extract` stage hasn't reached yet.
Rows written here get `category_source = 'heuristic'`; LLM extraction overwrites them, and
`run_classify` never touches a row that has `extracted_at` set.
"""

from __future__ import annotations

import logging
import re

from applypilot.enrichment.posting_model import Location

log = logging.getLogger(__name__)


def _rx(*patterns: str) -> re.Pattern:
    """Whole-word, case-insensitive alternation. A pattern ending in `*` matches as a word prefix."""
    parts = [re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p) + r"\b" for p in patterns]
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)


# First match wins, so the order matters: "Data Scientist, ML" is data_science, "Product Manager, AI"
# is product_program_management, "Software Engineer, Machine Learning" is ml_ai_engineering.
_TITLE_RULES: list[tuple[str, re.Pattern]] = [
    # Sales and customer-facing roles that mention a tech word ("Account Executive, Tableau").
    ("other", _rx(
        "sales", "account executive", "account manager", "business development", "recruiter", "recruiting",
        "customer success")),
    ("quant_finance", _rx("quant", "quants", "quantitative", "trader", "trading", "actuar*", "portfolio manager")),
    ("data_science", _rx("data scien*", "decision scien*")),
    ("research_science", _rx("research scien*", "applied scien*", "research engineer", "scientist", "researcher")),
    ("product_program_management", _rx(
        "product manager", "product management", "product owner", "program manager", "programme manager",
        "project manager", "tpm", "product lead", "head of product")),
    ("data_engineering", _rx(
        "data engineer*", "analytics engineer*", "etl", "elt", "data platform", "data pipeline*",
        "data architect*", "big data", "data infrastructure", "data warehous*", "data integration",
        "database engineer", "databricks")),
    ("ml_ai_engineering", _rx(
        "machine learning", "ml", "mlops", "ai", "a.i", "llm*", "deep learning", "computer vision", "nlp",
        "genai", "generative", "prompt engineer", "artificial intelligence", "perception", "applied ai", "agent*")),
    ("data_analytics_bi", _rx(
        "analyst", "analytics", "business intelligence", "bi", "power bi", "tableau", "reporting", "insights")),
    ("hardware_electrical", _rx(
        "hardware", "electrical", "electronics", "asic", "fpga", "rtl", "silicon", "circuit*", "analog",
        "mixed-signal", "ic design", "firmware", "embedded", "pcb", "physical design", "soc", "vlsi",
        "semiconductor", "rf")),
    ("it_infra_devops", _rx(
        "devops", "devsecops", "sre", "site reliability", "infrastructure", "cloud engineer", "cloud architect",
        "platform engineer", "network*", "systems engineer", "system administrator", "sysadmin", "it support",
        "help desk", "helpdesk", "security engineer", "cybersecurity", "cyber", "dba", "database administrator",
        "administrator", "admin")),
    ("software_engineering", _rx(
        "software", "developer", "swe", "sde", "backend", "back-end", "back end", "frontend", "front-end",
        "front end", "full stack", "full-stack", "fullstack", "programmer", "web engineer", "mobile engineer",
        "ios", "android", "forward deployed engineer", "application engineer", "applications engineer")),
]


def classify_title(title: str) -> str:
    """Role category from the job title alone (first matching rule; `other` when none match)."""
    for category, pattern in _TITLE_RULES:
        if pattern.search(title or ""):
            return category
    return "other"


# ── Work mode ──────────────────────────────────────────────────────────────

_NOT_REMOTE = re.compile(
    r"\b(?:not (?:a )?remote|no remote|non-remote|remote work is not|isn't remote|is not remote)\b", re.IGNORECASE)
_HYBRID = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTE_LOC = re.compile(r"\b(?:remote|virtual|work from home|wfh|telecommute|offsite)\b", re.IGNORECASE)
_REMOTE_DESC = re.compile(
    r"\b(?:fully remote|100% remote|remote[- ]first|remote position|remote role|remote opportunity|"
    r"this (?:position|role|job) is remote|work from anywhere|work from home|remote within|"
    r"remote \(|remote, us|remote in the (?:us|united states))", re.IGNORECASE)
_ONSITE_DESC = re.compile(
    r"\b(?:on-?site|in-office|in office|in-person|in person|office-based|office based|"
    r"(?:\d|five|four) days (?:a|per) week in)", re.IGNORECASE)


def classify_work_mode(location: str | None, description: str | None) -> str:
    """remote, hybrid, onsite or unknown, from explicit wording only (location string first)."""
    loc = location or ""
    desc = description or ""
    if _HYBRID.search(loc):
        return "hybrid"
    if _REMOTE_LOC.search(loc):
        return "remote"
    if _NOT_REMOTE.search(desc):
        return "hybrid" if _HYBRID.search(desc) else "onsite"
    if _HYBRID.search(desc):
        return "hybrid"
    if _REMOTE_DESC.search(desc):
        return "remote"
    if _ONSITE_DESC.search(desc):
        return "onsite"
    return "unknown"


# ── Location ───────────────────────────────────────────────────────────────

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "washington dc": "DC",
    "washington d.c.": "DC", "puerto rico": "PR",
}
_STATE_CODES = set(US_STATES.values())
_US_ALIASES = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
_COUNTRIES = {
    "canada", "mexico", "united kingdom", "uk", "england", "ireland", "germany", "france", "netherlands",
    "belgium", "spain", "portugal", "italy", "switzerland", "austria", "poland", "sweden", "norway", "denmark",
    "finland", "india", "china", "japan", "singapore", "australia", "new zealand", "israel", "brazil",
    "argentina", "south korea", "korea", "taiwan", "hong kong", "philippines", "vietnam", "costa rica",
    "colombia", "czech republic", "czechia", "romania", "hungary", "uae", "united arab emirates", "malaysia",
    "indonesia", "thailand", "chile", "peru", "south africa", "egypt", "turkey", "greece", "luxembourg",
}
# Text that describes how/where to work rather than a place.
_NOISE = re.compile(
    r"\((?:remote|hq|hybrid|on-?site)\)|,?\s*more\.\.\.|\bremote work\b|\bremote(?:[- ]friendly)?\b|"
    r"\bwork from home\b|\bvirtual\b|\boffsite\b|\bhybrid\b|\bon-?site\b|\(travel[- ]required\)|\bmetro\b",
    re.IGNORECASE,
)


def _country(token: str) -> str | None:
    t = token.strip().lower().rstrip(".")
    if t in _US_ALIASES or t + "." in _US_ALIASES:
        return "US"
    if t in _COUNTRIES:
        return "UK" if t in ("uk", "united kingdom", "england") else token.strip().title()
    return None


def _state(token: str) -> str | None:
    t = token.strip()
    if t.upper() in _STATE_CODES and (t.isupper() or len(t) == 2):
        return t.upper()
    return US_STATES.get(t.lower())


def _pick_part(location: str) -> str:
    """The first place in a multi-location string ("A; B", "Remote-Friendly | SF, CA | NYC")."""
    parts = [p.strip() for p in re.split(r"[;|]", location) if p.strip()]
    for p in parts:
        if _NOISE.sub("", p).strip(" ,-–"):
            return p
    return parts[0] if parts else ""


def parse_location(location: str | None) -> Location:
    """Best-effort city/state/country from the location formats job boards use.

    Handles "US, CA, Santa Clara", "USA.VA.Reston", "US-CA-Menlo Park", "Chicago, IL, US",
    "Seattle, Washington, US", "Remote, US (Remote)", "San Francisco (Remote)", "Remote - Canada",
    "Illinois Remote Work, More...", "US FL JAX 347". US states come back as 2-letter codes and the
    US as "US". Unrecognized parts are left as the city.
    """
    text = _pick_part(location or "")
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-–")
    if not text:
        return Location(None, None, "US" if re.search(r"\bUSA?\b", location or "") else None)

    # Country-first compact forms: "USA.VA.Reston", "US-CA-Menlo Park", "US FL JAX 347".
    m = re.match(r"^(USA?)[.\-](\w{2})[.\-](.+)$", text)
    if m:
        return Location(m.group(3).strip() or None, _state(m.group(2)), "US")
    m = re.match(r"^USA?-([A-Za-z .]{3,})$", text)  # Cisco: "USA-SAN FRANCISCO"
    if m:
        return Location(m.group(1).strip().title(), None, "US")
    m = re.match(r"^(USA?) ([A-Z]{2})\b", text)
    if m and _state(m.group(2)):
        return Location(None, m.group(2), "US")

    tokens = [t.strip() for t in re.split(r"\s*,\s*|\s+-\s+|\s+–\s+", text) if t.strip()]
    if not tokens:
        return Location(None, None, None)

    # "US, CA, Santa Clara" / "USA, CA, Pleasanton" / "US, Oregon, Hillsboro"
    if len(tokens) >= 2 and _country(tokens[0]) == "US" and _state(tokens[1]):
        return Location(", ".join(tokens[2:]) or None, _state(tokens[1]), "US")

    country = _country(tokens[-1])
    if country:
        tokens = tokens[:-1]
    if not tokens:
        return Location(None, None, country)

    # Leading country with nothing else recognisable ("US, Remote" after noise removal).
    lead = _country(tokens[0])
    if lead and len(tokens) == 1:
        return Location(None, None, lead)

    state = _state(tokens[-1]) if len(tokens) >= 2 else None
    if state and country in (None, "US"):
        tokens, country = tokens[:-1], "US"
    elif len(tokens) >= 2 and country:
        tokens = tokens[:1]  # "Helsinki, Uusimaa, Finland": keep the city, drop the region
    if len(tokens) == 1 and not state and tokens[0].lower() in US_STATES:  # "Maryland, US"
        return Location(None, US_STATES[tokens[0].lower()], "US")
    return Location(tokens[0], state, country)


# ── `applypilot classify` ──────────────────────────────────────────────────

def run_classify(conn=None) -> dict:
    """Fill role_category, work_mode and location_* by heuristics for every job not yet LLM-extracted.

    Rows with `extracted_at` set are never touched. Earlier heuristic values are recomputed, so rule
    changes apply on the next run. Returns {"classified": n, "by_category": {category: n}}.
    """
    from applypilot.database import get_connection

    if conn is None:
        conn = get_connection()
    rows = conn.execute(
        "SELECT url, title, location, full_description, description FROM jobs WHERE extracted_at IS NULL"
    ).fetchall()
    updates = []
    by_category: dict[str, int] = {}
    for url, title, location, full_desc, desc in rows:
        category = classify_title(title or "")
        loc = parse_location(location)
        mode = classify_work_mode(location, full_desc or desc)
        by_category[category] = by_category.get(category, 0) + 1
        updates.append((category, mode, loc.city, loc.state, loc.country, url))
    conn.executemany(
        "UPDATE jobs SET role_category = ?, work_mode = ?, location_city = ?, location_state = ?, "
        "location_country = ?, category_source = 'heuristic' WHERE url = ? AND extracted_at IS NULL",
        updates,
    )
    conn.commit()
    log.info("classify: %d jobs by heuristics", len(updates))
    return {"classified": len(updates), "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1]))}
