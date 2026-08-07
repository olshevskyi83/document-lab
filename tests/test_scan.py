from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services.intake import enqueue_document, enqueue_new_library_documents


def environment(tmp_path):
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    return settings, database


def test_library_scan_silently_skips_known_hash_without_duplicate_row(tmp_path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "Books" / "known.txt"
    source.parent.mkdir()
    source.write_text("known content")
    first = enqueue_new_library_documents(settings=settings, database=database)
    second = enqueue_new_library_documents(settings=settings, database=database)
    assert (first.new, first.already_known, first.failed) == (1, 0, 0)
    assert (second.new, second.already_known, second.failed) == (0, 1, 0)
    tasks = database.list_tasks()
    assert len(tasks) == 1
    assert all(task.status != TaskStatus.DUPLICATE for task in tasks)


def test_library_scan_processes_changed_bytes_under_same_filename(tmp_path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "Books" / "changing.txt"
    source.parent.mkdir()
    source.write_text("first version")
    assert enqueue_new_library_documents(settings=settings, database=database).new == 1
    source.write_text("second changed version")
    result = enqueue_new_library_documents(settings=settings, database=database)
    assert result.new == 1
    assert result.already_known == 0
    tasks = database.list_tasks()
    assert len(tasks) == 2
    assert tasks[0].source_filename == tasks[1].source_filename
    assert tasks[0].sha256 != tasks[1].sha256


def test_manual_upload_duplicate_remains_explicit(tmp_path):
    settings, database = environment(tmp_path)
    first = settings.originals_root / "first.txt"
    second = settings.originals_root / "second.txt"
    first.write_text("identical bytes")
    second.write_text("identical bytes")
    original = enqueue_document(first, settings=settings, database=database)
    duplicate = enqueue_document(second, settings=settings, database=database)
    assert original.status == TaskStatus.QUEUED
    assert duplicate.status == TaskStatus.DUPLICATE
    assert duplicate.duplicate_of == original.document_id
    assert len(database.list_tasks()) == 2
