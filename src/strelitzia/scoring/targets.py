"""Posting targets: the required and preferred qualifications a screener checks a resume against.

A target is either a short term (a skill or tool, "Airflow", "CI/CD") matched as a phrase, or a qualification
sentence ("3+ years building data pipelines in Python") matched by its content words. Each target is
`evidenced` only by the content library or the base resume: the profile's skills boundary doesn't make a
skill claimable. Coverage of a resume against the targets is reported, never enforced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from strelitzia.scoring.content_library import ContentLibrary
from strelitzia.scoring.template import parse_base_resume

# Posting field -> target kind, in the order targets are listed.
TARGET_FIELDS = (
    ("skills_required", "required"),
    ("required_qualifications", "required"),
    ("skills_preferred", "preferred"),
    ("preferred_qualifications", "preferred"),
)

# Common acronym <-> expansion pairs; the posting's own "Full Name (ACR)" pairs are added per job.
ACRONYMS: dict[str, str] = {
    "ai": "artificial intelligence",
    "ml": "machine learning",
    "nlp": "natural language processing",
    "llm": "large language model",
    "etl": "extract, transform, load",
    "ci/cd": "continuous integration/continuous delivery",
    "aws": "amazon web services",
    "gcp": "google cloud platform",
    "k8s": "kubernetes",
    "bi": "business intelligence",
    "qa": "quality assurance",
    "sre": "site reliability engineering",
    "oop": "object-oriented programming",
    "gis": "geographic information systems",
    "hpc": "high-performance computing",
    "js": "javascript",
    "ts": "typescript",
}

# A target with more words than this is a qualification sentence, matched by content words.
MAX_TERM_WORDS = 4
# Share of a sentence's content words that must appear in one source for it to count as matched.
SENTENCE_MATCH = 0.6
_STOPWORDS = {
    "a", "an", "and", "or", "the", "of", "in", "on", "for", "to", "with", "using", "as", "at", "by", "from",
    "is", "are", "be", "you", "your", "our", "we", "will", "have", "has", "plus", "etc", "e.g", "i.e",
    "experience", "experienced", "years", "year", "yrs", "strong", "solid", "good", "excellent", "knowledge",
    "ability", "able", "skills", "skill", "proficiency", "proficient", "familiarity", "familiar", "understanding",
    "working", "work", "hands-on", "demonstrated", "preferred", "required", "including", "such", "like",
    "related", "relevant", "similar", "least", "minimum", "more", "one", "two", "three", "other", "tools",
}
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#./-]*")
_ACRONYM_PAIR = re.compile(r"((?:[A-Za-z][\w-]*[ ,]+){1,7})\(([A-Za-z][A-Za-z0-9/&]{1,9})\)")


@dataclass(frozen=True)
class Target:
    text: str
    kind: str  # "required" | "preferred"
    field: str  # the JobPosting field it came from

    @property
    def is_term(self) -> bool:
        """A short skill or tool name (matched as a phrase), rather than a qualification sentence."""
        return len(self.text.split()) <= MAX_TERM_WORDS


@dataclass
class Evidence:
    target: Target
    slugs: list[str] = field(default_factory=list)  # content-library projects that back it
    base_roles: list[str] = field(default_factory=list)  # base-resume role keys, or "skills"

    @property
    def evidenced(self) -> bool:
        return bool(self.slugs or self.base_roles)


@dataclass
class EvidenceMap:
    items: list[Evidence] = field(default_factory=list)
    pairs: dict[str, str] = field(default_factory=dict)  # acronym -> expansion found in the posting

    def evidenced(self) -> list[Evidence]:
        return [e for e in self.items if e.evidenced]

    def unevidenced(self) -> list[Evidence]:
        return [e for e in self.items if not e.evidenced]

    def targets(self) -> list[Target]:
        return [e.target for e in self.items]


@dataclass
class CoverageReport:
    covered: list[str]
    missing: list[str]
    pct_required: float | None  # None when the posting lists no required targets
    pct_preferred: float | None

    def to_dict(self) -> dict:
        return {"covered": self.covered, "missing": self.missing,
                "pct_required": self.pct_required, "pct_preferred": self.pct_preferred}


def targets(posting: dict) -> list[Target]:
    """The posting's required then preferred skills and qualifications, without duplicates (case-insensitive)."""
    out: list[Target] = []
    seen: set[str] = set()
    for name, kind in TARGET_FIELDS:
        for raw in posting.get(name) or []:
            text = re.sub(r"\s+", " ", str(raw)).strip().rstrip(".;")
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                out.append(Target(text=text, kind=kind, field=name))
    return out


