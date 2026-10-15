"""Paste-back: extracting the JSON, the deterministic checks, and saving a draft."""

import copy
import json
from pathlib import Path

import pytest

from strelitzia import tracking
from strelitzia.database import init_db
from strelitzia.scoring import pdf as pdf_mod
from strelitzia.scoring import workbench as wb
from workbench_helpers import add_job_row, fake_render_pdf, good_result, inputs, reply


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "wb.db")


@pytest.fixture(autouse=True)
def _no_chromium(monkeypatch):
    monkeypatch.setattr(pdf_mod, "render_pdf", fake_render_pdf)


def _import(conn, tmp_path, data_or_text, **job_overrides):
    job = add_job_row(conn, **job_overrides) if not conn.execute("SELECT 1 FROM jobs").fetchone() else \
        dict(conn.execute("SELECT * FROM jobs").fetchone())
    text = data_or_text if isinstance(data_or_text, str) else reply(data_or_text)
    return wb.import_result(conn, job, text, inputs=inputs(), out_dir=tmp_path / "out")


# ── extract_result ──

def test_extract_takes_the_last_json_block_with_prose_around():
    old, new = good_result(), good_result(headline="Second version")
    text = "First try:\n```json\n" + json.dumps(old) + "\n```\nThen I changed it.\n" + reply(new) + "\nDone!"
    assert wb.extract_result(text)["resume"]["headline"] == "Second version"


def test_extract_accepts_bare_json():
    assert wb.extract_result(json.dumps(good_result()))["strelitzia"] == 1


def test_extract_errors_are_clear():
    with pytest.raises(ValueError, match="No ```json block"):
        wb.extract_result("Sure, here is your resume!")
    with pytest.raises(ValueError, match="isn't valid JSON"):
        wb.extract_result('```json\n{"strelitzia": 1,,}\n```')
    with pytest.raises(ValueError, match='missing "resume"'):
        wb.extract_result('```json\n{"strelitzia": 1, "cover_letter": null}\n```')
    with pytest.raises(ValueError, match="Nothing was pasted"):
        wb.extract_result("  ")


# ── checks ──

def test_invalid_json_gives_errors_and_fix_prompt(conn, tmp_path):
    out = _import(conn, tmp_path, "```json\n{not json}\n```")
    assert not out.ok and "isn't valid JSON" in out.errors[0]
    assert out.fix_prompt.startswith("These problems were found:\n1. ")
    assert out.fix_prompt.endswith("Keep everything else the same and reply with the corrected JSON.")


def test_unevidenced_skill_in_a_bullet_is_an_error(conn, tmp_path):
    data = good_result()
    data["resume"]["roles"][0]["bullets"][0]["text"] = "Deployed the orders pipeline on Kubernetes with Airflow"
    out = _import(conn, tmp_path, data)
    assert not out.ok
    assert any("'Kubernetes' is not backed" in e and "data-engineer-acme bullet 1" in e for e in out.errors)
    assert "Kubernetes" in out.fix_prompt
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
    assert conn.execute("SELECT draft_version FROM jobs").fetchone()[0] is None


def test_unevidenced_skill_in_skills_and_headline(conn, tmp_path):
    data = good_result(headline="Terraform data engineer",
                       skills=[{"category": "Tools", "items": "Airflow, Kubernetes"}])
    out = _import(conn, tmp_path, data)
    assert any("'Kubernetes'" in e and "Skills (Tools)" in e for e in out.errors)
    assert any("'Terraform'" in e and "headline" in e for e in out.errors)
    assert any("Skill 'Kubernetes' (Tools) is not in your content library" in e for e in out.errors)


def test_watchlist_term_in_a_bullet_is_an_error(conn, tmp_path):
    data = good_result()
    data["resume"]["roles"][1]["bullets"][0]["text"] = "Rewrote the churn model in Scala"
    out = _import(conn, tmp_path, data)
    assert any("'scala' is not in your content library" in e for e in out.errors)


def test_number_not_in_the_facts_is_an_error(conn, tmp_path):
    data = good_result()
    data["resume"]["roles"][0]["bullets"][0]["text"] = "Cut pipeline runtime by 95% with Airflow"
    out = _import(conn, tmp_path, data)
    assert any("Number(s) 95 not in the facts of orders-pipeline" in e for e in out.errors)


def test_cover_letter_checks(conn, tmp_path):
    data = good_result()
    data["cover_letter"]["paragraphs"] = data["cover_letter"]["paragraphs"][:2] + [
        "I also ran Kubernetes for 7 teams, and I am excited to join."]
    out = _import(conn, tmp_path, data)
    joined = "\n".join(out.errors)
    assert "Cover letter number(s) 7" in joined
    assert "Cover letter mentions 'Kubernetes'" in joined
    assert "Banned words: i am excited" in joined


def test_number_in_the_headline_must_be_known(conn, tmp_path):
    out = _import(conn, tmp_path, good_result(headline="Data engineer with 10 years leading a team of 37"))
    assert any("Number(s) 10, 37 in the headline" in e for e in out.errors), out.errors
    assert _import(conn, tmp_path, good_result(headline="Data engineer moving 40 million rows a day")).ok


def test_unknown_tool_in_parentheses_in_skills_is_an_error(conn, tmp_path):
    data = good_result()
    data["resume"]["skills"][0]["items"] = "Python (PyTorch), SQL"
    out = _import(conn, tmp_path, data)
    assert any("Skill 'Python (PyTorch)'" in e for e in out.errors)
    data["resume"]["skills"][0]["items"] = "Python (scikit-learn), SQL"
    assert _import(conn, tmp_path, data).ok


