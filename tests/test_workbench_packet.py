"""The workbench's output contract, the fixed docs and the per-job packet."""

import copy
import json

from strelitzia.scoring import workbench as wb
from workbench_helpers import BASE_RESUME, good_result, job, library


def test_schema_accepts_a_sample_result():
    assert wb.schema_errors(good_result(), wb.RESULT_SCHEMA) == []
    assert wb.schema_errors(good_result(), wb.result_schema(library())) == []


def test_schema_accepts_null_cover_letter_and_no_notes():
    data = good_result()
    data["cover_letter"] = None
    del data["notes_for_user"]
    assert wb.schema_errors(data, wb.RESULT_SCHEMA) == []


def test_schema_errors_name_the_problem():
    data = good_result()
    data["resume"]["headline"] = "x" * 111
    del data["resume"]["skills"]
    data["extra"] = 1
    data["resume"]["roles"][0]["bullets"][0]["project_ids"] = ["nope"]
    errors = wb.schema_errors(data, wb.result_schema(library()))
    assert any("headline" in e and "at most 110" in e for e in errors)
    assert any('missing "skills"' in e for e in errors)
    assert any('unexpected key "extra"' in e for e in errors)
    assert any('"nope" is not allowed' in e for e in errors)


def test_schema_rejects_wrong_version_and_bool_as_int():
    data = good_result()
    data["strelitzia"] = True
    assert wb.schema_errors(data, wb.RESULT_SCHEMA)


def test_rules_doc_covers_the_rules_and_schema():
    html = wb.rules_doc()
    for phrase in ("every evidenced required qualification", "most recent role", "exact term",
                   "Never mention an unevidenced", "No keyword lists", "No hidden text", "230 characters",
                   "3 paragraphs", "250 words", "```json", "spearheaded", "&quot;strelitzia&quot;"):
        assert phrase in html, phrase
    assert len(wb.RULES_VERSION) == 8 and wb.RULES_VERSION in wb.job_packet(job(), library(), BASE_RESUME).text


def test_library_doc_shows_slugs_and_base_resume():
    html = wb.library_doc(library(), BASE_RESUME)
    assert "Project slug: orders-pipeline" in html and "Role data-engineer-acme" in html
    assert "Data Analyst Intern at Globex" in html


def test_packet_lists_targets_and_unmet():
    p = wb.job_packet(job(), library(), BASE_RESUME)
    assert p.name == "Initech – Senior Data Engineer – Claude packet"
    assert "Required qualifications" in p.text and "Preferred qualifications" in p.text
    assert "Airflow: evidenced (projects orders-pipeline; base resume data-engineer-acme, skills)" in p.text
    assert "Kubernetes: UNEVIDENCED, do not claim" in p.text
    unmet = p.text.split("## Not claimable (unevidenced)")[1].split("##")[0]
    assert "- Kubernetes" in unmet and "- Terraform" in unmet and "Airflow" not in unmet
    assert "data-engineer-acme: Data Engineer at Acme Corp (Jan 2024 – Present); 1-2 bullets, required" in p.text
    assert "projects: orders-pipeline, quality-dashboard" in p.text
    assert "Customer Data Platform (CDP)" in p.text
    assert "<h2>Required qualifications</h2>" in p.html


def test_drive_prompt_is_short_and_names_the_doc():
    p = wb.job_packet(job(), library(), BASE_RESUME, inline=False)
    assert len(p.prompt) <= wb.PROMPT_MAX
    assert "'Initech – Senior Data Engineer – Claude packet'" in p.prompt
    long = wb.job_packet(job(title="Very long title " * 30), library(), BASE_RESUME, current=good_result(),
                         inline=False)
    assert len(long.prompt) <= wb.PROMPT_MAX and "current draft" in long.prompt


def test_inline_prompt_carries_packet_rules_and_library():
    p = wb.job_packet(job(), library(), BASE_RESUME)
    assert p.text in p.prompt
    assert "Strelitzia rules" in p.prompt and "Project slug: churn-model" in p.prompt


def test_packet_includes_current_draft():
    current = good_result()
    p = wb.job_packet(job(), library(), BASE_RESUME, current=copy.deepcopy(current))
    assert "## Current draft" in p.text
    assert json.dumps(current["resume"]["headline"]) in p.text


def test_packet_without_posting_details():
    p = wb.job_packet(job(details_json=None), library(), "")
    assert "None listed in the posting." in p.text
