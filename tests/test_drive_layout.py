"""Tests for the Google Drive folder layout (google-drive-file-library Task 2)."""

from datetime import datetime

import pytest

from strelitzia.storage.drive_layout import (
    clean_name,
    drive_target,
    file_name,
    folder_path,
    job_date,
    job_key,
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
    assert path[:3] == ["Strelitzia", "Acme", "Data Engineer"]
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
    assert job_date({"tailored_at": "garbage"}, "resume") == datetime.now().astimezone().date().isoformat()
    assert job_date({}, "resume") == datetime.now().astimezone().date().isoformat()


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


# -- Per-user root folder (user-auth-6-drive Task 3) ---------------------------

@pytest.fixture
def conn(tmp_path):
    from strelitzia.database import init_db
    c = init_db(tmp_path / "layout.db")
    c.execute("INSERT INTO user_drive (user_id, token_enc) VALUES ('u1', 'x'), ('u2', 'y')")
    c.commit()
    yield c
    c.close()


def _client(fake, conn, user_id):
    from strelitzia.storage.drive import DriveClient
    return DriveClient(fake, conn=conn, user_id=user_id)


def test_root_folder_is_looked_up_by_name_once_per_user(conn, monkeypatch):
    from fake_drive import FakeDrive

    from strelitzia.storage.drive_layout import cached_root_folder_id
    monkeypatch.delenv("DRIVE_ROOT_FOLDER", raising=False)
    fake = FakeDrive()
    first = _client(fake, conn, "u1").ensure_folder_path(("Strelitzia", "Acme"))
    root = cached_root_folder_id(conn, "u1")
    assert root and fake.store[root]["name"] == "Strelitzia"
    lists = fake.calls["list"]
    # A new client (a later request) reuses the cached id: only the subfolder is searched by name.
    assert _client(fake, conn, "u1").ensure_folder_path(("Strelitzia", "Acme")) == first
    assert fake.calls["list"] == lists + 1
    assert cached_root_folder_id(conn, "u2") is None


def test_each_user_caches_their_own_root(conn):
    from fake_drive import FakeDrive

    from strelitzia.storage.drive_layout import cached_root_folder_id
    mine, theirs = FakeDrive(), FakeDrive()
    _client(mine, conn, "u1").ensure_folder_path(("Strelitzia",))
    _client(theirs, conn, "u2").ensure_folder_path(("Strelitzia",))
    assert cached_root_folder_id(conn, "u1") in mine.store
    assert cached_root_folder_id(conn, "u2") in theirs.store


def test_a_trashed_or_foreign_cached_root_is_replaced(conn):
    from fake_drive import FakeDrive

    from strelitzia.storage.drive_layout import cache_root_folder_id, cached_root_folder_id
    fake = FakeDrive()
    old = _client(fake, conn, "u1").ensure_folder_path(("Strelitzia",))
    fake.store[old]["trashed"] = True
    new = _client(fake, conn, "u1").ensure_folder_path(("Strelitzia",))
    assert new != old and cached_root_folder_id(conn, "u1") == new
    cache_root_folder_id(conn, "u1", "from-another-account")  # e.g. reconnected with a different Google account
    assert _client(fake, conn, "u1").ensure_folder_path(("Strelitzia",)) == new
    assert cached_root_folder_id(conn, "u1") == new


def test_a_renamed_root_setting_does_not_reuse_the_cache(conn, monkeypatch):
    from fake_drive import FakeDrive
    fake = FakeDrive()
    default = _client(fake, conn, "u1").ensure_folder_path(("Strelitzia",))
    monkeypatch.setenv("DRIVE_ROOT_FOLDER", "Jobs")
    assert _client(fake, conn, "u1").ensure_folder_path(("Jobs",)) != default
