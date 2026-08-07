from app.db import Database
from app.models import TaskStatus


def values(**overrides):
    base = dict(
        document_id="doc-abc",
        sha256="abc",
        source_path="/remote/Documents/Library/a.txt",
        source_filename="a.txt",
        relative_path="Library/a.txt",
        catalog="",
        format="txt",
        content_type="auto",
        ocr_languages="auto",
        status=TaskStatus.QUEUED,
        progress=0,
        duplicate_of=None,
    )
    base.update(overrides)
    return base


def test_duplicate_lookup_uses_hash_not_filename(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    first = database.create_task(**values())
    assert database.find_canonical_by_hash("abc").id == first.id
    assert database.find_canonical_by_hash("different") is None


def test_processing_tasks_are_recovered_on_restart(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    task = database.create_task(**values(status=TaskStatus.PROCESSING))
    database.initialize()
    recovered = database.get_task(task.id)
    assert recovered.status == TaskStatus.QUEUED
    assert "Recovered" in recovered.error
