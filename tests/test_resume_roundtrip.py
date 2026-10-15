"""ATS parse safety: the default template's PDF text comes back complete and in reading order."""

import re
import unicodedata

from pypdf import PdfReader

from strelitzia.scoring.pdf import render_pdf
from strelitzia.scoring.resume_model import TailoredResume
from strelitzia.scoring.template import (
    DEFAULT_TEMPLATE_PATH,
    fixed_roles_from_resume,
    parse_base_resume,
    render_resume,
    resume_to_text,
)
from workbench_helpers import BASE_RESUME, FIXED, PROFILE, good_result, library


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


def _render(tmp_path):
    lib = library()
    model = TailoredResume.from_llm_json(good_result()["resume"], lib,
                                         fixed_roles=fixed_roles_from_resume(parse_base_resume(BASE_RESUME, lib)))
    html = render_resume(model, PROFILE, template_path=DEFAULT_TEMPLATE_PATH, fixed=FIXED)
    info = render_pdf(html, str(tmp_path / "resume.pdf"))
    text = "\n".join(p.extract_text() for p in PdfReader(str(tmp_path / "resume.pdf")).pages)
    return model, html, info, text


def test_pdf_text_in_reading_order(tmp_path):
    model, html, info, text = _render(tmp_path)
    assert info["pages"] == 1
    expected = ["Alex Tester", model.headline, "Springfield, IL", "alex@example.com"]
    for role in model.roles:
        expected += [role.title, role.company, role.dates] + [b.text for b in role.bullets]
    edu = FIXED["education"][0]
    expected += ["Education", edu["heading"], edu["dates"], *edu["bullets"], "Skills"]
    expected += [f"{cat}: {items}" for cat, items in model.skills.items()]
    flat, pos = _norm(text), 0
    for item in expected:
        found = flat.find(_norm(item), pos)
        assert found >= 0, f"{item!r} missing or out of order after position {pos}:\n{text}"
        pos = found + len(_norm(item))


def test_pdf_token_recall_against_plain_text(tmp_path):
    model, _, _, text = _render(tmp_path)
    want = _tokens(resume_to_text(model, PROFILE, FIXED))
    have = set(_tokens(text))
    recall = sum(t in have for t in want) / len(want)
    assert recall >= 0.98, sorted(set(want) - have)


def test_dates_inline_and_no_layout_traps():
    template = DEFAULT_TEMPLATE_PATH.read_text()
    for trap in ("<table", "display: flex", "display:flex", "column-count", "position: absolute", "<img",
                 "@top-", "@bottom-"):
        assert trap not in template, trap
    assert "Calibri, Carlito, Arial, sans-serif" in template
    assert "{{ headline }}" in template
