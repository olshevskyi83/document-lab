from fastapi.testclient import TestClient

import app.main as web
from app.config import Settings
from app.db import Database
from app.services.intake import enqueue_document


def test_task_preview_contains_full_text_and_download(monkeypatch, tmp_path):
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source = settings.library_root / "Books" / "long.txt"
    source.parent.mkdir()
    source.write_text("source")
    task = enqueue_document(source, settings=settings, database=database)
    full_text = "A" * 110_000 + "FULL-TEXT-END"
    text_path = settings.ready_root / task.document_id / "text.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text(full_text)
    database.update_task(task.id, text_path=str(text_path))
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    client = TestClient(web.app)
    preview = client.get(f"/tasks/{task.id}")
    assert preview.status_code == 200
    assert "FULL-TEXT-END" in preview.text
    assert f"/tasks/{task.id}/download-text" in preview.text
    download = client.get(f"/tasks/{task.id}/download-text")
    assert download.status_code == 200
    assert download.text == full_text
    assert "attachment" in download.headers["content-disposition"]