def acronym_pairs(text: str) -> dict[str, str]:
    """"Full Name (ACR)" pairs in a posting: {"acr": "full name"} when the words' initials spell the acronym."""
    pairs: dict[str, str] = {}
    for m in _ACRONYM_PAIR.finditer(text or ""):
        words = re.split(r"[ ,]+", m.group(1).strip())
        acr = m.group(2)
        letters = re.sub(r"[^A-Za-z]", "", acr).lower()
        for n in range(min(len(words), 7), 0, -1):
            tail = words[-n:]
            if "".join(w[0].lower() for w in tail) == letters:
                pairs[acr.casefold()] = " ".join(tail).casefold()
                break
    return pairs


def term_variants(term: str, pairs: dict[str, str] | None = None) -> set[str]:
    """The term case-folded, plus its acronym or expansion (from ACRONYMS and the posting's own pairs)."""
    t = re.sub(r"\s+", " ", term).strip().casefold()
    table = {**ACRONYMS, **(pairs or {})}
    out = {t}
    if t in table:
        out.add(table[t])
    out.update(acr for acr, full in table.items() if full == t)
    # "Machine Learning (ML)" as one target
    m = re.fullmatch(r"(.+?)\s*\(([^)]+)\)", t)
    if m:
        out.update({m.group(1).strip(), m.group(2).strip()})
    return {v for v in out if v}


def _phrase_in(phrase: str, text: str) -> bool:
    """Whole-phrase match; single characters (C, R) are matched case-sensitively in the original casing."""
    if len(phrase) == 1:
        return re.search(rf"(?<![\w+#]){re.escape(phrase.upper())}(?![\w+#])", text) is not None
    return re.search(rf"(?<![\w+#]){re.escape(phrase)}(?![\w+#])", text.casefold()) is not None


def content_tokens(text: str) -> set[str]:
    """Content words of a sentence, singular ("pipelines" -> "pipeline"), without stopwords and bare numbers."""
    out = set()
    for tok in _TOKEN.findall(text.casefold()):
        tok = tok.strip("./-")
        if len(tok) < 2 or tok in _STOPWORDS or not any(c.isalpha() for c in tok):
            continue
        out.add(tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok)
    return out


def matches(target: Target | str, text: str, pairs: dict[str, str] | None = None) -> bool:
    """Does `text` state this target? Terms by phrase (any variant), sentences by content-word overlap."""
    t = target if isinstance(target, Target) else Target(str(target), "required", "")
    if t.is_term:
        return any(_phrase_in(v, text) for v in term_variants(t.text, pairs))
    words = content_tokens(t.text)
    if not words:
        return False
    have = content_tokens(text)
    return len(words & have) / len(words) >= SENTENCE_MATCH


def evidence_map(posting: dict, library: ContentLibrary | None, base_resume_text: str = "",
                 description: str = "") -> EvidenceMap:
    """Which targets the candidate can honestly claim: backed by a library project or the base resume."""
    pairs = acronym_pairs(" ".join([description, *(t.text for t in targets(posting))]))
    projects = [(p.slug, p.facts()) for r in (library.ensure_keys().roles if library else []) for p in r.projects]
    base = parse_base_resume(base_resume_text, library) if base_resume_text.strip() else None
    base_sources: list[tuple[str, str]] = []
    if base:
        base_sources = [(r.role_key, f"{r.title} {r.tagline or ''} " + " ".join(b.text for b in r.bullets))
                        for r in base.roles]
        if base.skills:
            base_sources.append(("skills", " ".join(f"{k}: {v}" for k, v in base.skills.items())))
    items = []
    for t in targets(posting):
        items.append(Evidence(
            target=t,
            slugs=[slug for slug, facts in projects if matches(t, facts, pairs)],
            base_roles=[key for key, text in base_sources if matches(t, text, pairs)],
        ))
    return EvidenceMap(items=items, pairs=pairs)


def coverage(resume_text: str, target_list: list[Target], pairs: dict[str, str] | None = None) -> CoverageReport:
    """Which targets the resume text states. Percentages are 0-100, rounded."""
    covered = [t for t in target_list if matches(t, resume_text, pairs)]
    missing = [t for t in target_list if t not in covered]

    def pct(kind: str) -> float | None:
        total = [t for t in target_list if t.kind == kind]
        if not total:
            return None
        return round(100 * sum(t in covered for t in total) / len(total), 1)

    return CoverageReport(covered=[t.text for t in covered], missing=[t.text for t in missing],
                          pct_required=pct("required"), pct_preferred=pct("preferred"))
