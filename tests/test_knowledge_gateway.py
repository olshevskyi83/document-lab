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


def test_task_detail_shows_only_add_to_knowledge_actions(monkeypatch, tmp_path):
    settings, database, task = make_completed_task(tmp_path)
    core = Mock()
    core.index.return_value = {"document_id": "doc-1", "status": "indexed"}
    core.check_knowledge_exists.side_effect = [False, True]
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    detail_before_indexing = client.get(f"/tasks/{task.id}")
    assert "Додати до бази знань" in detail_before_indexing.text
    assert "Not added" in detail_before_indexing.text
    assert "Переіндексувати" not in detail_before_indexing.text
    assert "Видалити з бази знань" not in detail_before_indexing.text

    indexed = client.post(
        f"/tasks/{task.id}/knowledge/index",
        follow_redirects=False,
    )
    assert indexed.status_code == 303
    assert database.get_task(task.id).knowledge_status == "indexed"

    detail = client.get(f"/tasks/{task.id}")
    assert "Indexed" in detail.text
    assert "<button disabled>Added to Knowledge</button>" in detail.text
    assert "Додати до бази знань" not in detail.text
    assert "Переіндексувати" not in detail.text
    assert "Видалити з бази знань" not in detail.text


# ====================================================================
# NEW TESTS: Knowledge status checking and consistency
# ====================================================================


def test_check_knowledge_exists_returns_true_when_core_has_document(tmp_path):
    """Document in central registry → should return True."""
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)

    # Mock _request to return central list with document
    gateway._request = Mock(
        return_value=[
            {"document_id": "doc-1", "status": "indexed"},
            {"document_id": "other-doc", "status": "indexed"},
        ]
    )

    result = gateway.check_knowledge_exists("doc-1")

    assert result is True
    # Verify it queries the central list, not single-document endpoint
    gateway._request.assert_called_once_with("GET", "/knowledge/documents")


def test_check_knowledge_exists_returns_false_when_central_registry_absent(tmp_path):
    """Document NOT in central registry → should return False."""
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)

    # Mock _request to return central list without document
    gateway._request = Mock(
        return_value=[
            {"document_id": "other-doc", "status": "indexed"},
        ]
    )

    result = gateway.check_knowledge_exists("doc-1")

    assert result is False
    # Verify it queries the central list, not single-document endpoint
    gateway._request.assert_called_once_with("GET", "/knowledge/documents")


def test_check_knowledge_exists_handles_central_list_dict_response(tmp_path):
    """Central list may return dict with 'documents' key."""
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)

    # Mock _request to return dict format with documents key
    gateway._request = Mock(
        return_value={
            "documents": [
                {"document_id": "doc-1", "status": "indexed"},
            ]
        }
    )

    result = gateway.check_knowledge_exists("doc-1")

    assert result is True


def test_check_knowledge_exists_handles_core_unavailable(tmp_path):
    """Core unavailable is distinct from a factual central-registry absence."""
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)

    gateway._request = Mock(side_effect=KnowledgeGatewayError("Core unavailable"))

    with pytest.raises(KnowledgeGatewayError, match="Core unavailable"):
        gateway.check_knowledge_exists("nonexistent-doc")


def test_check_knowledge_exists_only_queries_central_list(tmp_path):
    """Verify that check_knowledge_exists queries central list, NOT single-document endpoint."""
    settings, _, task = make_completed_task(tmp_path)
    gateway = KnowledgeGateway(settings)

    gateway._request = Mock(
        return_value=[
            {"document_id": "doc-1", "status": "indexed"},
        ]
    )

    gateway.check_knowledge_exists("doc-1")

    # Should call GET /knowledge/documents, NOT /knowledge/documents/{id}
    gateway._request.assert_called_once_with("GET", "/knowledge/documents")

    # Verify it's NOT calling the single-document endpoint
    call_args = gateway._request.call_args_list
    assert len(call_args) == 1
    assert "/knowledge/documents/doc-1" not in str(call_args)


def test_local_indexed_cache_reconciles_when_central_registry_absent(monkeypatch, tmp_path):
    """Stale local indexed cache + central registry absent → local should reconcile to not_indexed."""
    settings, database, task = make_completed_task(tmp_path)

    # Simulate stale local cache: database says indexed
    database.update_knowledge(task.id, status="indexed", error=None)
    assert database.get_task(task.id).knowledge_status == "indexed"

    # Mock gateway to return False (Core says document doesn't exist)
    core = Mock()
    core.check_knowledge_exists.return_value = False

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # Fetch task detail - should trigger reconciliation
    response = client.get(f"/tasks/{task.id}")
    assert response.status_code == 200

    # After fetching, local cache should be reconciled to not_indexed
    task_after = database.get_task(task.id)
    assert task_after.knowledge_status == "not_indexed"

    # UI should show "Add to Knowledge" button
    assert "Додати до бази знань" in response.text


def test_stale_indexed_cache_does_not_auto_register(monkeypatch, tmp_path):
    """Stale local indexed + Core 404 → should NOT trigger auto-register."""
    settings, database, task = make_completed_task(tmp_path)

    # Set stale cache
    database.update_knowledge(task.id, status="indexed", error=None)

    core = Mock()
    core.check_knowledge_exists.return_value = False

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # Fetch task detail
    response = client.get(f"/tasks/{task.id}")

    # Verify that index/register was NOT called
    # (only check_knowledge_exists should have been called)
    core.index.assert_not_called()
    core._register.assert_not_called()


