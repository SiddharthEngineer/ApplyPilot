"""Tests for the keyword heuristics: role category, work mode, location, and `run_classify`."""

import pytest

from applypilot.database import init_db
from applypilot.enrichment.classify import classify_title, classify_work_mode, parse_location, run_classify
from applypilot.enrichment.posting_model import ROLE_CATEGORIES, Location


@pytest.mark.parametrize("title,expected", [
    ("Senior Data Engineer", "data_engineering"),
    ("Analytics Engineer II", "data_engineering"),
    ("ETL Developer", "data_engineering"),
    ("Machine Learning Engineer", "ml_ai_engineering"),
    ("Senior Software Engineer, Machine Learning", "ml_ai_engineering"),
    ("ML Engineer", "ml_ai_engineering"),
    ("AI Engineer", "ml_ai_engineering"),
    ("LLM Inference Engineer", "ml_ai_engineering"),
    ("Data Scientist", "data_science"),
    ("Machine Learning Data Scientist, Forecasting", "data_science"),
    ("Data Analyst", "data_analytics_bi"),
    ("Business Intelligence Developer", "data_analytics_bi"),
    ("Research Scientist, Alignment", "research_science"),
    ("Senior Applied Scientist", "research_science"),
    ("Senior Product Manager, AI & Analytics", "product_program_management"),
    ("Technical Program Manager", "product_program_management"),
    ("Quantitative Researcher", "quant_finance"),
    ("Quantum Hardware Engineer", "hardware_electrical"),
    ("Analog/mixed-signal IC Design Engineer", "hardware_electrical"),
    ("Sr. Site Reliability Engineer", "it_infra_devops"),
    ("Manager, DevOps", "it_infra_devops"),
    ("Senior Software Engineer - Distributed Systems", "software_engineering"),
    ("Full Stack Software Engineer", "software_engineering"),
    ("Web Developer, eCRM", "software_engineering"),
    ("Account Executive, Tableau", "other"),
    ("Warehouse Team Lead", "other"),
    ("Mainframe Training Specialist", "other"),  # "ai" inside "Mainframe"/"Training" is not a word
    ("", "other"),
])
def test_classify_title(title, expected):
    assert classify_title(title) == expected
    assert expected in ROLE_CATEGORIES


@pytest.mark.parametrize("location,description,expected", [
    ("Remote, US (Remote)", "", "remote"),
    ("Illinois Remote Work, More...", "", "remote"),
    ("Virtual US", "", "remote"),
    ("Analog Design Engineer (Hybrid)", "", "hybrid"),
    ("Chicago, IL", "This is a hybrid role, 3 days in office.", "hybrid"),
    ("Chicago, IL", "This role is not remote. You will work on-site in Chicago.", "onsite"),
    ("Chicago, IL", "This is a fully remote position.", "remote"),
    ("Santa Clara, CA", "Work on-site with the silicon team.", "onsite"),
    ("Santa Clara, CA", "Build great things.", "unknown"),
    ("", None, "unknown"),
])
def test_classify_work_mode(location, description, expected):
    assert classify_work_mode(location, description) == expected


@pytest.mark.parametrize("location,expected", [
    ("US, CA, Santa Clara", Location("Santa Clara", "CA", "US")),
    ("USA, CA, Pleasanton", Location("Pleasanton", "CA", "US")),
    ("US, Oregon, Hillsboro", Location("Hillsboro", "OR", "US")),
    ("USA.VA.Reston", Location("Reston", "VA", "US")),
    ("US-CA-Menlo Park (Remote)", Location("Menlo Park", "CA", "US")),
    ("USA-SAN FRANCISCO", Location("San Francisco", None, "US")),
    ("US FL JAX 347", Location(None, "FL", "US")),
    ("Chicago, IL, US", Location("Chicago", "IL", "US")),
    ("Chicago, IL", Location("Chicago", "IL", "US")),
    ("Chicago, IL, US (Remote)", Location("Chicago", "IL", "US")),
    ("Chicago, IL, More...", Location("Chicago", "IL", "US")),
    ("San Jose, California, US", Location("San Jose", "CA", "US")),
    ("Houston, Texas, United States of America", Location("Houston", "TX", "US")),
    ("New York, NY (HQ) (Remote)", Location("New York", "NY", "US")),
    ("Remote, US (Remote)", Location(None, None, "US")),
    ("Remote - USA", Location(None, None, "US")),
    ("Virtual US", Location(None, None, "US")),
    ("Remote - Canada", Location(None, None, "Canada")),
    ("San Francisco (Remote)", Location("San Francisco", None, None)),
    ("Illinois Remote Work, More...", Location(None, "IL", "US")),
    ("Maryland, US Offsite, More...", Location(None, "MD", "US")),
    ("Helsinki, Uusimaa, Finland", Location("Helsinki", None, "Finland")),
    ("Delhi, India (Remote)", Location("Delhi", None, "India")),
    ("Atlanta, Georgia, USA; Boston, Massachusetts, USA", Location("Atlanta", "GA", "US")),
    ("Remote-Friendly (Travel Required) | San Francisco, CA", Location("San Francisco", "CA", "US")),
    ("Brussels", Location("Brussels", None, None)),
    ("Remote", Location(None, None, None)),
    ("", Location(None, None, None)),
    (None, Location(None, None, None)),
])
def test_parse_location(location, expected):
    assert parse_location(location) == expected


def test_run_classify_fills_unextracted_and_skips_llm_rows(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    conn.executemany(
        "INSERT INTO jobs (url, title, location, full_description, extracted_at, role_category, category_source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("u1", "Senior Data Engineer", "Chicago, IL, US (Remote)", "desc", None, None, None),
            ("u2", "Data Scientist", "USA.VA.Reston", "hybrid schedule", None, "other", "heuristic"),
            ("u3", "Data Analyst", "Chicago, IL", "desc", "2026-10-05", "data_science", "llm"),
            ("u4", "Warehouse Lead", None, None, None, None, None),
        ],
    )
    conn.commit()

    result = run_classify(conn)

    assert result["classified"] == 3
    rows = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT url, role_category, work_mode, location_city, location_state, location_country, category_source "
        "FROM jobs").fetchall()}
    assert rows["u1"] == ("data_engineering", "remote", "Chicago", "IL", "US", "heuristic")
    assert rows["u2"] == ("data_science", "hybrid", "Reston", "VA", "US", "heuristic")
    assert rows["u3"][0] == "data_science" and rows["u3"][-1] == "llm"  # LLM output untouched
    assert rows["u4"][0] == "other"
    described_without_category = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND role_category IS NULL").fetchone()[0]
    assert described_without_category == 0
