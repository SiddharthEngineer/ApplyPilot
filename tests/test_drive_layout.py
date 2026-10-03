"""Tests for the Google Drive folder layout (google-drive-file-library Task 2)."""

from datetime import date

import pytest

from applypilot.storage.drive_layout import (
    clean_name, drive_target, file_name, folder_path, job_date, job_key,
)

JOB = {
    "url": "https://example.com/jobs/1", "title": "Data Engineer", "site": "indeed", "company": "Acme",
    "tailored_at": "2026-09-30T12:00:00+00:00", "cover_letter_at": None,
}


@pytest.mark.parametrize("raw, expected", [
    ("Software Engineer / ML", "Software Engineer ML"),
    ('C:\\temp*?"<>|x', "C temp x"),
    ("  lots   of\tspace  ", "lots of space"),
    ("", "Untitled"),
    (None, "Untitled"),
    ("///", "Untitled"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_clean_name_truncates():
    assert len(clean_name("x" * 300)) == 80
    assert clean_name("abc def", max_len=4) == "abc"


def test_folder_path():
    path = folder_path(JOB, "resume")
    assert path[:3] == ["ApplyPilot", "Acme", "Data Engineer"]
    assert len(path) == 4 and len(path[3]) == 10


def test_folder_path_unknown_company():
    job = dict(JOB, company=None)
    assert folder_path(job, "resume")[1] == "Unknown company"
    job = dict(JOB, company=None, site="NVIDIA")
    assert folder_path(job, "resume")[1] == "NVIDIA"


def test_root_override(monkeypatch):
    monkeypatch.setenv("DRIVE_ROOT_FOLDER", "Job Search/2026")
    assert folder_path(JOB, "resume")[0] == "Job Search 2026"


def test_job_date_fallbacks():
    assert job_date({"tailored_at": "2026-09-30T12:00:00"}, "resume") == "2026-09-30"
    assert job_date({"tailored_at": "2026-09-30T12:00:00", "cover_letter_at": "2026-10-01T12:00:00"},
                    "cover_letter") == "2026-10-01"
    assert job_date({"tailored_at": "2026-09-30T12:00:00"}, "cover_letter") == "2026-09-30"
    assert job_date({"tailored_at": "garbage"}, "resume") == date.today().isoformat()
    assert job_date({}, "resume") == date.today().isoformat()


def test_file_name():
    assert file_name(JOB, "resume") == "Acme - Data Engineer - Resume.pdf"
    assert file_name(JOB, "cover_letter", suffix="abc123") == "Acme - Data Engineer - Cover Letter (abc123).pdf"


def test_target_is_deterministic():
    a, b = drive_target(dict(JOB), "resume"), drive_target(dict(JOB), "resume")
    assert a == b
    assert a.job_key == job_key(JOB["url"]) and len(a.job_key) == 12
    assert a.site == "indeed"
    assert a.collision_name() == f"Acme - Data Engineer - Resume ({a.job_key[:6]}).pdf"


def test_different_urls_different_keys():
    assert job_key("https://a") != job_key("https://b")


def test_unknown_kind():
    with pytest.raises(ValueError):
        drive_target(JOB, "transcript")