def test_cover_letter_may_use_numbers_from_the_posting(conn, tmp_path):
    data = good_result()
    data["cover_letter"]["paragraphs"][2] = ("Your platform serves 20 million riders, and I have built on a customer "
                                             "data platform before. Happy to walk through any of this.")
    assert not _import(conn, tmp_path, data).ok
    conn.execute("UPDATE jobs SET full_description = full_description || ' Our platform serves 20 million riders.'")
    conn.commit()
    out = _import(conn, tmp_path, data)
    assert out.ok, out.errors


def test_jobs_with_the_same_site_and_title_keep_separate_files(conn, tmp_path):
    a = _import(conn, tmp_path, good_result())
    b_job = add_job_row(conn, url="https://example.com/jobs/43", job_key="fed987cba654")
    b = wb.import_result(conn, b_job, reply(good_result(headline="Data engineer for Airflow pipelines")),
                         inputs=inputs(), out_dir=tmp_path / "out")
    assert a.ok and b.ok
    assert a.files["resume"] != b.files["resume"] and a.files["resume_docx"] != b.files["resume_docx"]
    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM jobs").fetchall()}
    first = wb.current_result(rows["https://example.com/jobs/42"])
    assert first["resume"]["headline"] == "Data engineer building Airflow and Spark pipelines"


def test_slug_role_count_wording_checks(conn, tmp_path):
    data = good_result()
    data["resume"]["roles"][0]["bullets"].append(
        {"text": "Ran the pipeline — every night, a robust setup " + "x" * 220, "project_ids": ["orders-pipeline"]})
    out = _import(conn, tmp_path, data)
    joined = "\n".join(out.errors)
    assert "Role 'data-engineer-acme' has 3 bullets; it needs 1-2" in joined
    assert "Em or en dash" in joined and "Banned words in data-engineer-acme bullet 3: robust" in joined
    assert "at most 230" in joined
    bad_slug = good_result()
    bad_slug["resume"]["roles"][1]["bullets"][0]["project_ids"] = ["orders-pipeline"]
    out = wb.import_result(conn, dict(conn.execute("SELECT * FROM jobs").fetchone()), reply(bad_slug),
                           inputs=inputs(), out_dir=tmp_path / "out")
    assert "does not belong to role 'data-analyst-intern-globex'" in out.errors[0]


# ── success ──

def test_valid_result_writes_files_and_bumps_the_version(conn, tmp_path):
    out = _import(conn, tmp_path, good_result())
    assert out.ok, out.errors
    assert out.draft_version == 1
    assert Path(out.files["resume"]).exists()
    prefix = Path(out.files["resume"]).with_suffix("")
    for suffix in (".json", "_RESULT.json", "_REPORT.json", ".txt"):
        assert Path(f"{prefix}{suffix}").exists(), suffix
    sidecar = json.loads(Path(f"{prefix}.json").read_text())
    assert sidecar["headline"] == "Data engineer building Airflow and Spark pipelines"
    assert sidecar["job_url"] == "https://example.com/jobs/42"
    assert out.unmet == ["Kubernetes", "Terraform"]
    assert "Airflow" in out.coverage["covered"] and "Kubernetes" in out.coverage["missing"]
    assert out.coverage["pct_required"] == 80.0

    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["draft_version"] == 1 and row["draft_rules_version"] == wb.RULES_VERSION
    assert row["tailored_resume_path"] == out.files["resume"] and row["tailored_at"]
    assert json.loads(row["tailor_report"])["unmet"] == ["Kubernetes", "Terraform"]
    assert row["resume_docx_path"] == out.files["resume_docx"] == f"{prefix}.docx"
    assert Path(out.files["resume_docx"]).exists()
    assert row["cover_letter_path"] == out.files["cover_letter"] and row["cover_letter_at"]
    assert Path(out.files["cover_letter"]).exists() and Path(out.files["cover_letter"]).with_suffix(".txt").exists()
    assert not list((tmp_path / "out").glob("*.staging.*"))
    assert wb.current_result(row)["resume"]["headline"] == sidecar["headline"]

    second = good_result(headline="Data engineer for Airflow pipelines")
    out2 = wb.import_result(conn, row, reply(second), inputs=inputs(), out_dir=tmp_path / "out")
    assert out2.ok and out2.draft_version == 2
    assert conn.execute("SELECT draft_version FROM jobs").fetchone()[0] == 2


def test_dropped_bullets_become_warnings(conn, tmp_path, monkeypatch):
    calls = []

    def overflow_once(html, output_path):
        calls.append(1)
        info = fake_render_pdf(html, output_path)
        return {**info, "overflow": len(calls) == 1, "pages": 2 if len(calls) == 1 else 1}

    monkeypatch.setattr(pdf_mod, "render_pdf", overflow_once)
    out = _import(conn, tmp_path, good_result())
    assert out.ok and any(w.startswith("Dropped to fit one page:") for w in out.warnings)


def test_still_overflowing_is_an_error(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_mod, "render_pdf", lambda html, p: {**fake_render_pdf(html, p), "overflow": True})
    out = _import(conn, tmp_path, good_result())
    assert not out.ok and "longer than one page" in out.errors[0] and out.fix_prompt


def test_submitted_records_draft_and_rules_version(conn, tmp_path):
    out = _import(conn, tmp_path, copy.deepcopy(good_result()))
    assert out.ok
    tracking.set_status(conn, "https://example.com/jobs/42", "submitted")
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["submitted_draft_version"] == 1 and row["submitted_rules_version"] == wb.RULES_VERSION
