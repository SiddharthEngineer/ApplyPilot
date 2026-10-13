"""Tests for the extraction prompt and posting formatting (offline)."""

import json
from pathlib import Path

import pytest

from strelitzia.enrichment.posting_model import ENUMS, ROLE_CATEGORIES, JobPosting
from strelitzia.enrichment.posting_prompt import (
    MAX_DESCRIPTION_CHARS,
    build_extract_prompt,
    format_posting,
    job_id,
    truncate_description,
)

POSTINGS = Path(__file__).parent / "data" / "postings"
FIXTURES = sorted(p.stem for p in POSTINGS.glob("*.txt"))


def _fixture_job(name: str) -> dict:
    meta = json.loads((POSTINGS / f"{name}.meta.json").read_text())
    return {**meta, "full_description": (POSTINGS / f"{name}.txt").read_text()}


def test_prompt_lists_every_enum_value():
    prompt = build_extract_prompt("2026-10-05")
    for values in ENUMS.values():
        for v in values:
            assert v in prompt
    for c in ROLE_CATEGORIES:
        assert f"- {c}:" in prompt
    assert "Today is 2026-10-05" in prompt
    # The cached prefix may only change with the date.
    assert build_extract_prompt("2026-10-05") == prompt
    assert build_extract_prompt("2026-10-06").replace("2026-10-06", "2026-10-05") == prompt


def test_format_posting_has_metadata_then_description():
    for name in FIXTURES:
        job = _fixture_job(name)
        msg = format_posting(job)
        assert msg.startswith(f"TITLE: {job['title']}\n")
        assert "\nDESCRIPTION:\n" in msg and "JOB_ID" not in msg
    # Job boards are a source, not the employer.
    indeed = _fixture_job("indeed_epoch_data_scientist")
    block = format_posting(indeed)
    assert "COMPANY: not given" in block and "SOURCE: jobspy (indeed)" in block
    assert "SALARY: USD30-USD40/hourly" in block


def test_job_id_is_stable():
    assert job_id({"url": "https://x/1"}) == job_id({"url": "https://x/1"}) != job_id({"url": "https://x/2"})


def test_truncates_long_text_and_keeps_pay():
    body = "\n".join(f"Responsibility {i}: build things." for i in range(800))
    eeo = "\n".join("We are an equal opportunity employer without regard to race." for _ in range(100))
    pay = "The base salary range is $120,000 - $150,000."
    text = body + "\n" + pay + "\n" + eeo
    out = truncate_description(text)
    assert len(out) <= MAX_DESCRIPTION_CHARS
    assert pay in out
    assert "equal opportunity" not in out
    msg = format_posting({"url": "u", "title": "t", "full_description": text})
    assert len(msg) < MAX_DESCRIPTION_CHARS + 500


def test_short_text_unchanged():
    assert truncate_description("  hello\n\nworld ") == "hello\n\nworld"


def test_boilerplate_dropped_before_cutting():
    body = "x" * (MAX_DESCRIPTION_CHARS - 100)
    eeo = "Equal opportunity employer. " * 20
    out = truncate_description(body + "\n" + eeo)
    assert out == body


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_expectations_are_valid_postings(name):
    expected = json.loads((POSTINGS / f"{name}.json").read_text())
    expected.pop("_note", None)
    posting = JobPosting.from_dict(expected)
    for key, value in expected.items():
        assert getattr(posting, key) == value, key
    assert len(FIXTURES) == 6
