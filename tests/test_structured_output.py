"""Scoring requests Claude structured output (JSON Schema via output_config.format)."""

import json
from unittest.mock import MagicMock, patch

from strelitzia.scoring import assessment, scorer
from strelitzia.scoring.preferences import Preferences
from fit_helpers import add_extracted_job, reply

JOB = {"title": "Data Engineer", "site": "acme", "location": "Remote", "full_description": "SQL"}


def test_assess_job_sends_schema_and_parses_json():
    client = MagicMock()
    client.chat.return_value = reply(skills=4)
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.assess_job(Preferences(), "resume", JOB)
    assert client.chat.call_args.kwargs["schema"] is assessment.ASSESS_SCHEMA
    assert result["assessment"]["skills_match"] == {"score": 4, "evidence": "Has SQL and Python."}
    assert result["assessment"]["matched_keywords"] == ["SQL", "Python"]


def test_assessment_parser_reads_only_json():
    assert assessment.parse_assessment(reply()) is not None
    # The old 1-10 score format is no longer accepted.
    assert assessment.parse_assessment('{"score": 6, "keywords": "a, b", "reasoning": "ok"}') is None
    assert assessment.parse_assessment("SKILLS: 4") is None


def test_schemas_are_valid_for_structured_outputs():
    from schema_rules import assert_strict_schema
    assert_strict_schema(assessment.ASSESS_SCHEMA)


def test_resume_goes_in_the_cached_system_prompt():
    client = MagicMock()
    client.chat.return_value = reply()
    with patch.object(scorer, "get_client", return_value=client):
        scorer.assess_job(Preferences(), "MY RESUME", {**JOB, "url": "https://x/1"})
    rubric, candidate, user = client.chat.call_args.args[0]
    assert rubric == {"role": "system", "content": assessment.ASSESS_PROMPT}
    assert candidate["role"] == "system" and "MY RESUME" in candidate["content"]
    assert "MY RESUME" not in user["content"]
    assert user["content"].startswith("POSTING (extracted):") and "Data Engineer" in user["content"]
    assert client.chat.call_args.kwargs["job_url"] == "https://x/1"
    assert "temperature" not in client.chat.call_args.kwargs


def test_ratings_are_clamped():
    data = json.loads(reply())
    data["skills_match"]["score"] = 14
    data["experience_match"]["score"] = 0
    parsed = assessment.parse_assessment(json.dumps(data))
    assert parsed["skills_match"]["score"] == 5 and parsed["experience_match"]["score"] == 1


def test_unparseable_score_is_counted(tmp_path):
    from strelitzia.database import init_db
    conn = init_db(tmp_path / "t.db")
    add_extracted_job(conn, "u", title="T")
    resume = tmp_path / "r.txt"
    resume.write_text("r")
    client = MagicMock()
    client.chat.return_value = "no idea"
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "load_resume_text", return_value=resume.read_text()), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring(prefilter=False)
    assert stats["parse_errors"] == 1
