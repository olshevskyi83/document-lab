from io import BytesIO

from fastapi.testclient import TestClient

import app.main as web
from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services.intake import enqueue_document, place_approved_upload, store_upload


def environment(tmp_path):
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    return settings, database


def queued_upload(settings, database, filename="book.txt", content=b"new document content"):
    original = store_upload(BytesIO(content), filename, settings)
    task = enqueue_document(
        original,
        settings=settings,
        database=database,
        catalog="Books",
        source_filename=filename,
    )
    return original, task


def test_upload_does_not_enter_library_before_approval(tmp_path):
    settings, database = environment(tmp_path)
    (settings.library_root / "Books").mkdir()
    original, task = queued_upload(settings, database)
    assert original.is_file()
    assert task.status == TaskStatus.QUEUED
    assert list((settings.library_root / "Books").iterdir()) == []


def test_approval_copies_upload_to_selected_catalog(tmp_path):
    settings, database = environment(tmp_path)
    catalog = settings.library_root / "Books"
    catalog.mkdir()
    original, task = queued_upload(settings, database)
    database.update_task(task.id, status=TaskStatus.READY)
    task = database.get_task(task.id)
    destination = place_approved_upload(task, settings)
    assert database.approve(task.id)
    assert destination == catalog / "book.txt"
    assert destination.read_bytes() == original.read_bytes()
    assert original.is_file()


def test_library_scan_source_is_not_copied_on_approval(tmp_path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "Books" / "existing.txt"
    source.parent.mkdir()
    source.write_text("already in library")
    task = enqueue_document(source, settings=settings, database=database)
    assert place_approved_upload(task, settings) is None
    assert list(source.parent.iterdir()) == [source]


def test_approval_avoids_filename_collisions(tmp_path):
    settings, database = environment(tmp_path)
    catalog = settings.library_root / "Books"
    catalog.mkdir()
    (catalog / "book.txt").write_bytes(b"existing different content")
    original, task = queued_upload(settings, database, content=b"new unique content")
    destination = place_approved_upload(task, settings)
    assert destination.name.startswith("book-")
    assert destination.name.endswith(".txt")
    assert destination.read_bytes() == original.read_bytes()
    assert (catalog / "book.txt").read_bytes() == b"existing different content"


def test_manual_upload_requires_catalog_before_original_or_task_is_created(
    monkeypatch, tmp_path
):
    settings, database = environment(tmp_path)
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    client = TestClient(web.app)

    response = client.post(
        "/upload",
        files={"file": ("book.txt", b"must not be queued", "text/plain")},
        data={"catalog": "", "ocr_languages": "auto", "content_type": "auto"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "%D0%9E%D0%B1%D0%B5%D1%80%D1%96%D1%82%D1%8C" in response.headers["location"]
    assert database.list_tasks() == []
    assert list(settings.originals_root.iterdir()) == []
