"""Google Docs for the Claude Project (rules, content library) and the per-job packet, against a fake Drive."""

import pytest
from fake_drive import FakeDrive, fake_media

from strelitzia import users
from strelitzia.database import init_db, migrate_users, set_state
from strelitzia.scoring import workbench as wb
from strelitzia.storage.drive import DOC_MIME, DriveClient
from workbench_helpers import add_job_row, inputs


def _html_media(html: str) -> bytes:
    return html.encode("utf-8")


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def client(drive):
    return DriveClient(drive, media_factory=fake_media, html_media_factory=_html_media)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "wb.db")


@pytest.fixture(autouse=True)
def _root(monkeypatch):
    monkeypatch.setenv("DRIVE_ROOT_FOLDER", "Strelitzia")


def _docs(drive):
    return [f for f in drive.store.values() if f["mimeType"] == DOC_MIME]


def test_upsert_doc_creates_a_google_doc_then_updates_it(drive, client):
    folder = client.ensure_folder_path(("Strelitzia", "_Claude"))
    first = client.upsert_doc(folder, "Strelitzia rules", "<p>one</p>")
    assert first.created and first.url
    (doc,) = _docs(drive)
    assert doc["name"] == "Strelitzia rules" and doc["parents"] == [folder] and doc["content"] == b"<p>one</p>"

    second = client.upsert_doc(folder, "Strelitzia rules", "<p>two</p>")
    assert not second.created and second.id == first.id
    assert drive.calls["update"] == 1 and len(_docs(drive)) == 1
    assert drive.store[first.id]["content"] == b"<p>two</p>"


def test_upsert_doc_prefers_the_known_id_and_skips_trashed(drive, client):
    folder = client.ensure_folder_path(("Strelitzia", "Acme"))
    first = client.upsert_doc(folder, "Old name", "<p>a</p>")
    again = client.upsert_doc(folder, "New name", "<p>b</p>", known_id=first.id)
    assert again.id == first.id and not again.created

    drive.store[first.id]["trashed"] = True
    fresh = client.upsert_doc(folder, "New name", "<p>c</p>", known_id=first.id)
    assert fresh.created and fresh.id != first.id


def test_static_docs_are_written_once_and_skip_unchanged(drive, client, conn):
    urls = wb.publish_static_docs(client, conn, users.owner(conn).id, inputs())
    assert set(urls) == {"rules", "library", "panel_rubric"} and all(urls.values())
    docs = {d["name"]: d for d in _docs(drive)}
    assert set(docs) == {"Strelitzia rules", "Strelitzia content library", "Strelitzia panel rubric"}
    for d in docs.values():
        assert drive.path_of(d["id"]) == ["Strelitzia", "_Claude", d["name"]]
    assert b"notes_for_user" in docs["Strelitzia rules"]["content"]  # the schema is in the rules Doc
    assert b"orders-pipeline" in docs["Strelitzia content library"]["content"]
    assert b"not_met" in docs["Strelitzia panel rubric"]["content"]

    calls = dict(drive.calls)
    assert wb.publish_static_docs(client, conn, users.owner(conn).id, inputs()) == urls
    assert drive.calls["create"] == calls["create"] and drive.calls["update"] == calls.get("update", 0)

    state = wb.published_static_docs(conn, users.owner(conn).id)
    assert state["rules"]["url"] == urls["rules"] and state["rules"]["changed_at"]


def test_static_docs_are_per_user_and_the_old_ones_are_the_owners(drive, client, conn):
    set_state(conn, "claude_doc:rules", '{"id": "old", "url": "u", "hash": "h"}')
    conn.commit()
    migrate_users(conn)
    migrate_users(conn)  # idempotent
    me, other = users.owner(conn).id, users.invite(conn, "b@example.com").id
    assert wb.published_static_docs(conn, me) == {"rules": {"id": "old", "url": "u", "hash": "h"}}
    assert wb.published_static_docs(conn, other) == {}
    wb.publish_static_docs(client, conn, other, inputs())
    assert set(wb.published_static_docs(conn, other)) == {"rules", "library", "panel_rubric"}
    assert wb.published_static_docs(conn, me)["rules"]["id"] == "old"


def test_static_docs_update_only_the_changed_one(drive, client, conn):
    wb.publish_static_docs(client, conn, users.owner(conn).id, inputs())
    changed = inputs()
    changed.base_resume_text += "\nCertifications: none\n"
    wb.publish_static_docs(client, conn, users.owner(conn).id, changed)
    assert drive.calls["update"] == 1 and len(_docs(drive)) == 3
    lib = next(d for d in _docs(drive) if d["name"] == "Strelitzia content library")
    assert b"Certifications" in lib["content"]

    wb.publish_static_docs(client, conn, users.owner(conn).id, changed, force=True)
    assert drive.calls["update"] == 4 and len(_docs(drive)) == 3


def test_publish_packet_writes_the_job_folder_and_records_the_doc(drive, client, conn):
    job = add_job_row(conn)
    doc_id, url = wb.publish_packet(client, conn, job, inputs())
    doc = drive.store[doc_id]
    assert doc["mimeType"] == DOC_MIME and url
    path = drive.path_of(doc_id)
    assert path[0] == "Strelitzia" and path[-1] == f"{job['company']} – {job['title']} – Claude packet"
    assert b"Required qualifications" in doc["content"]
    row = dict(conn.execute("SELECT packet_doc_id, packet_doc_url FROM jobs").fetchone())
    assert row == {"packet_doc_id": doc_id, "packet_doc_url": url}

    job = dict(conn.execute("SELECT * FROM jobs").fetchone())
    again_id, _ = wb.publish_packet(client, conn, job, inputs())
    assert again_id == doc_id and len(_docs(drive)) == 1
