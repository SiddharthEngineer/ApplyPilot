"""Resume upload parsing (strelitzia.scoring.resume_parse): text from a PDF or DOCX, and the draft from a mocked Claude
answer. Fake data only."""

import json
from unittest.mock import MagicMock, patch

import pytest
from resume_files import make_docx, make_pdf

from strelitzia import users
from strelitzia.database import close_connection, init_db
from strelitzia.llm import LLMStopRun
from strelitzia.scoring import resume_parse
from strelitzia.scoring.resume_parse import DOCX_MIME, PARSE_SCHEMA, extract_text, parse_resume

LINES = ["Ada Example", "ada@example.com | 555-000-0000 | Springfield, ST", "Widget Engineer, Acme Widgets"]


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "jobs.db")
    yield c
    close_connection()


class TestExtractText:
    def test_pdf(self):
        text = extract_text("cv.pdf", "application/pdf", make_pdf(LINES))
        assert text.splitlines() == LINES

    def test_docx_by_extension_or_mime(self):
        data = make_docx(LINES)
        assert extract_text("cv.docx", None, data).splitlines() == LINES
        assert extract_text("upload", DOCX_MIME, data).splitlines() == LINES

    @pytest.mark.parametrize("name,mime,data", [
        ("cv.txt", "text/plain", b"Ada Example"),
        ("cv.doc", "application/msword", b"\xd0\xcf\x11\xe0"),
        ("cv.pdf", "application/pdf", b"%PDF-1.4 not really"),
        ("cv.docx", DOCX_MIME, b"PK not a zip"),
        ("cv.pdf", "application/pdf", make_pdf([])),  # no text layer
        ("cv.docx", DOCX_MIME, make_docx(["", "  "])),
    ])
    def test_anything_else_is_a_value_error(self, name, mime, data):
        with pytest.raises(ValueError):
            extract_text(name, mime, data)


def _parse(reply: dict | str, conn, user_id=None):
    client = MagicMock()
    client.chat.return_value = reply if isinstance(reply, str) else json.dumps(reply)
    with patch.object(resume_parse, "get_client", return_value=client) as get_client:
        draft = parse_resume("Ada Example\n...", user_id, conn=conn)
    get_client.assert_called_once_with("profile_parse")
    return draft, client


class TestParseResume:
    def test_returns_only_the_stated_fields(self, conn):
        draft, client = _parse({
            "first_name": "Ada", "last_name": "Example", "phone": "555-000-0000", "email": " ada@example.com ",
            "linkedin_url": None, "github_url": "", "location": "Springfield, ST",
            "current_job_title": "Widget Engineer", "years_experience": 4, "education_level": "masters",
            "skills": ["Python", "SQL", "python", " "],
        }, conn)
        assert draft == {"first_name": "Ada", "last_name": "Example", "phone": "555-000-0000",
                         "email": "ada@example.com", "location": "Springfield, ST",
                         "current_job_title": "Widget Engineer", "years_experience": 4.0,
                         "education_level": "masters", "skills": ["Python", "SQL"]}
        kwargs = client.chat.call_args.kwargs
        assert kwargs["schema"] is PARSE_SCHEMA
        messages = client.chat.call_args.args[0]
        assert messages[0]["role"] == "system" and "Ada Example" in messages[1]["content"]

    def test_bad_values_are_dropped(self, conn):
        draft, _ = _parse({"first_name": 3, "years_experience": "eight", "education_level": "wizard",
                           "skills": "Python"}, conn)
        assert draft == {}
        assert _parse({"years_experience": True}, conn)[0] == {}
        assert _parse({"years_experience": 500}, conn)[0] == {}

    def test_a_non_object_answer_is_a_value_error(self, conn):
        with pytest.raises(ValueError):
            _parse("[1, 2]", conn)

    def test_the_schema_offers_the_profile_education_levels(self):
        levels = PARSE_SCHEMA["properties"]["education_level"]["anyOf"][0]["enum"]
        assert "unknown" not in levels and "bachelors" in levels
        assert set(PARSE_SCHEMA["required"]) == set(PARSE_SCHEMA["properties"])

    def test_over_the_users_cap_nothing_is_sent(self, conn):
        uid = users.invite(conn, "capped@example.com", cap=0.0).id
        client = MagicMock()
        with patch.object(resume_parse, "get_client", return_value=client), pytest.raises(LLMStopRun) as e:
            parse_resume("Ada Example", uid, conn=conn)
        assert e.value.reason == "budget" and e.value.user_id == uid
        client.chat.assert_not_called()

    def test_the_call_is_billed_to_the_user(self, conn):
        uid = users.invite(conn, "bo@example.com").id
        seen = []
        client = MagicMock()

        def chat(*args, **kwargs):
            from strelitzia.llm import current_user
            seen.append(current_user())
            return "{}"
        client.chat.side_effect = chat
        with patch.object(resume_parse, "get_client", return_value=client):
            assert parse_resume("Bo", uid, conn=conn) == {}
        assert seen == [uid]


def test_a_broken_pdf_of_any_kind_is_a_value_error():
    with patch("pypdf.PdfReader", side_effect=IndexError("list index out of range")), pytest.raises(ValueError):
        extract_text("cv.pdf", "application/pdf", b"%PDF-1.4")
