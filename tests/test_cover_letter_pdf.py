"""The cover letter renders through its own HTML template to a real PDF."""

from datetime import datetime

from pypdf import PdfReader

from strelitzia.scoring import cover_letter
from strelitzia.scoring.validator import validate_visible
from workbench_helpers import PROFILE, good_result, job


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_cover_pdf_has_greeting_paragraphs_and_sign_off(tmp_path):
    paragraphs = good_result()["cover_letter"]["paragraphs"]
    path = cover_letter.render_pdf(paragraphs, PROFILE, job(), tmp_path / "cl.pdf")
    assert path == tmp_path / "cl.pdf" and isinstance(path, type(tmp_path))
    text = _norm(" ".join(p.extract_text() for p in PdfReader(str(path)).pages))
    pos = 0
    for part in ["Dear Hiring Manager,", *paragraphs, "Alex Tester"]:
        found = text.find(_norm(part), pos)
        assert found >= 0, f"{part!r} missing or out of order in {text!r}"
        pos = found + len(_norm(part))
    assert "Initech" in text


def test_letter_html_is_clean_and_dated():
    html = cover_letter.letter_html(["One.", "Two.", "Three."], PROFILE, job(), today=datetime(2026, 10, 5))
    assert "October 5, 2026" in html and "Dear Hiring Manager," in html
    assert validate_visible(html) == []


def test_letter_text_and_preferred_sign_off():
    profile = {"personal": {"full_name": "Alexandra Tester", "preferred_name": "Alex"}}
    text = cover_letter.letter_text(["One.", " ", "Two."], profile)
    assert text == "Dear Hiring Manager,\n\nOne.\n\nTwo.\n\nAlex\n"