def test_explicit_add_to_knowledge_still_works(monkeypatch, tmp_path):
    """Explicit Add still works after the stale cache has been reconciled."""
    settings, database, task = make_completed_task(tmp_path)
    database.update_knowledge(task.id, status="indexed", error=None)

    core = Mock()
    core.index.return_value = {"document_id": "doc-1", "status": "indexed"}
    core.check_knowledge_exists.return_value = False  # Before adding, not in Core

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # Verify UI shows "Add to Knowledge" before indexing
    detail_before = client.get(f"/tasks/{task.id}")
    assert "Додати до бази знань" in detail_before.text
    assert database.get_task(task.id).knowledge_status == "not_indexed"

    # Click "Add to Knowledge"
    response = client.post(
        f"/tasks/{task.id}/knowledge/index",
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Verify index was called
    core.index.assert_called_once()


def test_core_indexed_shows_added_to_knowledge(monkeypatch, tmp_path):
    """After Add, if Core says indexed → UI should show "Added to Knowledge"."""
    settings, database, task = make_completed_task(tmp_path)

    core = Mock()
    core.check_knowledge_exists.return_value = True  # Core has the document
    core.index.return_value = {"document_id": "doc-1", "status": "indexed"}

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # After indexing
    client.post(f"/tasks/{task.id}/knowledge/index", follow_redirects=True)

    # Fetch detail again - Core now says document exists
    detail = client.get(f"/tasks/{task.id}")
    assert "Added to Knowledge" in detail.text
    assert "<button disabled>Added to Knowledge</button>" in detail.text


def test_reindex_button_is_absent_from_ui(monkeypatch, tmp_path):
    """Reindex button should NOT appear in UI."""
    settings, database, task = make_completed_task(tmp_path)

    core = Mock()
    core.check_knowledge_exists.return_value = True

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    detail = client.get(f"/tasks/{task.id}")
    assert "Переіндексувати" not in detail.text
    assert "/knowledge/reindex" not in detail.text


def test_delete_knowledge_button_is_absent_from_ui(monkeypatch, tmp_path):
    """Delete Knowledge button should NOT appear in UI."""
    settings, database, task = make_completed_task(tmp_path)

    core = Mock()
    core.check_knowledge_exists.return_value = True

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    detail = client.get(f"/tasks/{task.id}")
    assert "Видалити з бази знань" not in detail.text
    assert "/knowledge/delete" not in detail.text


def test_get_api_knowledge_status_endpoint(monkeypatch, tmp_path):
    """GET /api/tasks/{task_id}/knowledge/status should return Core status."""
    settings, database, task = make_completed_task(tmp_path)

    core = Mock()
    core.check_knowledge_exists.return_value = True

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    response = client.get(f"/api/tasks/{task.id}/knowledge/status")
    assert response.status_code == 200

    data = response.json()
    assert "exists_in_core" in data
    assert "local_cache" in data
    assert data["exists_in_core"] is True
    assert data["local_cache"] == "not_indexed"


def test_get_api_knowledge_status_reconciles_stale_cache(monkeypatch, tmp_path):
    """GET /api/tasks/{task_id}/knowledge/status should reconcile stale cache."""
    settings, database, task = make_completed_task(tmp_path)

    # Set stale cache
    database.update_knowledge(task.id, status="indexed", error=None)

    core = Mock()
    core.check_knowledge_exists.return_value = False  # Core says no document

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    response = client.get(f"/api/tasks/{task.id}/knowledge/status")
    assert response.status_code == 200

    data = response.json()
    assert data["exists_in_core"] is False

    # Local cache should now be reconciled
    task_after = database.get_task(task.id)
    assert task_after.knowledge_status == "not_indexed"


def test_core_unavailable_does_not_cause_auto_registration(monkeypatch, tmp_path):
    """Core unavailable does not report local cache as factual Core status."""
    settings, database, task = make_completed_task(tmp_path)
    database.update_knowledge(task.id, status="indexed", error=None)

    core = Mock()
    core.check_knowledge_exists.side_effect = KnowledgeGatewayError("Core unavailable")

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # Even with Core unavailable, should not register
    response = client.get(f"/tasks/{task.id}")
    assert response.status_code == 200
    assert "Core unavailable" in response.text
    assert "Статус (Homelab Core)</dt>\n    <dd><code>Indexed" not in response.text
    assert database.get_task(task.id).knowledge_status == "indexed"

    # No auto-registration should happen
    core.index.assert_not_called()


def test_central_indexed_wins_over_local_not_indexed(monkeypatch, tmp_path):
    """A central record, rather than the cache, controls the Add button."""
    settings, database, task = make_completed_task(tmp_path)
    core = Mock()
    core.check_knowledge_exists.return_value = True

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    response = TestClient(web.app).get(f"/tasks/{task.id}")

    assert "Indexed" in response.text
    assert "<button disabled>Added to Knowledge</button>" in response.text
    assert "Додати до бази знань" not in response.text
    assert database.get_task(task.id).knowledge_status == "not_indexed"


def test_local_artifact_alone_does_not_create_knowledge_record(monkeypatch, tmp_path):
    """Local document artifact alone should NOT create Knowledge record."""
    settings, database, task = make_completed_task(tmp_path)

    core = Mock()
    # Core says document doesn't exist
    core.check_knowledge_exists.return_value = False

    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    monkeypatch.setattr(web, "knowledge_gateway", core)
    client = TestClient(web.app)

    # Just loading the page should not create a Knowledge record
    response = client.get(f"/tasks/{task.id}")
    assert response.status_code == 200

    # Verify Core.index was NOT called
    core.index.assert_not_called()

    # Verify UI shows "Add to Knowledge" button, not "Added"
    assert "Додати до бази знань" in response.text
