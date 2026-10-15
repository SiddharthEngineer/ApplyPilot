"""Posting targets, the evidence map (what is honestly claimable) and coverage."""

from strelitzia.scoring.targets import Target, acronym_pairs, coverage, evidence_map, matches, targets, term_variants
from workbench_helpers import BASE_RESUME, DESCRIPTION, POSTING, library


def _by_text(ev):
    return {e.target.text: e for e in ev.items}


def test_targets_required_then_preferred_without_duplicates():
    ts = targets({"skills_required": ["Python", "python"], "required_qualifications": ["SQL."],
                  "skills_preferred": ["dbt"], "preferred_qualifications": ["Python"]})
    assert [(t.text, t.kind) for t in ts] == [("Python", "required"), ("SQL", "required"), ("dbt", "preferred")]


def test_term_variants_acronyms_both_ways():
    assert "ml" in term_variants("Machine Learning")
    assert "machine learning" in term_variants("ML")
    assert {"customer data platform", "cdp"} <= term_variants("CDP", {"cdp": "customer data platform"})
    assert {"natural language processing", "nlp"} <= term_variants("Natural Language Processing (NLP)")


def test_acronym_pairs_from_posting():
    assert acronym_pairs(DESCRIPTION) == {"cdp": "customer data platform"}


def test_evidence_map_on_synthetic_library():
    ev = _by_text(evidence_map(POSTING, library(), BASE_RESUME, description=DESCRIPTION))
    assert ev["Airflow"].evidenced and "orders-pipeline" in ev["Airflow"].slugs
    assert "skills" in ev["Airflow"].base_roles
    # Acronym match: the library says "ML", the posting says "Machine Learning".
    assert ev["Machine Learning"].slugs == ["churn-model"]
    # The posting's own "Customer Data Platform (CDP)" pair counts.
    assert ev["CDP"].slugs == ["quality-dashboard"]
    # A skills-boundary-only skill is not claimable.
    assert not ev["Kubernetes"].evidenced
    assert not ev["Terraform"].evidenced
    # Sentences match by content words.
    assert ev["3+ years building data pipelines with Spark"].evidenced


def test_evidence_without_base_resume_uses_library_only():
    ev = _by_text(evidence_map(POSTING, library(), "", description=DESCRIPTION))
    assert ev["dbt"].slugs == ["orders-pipeline"] and ev["dbt"].base_roles == []


def test_single_letter_terms_are_case_sensitive():
    assert matches("R", "Built models in R and Python")
    assert not matches("R", "r&d budget for the research team")


def test_terms_match_whole_words_only():
    assert not matches("Java", "Wrote JavaScript widgets")
    assert matches("C++", "Ported the engine to C++ and Rust")


def test_coverage_reports_percentages():
    ts = [Target("Airflow", "required", "skills_required"), Target("Kubernetes", "required", "skills_required"),
          Target("dbt", "preferred", "skills_preferred")]
    rep = coverage("Built Airflow DAGs and dbt models", ts)
    assert rep.covered == ["Airflow", "dbt"] and rep.missing == ["Kubernetes"]
    assert rep.pct_required == 50.0 and rep.pct_preferred == 100.0
    assert coverage("x", []).pct_required is None
