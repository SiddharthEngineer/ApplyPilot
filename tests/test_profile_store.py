"""The per-user profile store (strelitzia.profile) and the one-time import of the owner's files. Fake data only."""

import json

import pytest
import yaml

from strelitzia import config, users
from strelitzia import profile as store
from strelitzia.database import close_connection, init_db
from strelitzia.profile import UserProfile

FAKE_PROFILE = {
    "personal": {"full_name": "Ada Example Tester", "preferred_name": "Ada", "email": "ada@example.com",
                 "phone": "555-000-0000", "city": "Springfield", "province_state": "ST", "country": "Nowhere",
                 "linkedin_url": "https://www.linkedin.com/in/example", "github_url": "https://github.com/example"},
    "experience": {"years_of_experience_total": "4", "education_level": "Master's Degree",
                   "current_job_title": "Widget Engineer", "target_role": "widget engineer"},
    "skills_boundary": {"languages": ["Python", "SQL"], "tools": ["Git", "Python"]},
    "resume_facts": {"preserved_companies": ["Acme Widgets"]},
    "work_authorization": {"legally_authorized_to_work": "Yes"},
}
FAKE_FIXED = {"education": [{"heading": "BSc Widgetry, Example University", "dates": "2015 - 2019"}]}


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The app-dir files, redirected to tmp_path (none exist until a test writes them)."""
    for name, attr in (("profile.json", "PROFILE_PATH"), ("resume.txt", "RESUME_PATH"),
                       ("resume.pdf", "RESUME_PDF_PATH"), ("content_library.md", "CONTENT_LIBRARY_PATH"),
                       ("resume_fixed.yaml", "RESUME_FIXED_PATH")):
        monkeypatch.setattr(config, attr, tmp_path / "app" / name)
    (tmp_path / "app").mkdir()
    return tmp_path / "app"


@pytest.fixture
def conn(files, db_target):
    c = init_db(db_target)
    yield c
    close_connection(db_target)


@pytest.mark.parametrize("db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True)
def test_round_trip(conn):
    uid = users.invite(conn, "someone@example.com").id
    assert store.load_profile(conn, uid) == UserProfile()
    p = UserProfile(first_name="Bo", last_name="Sample", email="bo@example.com", location="Town, ST",
                    years_experience=2.5, education_level="bachelors", skills=["Rust", "Go"],
                    extras={"resume_facts": {"real_metrics": ["2x"]}},
                    resume_text="Bo Sample\nresume", content_library_md="## CURRENT ROLE\n")
    store.save_profile(conn, uid, p)
    store.save_resume_file(conn, uid, "bo.pdf", store.PDF_MIME, b"%PDF-1.4 \x00\xff", "Bo Sample\nfrom the pdf")
    got = store.load_profile(conn, uid)
    assert got.resume_file == b"%PDF-1.4 \x00\xff"
    assert (got.resume_file_name, got.resume_mime, got.resume_text) == ("bo.pdf", store.PDF_MIME,
                                                                         "Bo Sample\nfrom the pdf")
    assert got.data() == p.data() and got.content_library_md == p.content_library_md
    # Saving the fields again keeps the file; another user's profile is untouched.
    store.save_profile(conn, uid, got)
    assert store.load_profile(conn, uid).resume_file == b"%PDF-1.4 \x00\xff"
    assert store.load_profile(conn, users.owner(conn).id) == UserProfile()


def test_unknown_values_are_dropped():
    p = UserProfile.from_data({"education_level": "unknown", "years_experience": "about 3", "skills": "x",
                               "extras": [], "nonsense": 1})
    assert (p.education_level, p.years_experience, p.skills, p.extras) == (None, 3.0, [], {})


def test_as_profile_json_has_the_profile_json_shape():
    p = store.from_profile_json(FAKE_PROFILE)
    out = p.as_profile_json()
    assert out["personal"]["full_name"] == "Ada Example Tester"
    assert out["personal"]["preferred_name"] == "Ada" and out["personal"]["country"] == "Nowhere"
    assert out["personal"]["city"] == "Springfield, ST"
    assert out["experience"]["target_role"] == "widget engineer"
    assert out["experience"]["years_of_experience_total"] == "4"
    assert out["resume_facts"] == FAKE_PROFILE["resume_facts"]
    assert sorted(s for v in out["skills_boundary"].values() for s in v) == ["Git", "Python", "SQL"]
    assert "resume_fixed" not in UserProfile(extras={"resume_fixed": FAKE_FIXED}).as_profile_json()


@pytest.mark.parametrize("db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True)
def test_import_owner_files(files, db_target):
    (files / "profile.json").write_text(json.dumps(FAKE_PROFILE), encoding="utf-8")
    (files / "resume.txt").write_text("Ada Example Tester\nada@example.com\n", encoding="utf-8")
    (files / "resume.pdf").write_bytes(b"%PDF-fake")
    (files / "content_library.md").write_text("## CURRENT ROLE: Widget Engineer\n", encoding="utf-8")
    (files / "resume_fixed.yaml").write_text(yaml.safe_dump(FAKE_FIXED), encoding="utf-8")
    conn = init_db(db_target)  # the import runs on start
    try:
        p = store.load_profile(conn)
        assert (p.first_name, p.last_name, p.email) == ("Ada", "Example Tester", "ada@example.com")
        assert (p.location, p.current_job_title, p.years_experience) == ("Springfield, ST", "Widget Engineer", 4.0)
        assert p.education_level == "masters" and p.skills == ["Python", "SQL", "Git"]
        assert p.extras["resume_fixed"] == FAKE_FIXED
        assert p.extras["work_authorization"] == FAKE_PROFILE["work_authorization"]
        assert p.resume_text.startswith("Ada Example Tester") and p.content_library_md.startswith("## CURRENT")
        assert (p.resume_file, p.resume_file_name) == (b"%PDF-fake", "resume.pdf")
        # Only once: a later edit of the file doesn't overwrite the DB copy.
        (files / "resume.txt").write_text("changed", encoding="utf-8")
        assert store.import_owner_files(conn) is False
        assert store.load_profile(conn).resume_text.startswith("Ada Example Tester")
    finally:
        close_connection(db_target)


@pytest.mark.parametrize("db_target", ["sqlite"], indirect=True)
def test_no_files_no_import(conn):
    assert store.import_owner_files(conn) is False
    assert not store.has_profile(conn, users.owner(conn).id)
