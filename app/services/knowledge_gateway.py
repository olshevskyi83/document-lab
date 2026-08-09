from __future__ import annotations

from pathlib import Path

import httpx

from app.config import Settings
from app.models import TaskRecord, TaskStatus


class KnowledgeGatewayError(RuntimeError):
    pass


class KnowledgeGateway:
    """HTTP boundary between Document Lab and Homelab Core Knowledge API."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _text_path(self, task: TaskRecord) -> str:
        if not task.text_path:
            raise KnowledgeGatewayError("Extracted text is not available")
        text_path = Path(task.text_path).resolve()
        documents_root = self.settings.documents_root.resolve()
        if documents_root not in text_path.parents or not text_path.is_file():
            raise KnowledgeGatewayError("Extracted text is outside Documents storage")
        return text_path.relative_to(documents_root).as_posix()

    def _request(self, method: str, path: str, **kwargs: object) -> dict:
        try:
            with httpx.Client(
                base_url=self.settings.core_url,
                timeout=self.settings.core_timeout_seconds,
            ) as client:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                detail = str(exc.response.json().get("detail") or detail)
            except (ValueError, AttributeError):
                pass
            raise KnowledgeGatewayError(
                f"Homelab Core returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.RequestError as exc:
            raise KnowledgeGatewayError(f"Homelab Core is unavailable: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise KnowledgeGatewayError("Homelab Core returned an invalid response")
        return payload

    def _register(self, task: TaskRecord) -> None:
        if task.status != TaskStatus.COMPLETED:
            raise KnowledgeGatewayError("Approve the document before adding it to Knowledge")
        self._request(
            "POST",
            "/knowledge/documents",
            json={
                "document_id": task.document_id,
                "path": self._text_path(task),
                "source_type": "document",
                "project": "document-lab",
                "source_filename": task.source_filename,
            },
        )

    def index(self, task: TaskRecord, *, reindex: bool = False) -> dict:
        self._register(task)
        operation = "reindex" if reindex else "index"
        return self._request(
            "POST",
            f"/knowledge/documents/{task.document_id}/{operation}",
        )

    def delete(self, task: TaskRecord) -> dict:
        return self._request(
            "DELETE",
            f"/knowledge/documents/{task.document_id}",
        )
