from pathlib import Path
from unittest.mock import Mock, call

import pytest
from fastapi.testclient import TestClient

import app.main as web
from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services.knowledge_gateway import KnowledgeGateway, KnowledgeGatewayError


def make_completed_task(tmp_path: Path):
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    text_path = settings.ready_root / "doc-1" / "text.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text("Knowledge text", encoding="utf-8")
    task = database.create_task(
        document_id="doc-1",
        sha256="abc",
        source_path=str(settings.originals_root / "book.pdf"),
        source_filename="book.pdf",
        relative_path="originals/book.pdf",
        catalog="Books",
        format="pdf",
        content_type="book",
        ocr_languages="eng",
        status=TaskStatus.COMPLETED,
        progress=100,
        duplicate_of=None,
    )
    database.update_task(task.id, text_path=str(text_path))
    return settings, database, database.get_task(task.id)


def test_index_registers_stable_document_then_calls_core(tmp_path):
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)
    gateway._request = Mock(
        side_effect=[
            {"document_id": task.document_id, "index_status": "not_indexed"},
            {"document_id": task.document_id, "status": "indexed"},
        ]
    )

    result = gateway.index(task)

    assert result["status"] == "indexed"
    assert gateway._request.call_args_list == [
        call(
            "POST",
            "/knowledge/documents",
            json={
                "document_id": "doc-1",
                "path": "ready/doc-1/text.txt",
                "source_type": "document",
                "project": "document-lab",
                "source_filename": "book.pdf",
            },
        ),
        call("POST", "/knowledge/documents/doc-1/index"),
    ]


def test_reindex_and_delete_use_generic_document_id(tmp_path):
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)
    gateway._request = Mock(
        side_effect=[
            {},
            {"status": "indexed"},
            {"status": "deleted", "index_deleted": True},
        ]
    )

    gateway.index(task, reindex=True)
    result = gateway.delete(task)

    assert gateway._request.call_args_list[-2:] == [
        call("POST", "/knowledge/documents/doc-1/reindex"),
        call("DELETE", "/knowledge/documents/doc-1"),
    ]
    assert result["status"] == "deleted"
    assert Path(task.text_path).is_file()


def test_unapproved_document_cannot_be_indexed(tmp_path):
    settings, _, task = make_completed_task(tmp_path)
    task.status = TaskStatus.READY
    gateway = KnowledgeGateway(settings)

    with pytest.raises(KnowledgeGatewayError, match="Approve"):
        gateway.index(task)


def test_database_persists_knowledge_state(tmp_path):
    _, database, task = make_completed_task(tmp_path)

    database.update_knowledge(task.id, status="indexed", error=None)
    saved = database.get_task(task.id)

    assert saved.knowledge_status == "indexed"
    assert saved.knowledge_error is None
    assert saved.knowledge_updated_at is not None


def test_task_routes_persist_index_and_delete_without_removing_text(monkeypatch, tmp_path):
    settings, database, task = make_completed_task(tmp_path)
    core = Mock()
    core.index.return_value = {"document_id": "doc-1", "status": "indexed"}
    core.delete.return_value = {
        "document_id": "doc-1",
        "index_deleted": True,
        "status": "deleted",
    }
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    indexed = client.post(
        f"/tasks/{task.id}/knowledge/index",
        follow_redirects=False,
    )
    assert indexed.status_code == 303
    assert database.get_task(task.id).knowledge_status == "indexed"

    detail = client.get(f"/tasks/{task.id}")
    assert "Переіндексувати" in detail.text
    assert "Видалити з бази знань" in detail.text

    deleted = client.post(
        f"/tasks/{task.id}/knowledge/delete",
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert database.get_task(task.id).knowledge_status == "deleted"
    assert Path(task.text_path).is_file()
