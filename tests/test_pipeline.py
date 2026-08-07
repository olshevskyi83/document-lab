from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services.document_worker import DocumentProcessor
from app.services.intake import enqueue_document


def test_txt_pipeline_writes_ready_artifacts(tmp_path):
    settings = Settings(
        remote_root=tmp_path / "remote",
        data_root=tmp_path / "data",
        logs_root=tmp_path / "logs",
    )
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source = settings.library_root / "Notes" / "sample.txt"
    source.parent.mkdir()
    source.write_text("A meaningful document. " * 100, encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database, content_type="note")
    claimed = database.claim_next_task()
    assert claimed.id == task.id
    DocumentProcessor(settings, database).process(task.id)
    result = database.get_task(task.id)
    assert result.status == TaskStatus.READY
    assert result.progress == 100
    assert result.text_path and result.metadata_path and result.report_path
    assert "meaningful document" in open(result.text_path, encoding="utf-8").read()
