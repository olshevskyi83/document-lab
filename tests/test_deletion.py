from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services.deletion import DeletionError, delete_managed_document
from app.services.hash import sha256_file
from app.services.intake import (
    enqueue_document,
    enqueue_new_library_documents,
    place_approved_upload,
    store_upload,
)


def environment(tmp_path: Path) -> tuple[Settings, Database]:
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    return settings, database


def create_task(database: Database, source: Path, **overrides) -> object:
    values = {
        "document_id": f"doc-{sha256_file(source)[:24]}",
        "sha256": sha256_file(source),
        "source_path": str(source),
        "source_filename": source.name,
        "relative_path": source.name,
        "catalog": "",
        "format": source.suffix.lstrip("."),
        "content_type": "auto",
        "ocr_languages": "auto",
        "status": TaskStatus.COMPLETED,
        "progress": 100,
        "duplicate_of": None,
        "library_path": None,
    }
    values.update(overrides)
    return database.create_task(**values)


def test_deleted_library_document_is_not_rediscovered(tmp_path: Path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "Books" / "book.txt"
    source.parent.mkdir()
    source.write_text("managed library document", encoding="utf-8")

    scan = enqueue_new_library_documents(settings=settings, database=database)
    assert scan.new == 1
    task = database.list_tasks()[0]
    database.update_task(task.id, status=TaskStatus.COMPLETED, progress=100)

    delete_managed_document(task.id, settings, database)

    assert not source.exists()
    assert database.get_task(task.id) is None
    second_scan = enqueue_new_library_documents(settings=settings, database=database)
    assert second_scan.new == 0
    assert second_scan.already_known == 0
    assert database.list_tasks() == []


def test_delete_approved_upload_removes_all_managed_files(tmp_path: Path):
    settings, database = environment(tmp_path)
    catalog = settings.library_root / "Books"
    catalog.mkdir()
    original = store_upload(BytesIO(b"approved upload"), "book.txt", settings)
    task = enqueue_document(
        original,
        settings=settings,
        database=database,
        catalog="Books",
        source_filename="book.txt",
    )
    database.update_task(task.id, status=TaskStatus.READY)
    task = database.get_task(task.id)
    destination = place_approved_upload(task, settings)
    assert destination is not None
    assert database.approve(task.id)

    output_dir = settings.ready_root / task.document_id
    output_dir.mkdir()
    text = output_dir / "document.txt"
    metadata = output_dir / "metadata.json"
    report = settings.reports_root / f"{task.document_id}.json"
    text.write_text("text", encoding="utf-8")
    metadata.write_text("{}", encoding="utf-8")
    report.write_text("{}", encoding="utf-8")
    database.update_task(
        task.id,
        library_path=str(destination.resolve()),
        text_path=str(text),
        metadata_path=str(metadata),
        report_path=str(report),
    )

    delete_managed_document(task.id, settings, database)

    assert database.get_task(task.id) is None
    assert not original.exists()
    assert not destination.exists()
    assert not text.exists()
    assert not metadata.exists()
    assert not report.exists()
    assert catalog.is_dir()


def test_delete_refuses_source_outside_managed_roots(tmp_path: Path):
    settings, database = environment(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete", encoding="utf-8")
    task = create_task(database, outside)

    with pytest.raises(DeletionError, match="outside managed Document Lab roots"):
        delete_managed_document(task.id, settings, database)

    assert outside.exists()
    assert database.get_task(task.id) is not None


def test_delete_refuses_symlink_in_library(tmp_path: Path):
    settings, database = environment(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete", encoding="utf-8")
    link = settings.library_root / "linked.txt"
    link.symlink_to(outside)
    task = create_task(database, outside, source_path=str(link), library_path=str(link))

    with pytest.raises(DeletionError, match="symlinks are not allowed"):
        delete_managed_document(task.id, settings, database)

    assert link.is_symlink()
    assert outside.exists()
    assert database.get_task(task.id) is not None


def test_failed_source_deletion_keeps_database_task(tmp_path: Path, monkeypatch):
    settings, database = environment(tmp_path)
    source = settings.library_root / "book.txt"
    source.write_text("managed document", encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database)
    database.update_task(task.id, status=TaskStatus.COMPLETED)
    real_unlink = Path.unlink

    def failing_unlink(path: Path, *args, **kwargs):
        if path == source:
            raise PermissionError("read-only filesystem")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(DeletionError, match="read-only filesystem"):
        delete_managed_document(task.id, settings, database)

    assert source.exists()
    assert database.get_task(task.id) is not None


def test_initialize_migrates_existing_database_with_library_path(tmp_path: Path):
    database_path = tmp_path / "data" / "legacy.sqlite3"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE tasks ("
            "id INTEGER PRIMARY KEY, document_id TEXT, sha256 TEXT, source_path TEXT, "
            "source_filename TEXT, relative_path TEXT, catalog TEXT, format TEXT, "
            "content_type TEXT, ocr_languages TEXT, status TEXT, progress INTEGER, "
            "error TEXT, duplicate_of TEXT, text_path TEXT, metadata_path TEXT, "
            "report_path TEXT, created_at TEXT, updated_at TEXT, approved_at TEXT)"
        )

    database = Database(database_path)
    database.initialize()

    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
    assert {"library_path", "stage", "stage_detail", "started_at", "finished_at"} <= columns


def test_processing_task_cannot_be_deleted(tmp_path: Path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "book.txt"
    source.write_text("processing", encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database)
    database.update_task(task.id, status=TaskStatus.PROCESSING)

    with pytest.raises(DeletionError, match="while it is processing"):
        delete_managed_document(task.id, settings, database)

    assert source.exists()
    assert database.get_task(task.id) is not None
