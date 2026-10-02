"""Scoring and tailoring request Gemini structured output (gemini-free-tier-llm Task 3)."""

from unittest.mock import MagicMock, patch

from applypilot.scoring import scorer, tailor

JOB = {"title": "Data Engineer", "site": "acme", "location": "Remote", "full_description": "SQL"}


def test_score_job_sends_schema_and_parses_json():
    client = MagicMock()
    client.chat.return_value = '{"score": 8, "keywords": "sql, python", "reasoning": "Good fit."}'
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.score_job("resume", JOB)
    assert client.chat.call_args.kwargs["response_schema"] is scorer.SCORE_SCHEMA
    assert result == {"score": 8, "keywords": "sql, python", "reasoning": "Good fit."}


def test_score_parser_still_reads_text_format():
    parsed = scorer._parse_score_response("SCORE: 6\nKEYWORDS: a, b\nREASONING: ok")
    assert parsed == {"score": 6, "keywords": "a, b", "reasoning": "ok"}


def test_score_json_is_clamped():
    assert scorer._parse_score_response('{"score": 14, "keywords": "", "reasoning": ""}')["score"] == 10


def test_unparseable_score_is_counted(tmp_path):
    from applypilot.database import init_db
    conn = init_db(tmp_path / "t.db")
    conn.execute("INSERT INTO jobs (url, title, site, full_description) VALUES ('u', 'T', 's', 'd')")
    conn.commit()
    resume = tmp_path / "r.txt"
    resume.write_text("r")
    client = MagicMock()
    client.chat.return_value = "no idea"
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring()
    assert stats["parse_errors"] == 1


def test_resume_schema_matches_assembler_shape():
    props = tailor.RESUME_SCHEMA["properties"]
    assert set(tailor.RESUME_SCHEMA["required"]) == {"title", "summary", "skills", "experience", "projects", "education"}
    assert props["experience"]["items"]["required"] == ["header", "subtitle", "bullets"]


def test_tailor_resume_sends_schema_and_counts_json_retries():
    good = ('{"title": "Data Engineer", "summary": "s", "skills": {"Languages": "Python"}, '
            '"experience": [], "projects": [], "education": "BS"}')
    client = MagicMock()
    client.chat.side_effect = ["not json", good]
    with patch.object(tailor, "get_client", return_value=client), \
            patch.object(tailor, "validate_json_fields", return_value={"passed": True, "errors": [], "warnings": []}):
        _, report = tailor.tailor_resume("resume", JOB, {}, validation_mode="lenient")
    assert all(c.kwargs["response_schema"] is tailor.RESUME_SCHEMA for c in client.chat.call_args_list)
    assert report["json_retries"] == 1
    assert report["status"] == "approved"
