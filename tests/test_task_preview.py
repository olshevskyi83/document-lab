from fastapi.testclient import TestClient
import json

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
    metadata_path = text_path.parent / "metadata.json"
    metadata = {
        "extraction_method": "pdf_text",
        "ocr_used": False,
        "ocr_languages": [],
        "ocr_page_count": 0,
        "ocr_strategy": "none",
        "page_count": 2,
        "text_page_count": 2,
        "text_coverage": 1.0,
        "character_count": len(full_text),
        "word_count": 1,
        "partially_scanned": False,
        "initial_text_coverage": 1.0,
    }
    metadata_path.write_text(json.dumps(metadata))
    report_path = settings.reports_root / f"{task.document_id}.json"
    report = {"quality": {"score": 1.0, "sufficient": True}, "warnings": []}
    report_path.write_text(json.dumps(report))
    database.update_task(
        task.id,
        text_path=str(text_path),
        metadata_path=str(metadata_path),
        report_path=str(report_path),
    )
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    client = TestClient(web.app)
    preview = client.get(f"/tasks/{task.id}")
    assert preview.status_code == 200
    assert "FULL-TEXT-END" in preview.text
    assert f"/tasks/{task.id}/download-text" in preview.text
    assert "Document pages" in preview.text
    assert "Coverage percent" in preview.text
    download = client.get(f"/tasks/{task.id}/download-text")
    assert download.status_code == 200
    assert download.text == full_text
    assert "attachment" in download.headers["content-disposition"]
    metadata_download = client.get(f"/tasks/{task.id}/download-metadata")
    report_download = client.get(f"/tasks/{task.id}/download-report")
    assert metadata_download.json() == metadata
    assert report_download.json() == report
