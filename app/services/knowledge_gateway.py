from __future__ import annotations

from pathlib import Path
from typing import Protocol


class KnowledgeGateway(Protocol):
    """Future Homelab Core boundary; implementations must live outside Document Lab core."""

    def publish(self, document_id: str, text_path: Path, metadata_path: Path) -> str:
        """Ask Homelab Core to add an approved document to its knowledge pipeline."""
        ...
