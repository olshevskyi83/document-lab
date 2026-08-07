from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    remote_root: Path
    data_root: Path
    logs_root: Path
    pipeline_version: str = "1.0.0"
    worker_poll_seconds: float = 1.0
    command_timeout_seconds: int = 3600

    @property
    def documents_root(self) -> Path:
        return self.remote_root / "Documents"

    @property
    def library_root(self) -> Path:
        return self.documents_root / "Library"

    @property
    def incoming_root(self) -> Path:
        return self.documents_root / "incoming"

    @property
    def processing_root(self) -> Path:
        return self.documents_root / "processing"

    @property
    def ready_root(self) -> Path:
        return self.documents_root / "ready"

    @property
    def failed_root(self) -> Path:
        return self.documents_root / "failed"

    @property
    def reports_root(self) -> Path:
        return self.documents_root / "reports"

    @property
    def originals_root(self) -> Path:
        return self.documents_root / "originals"

    @property
    def imports_root(self) -> Path:
        return self.documents_root / "imports"

    @property
    def database_path(self) -> Path:
        return self.data_root / "document_lab.sqlite3"

    def ensure_directories(self) -> None:
        for path in (
            self.library_root,
            self.incoming_root,
            self.processing_root,
            self.ready_root,
            self.failed_root,
            self.reports_root,
            self.originals_root,
            self.imports_root,
            self.data_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings(
        remote_root=Path(os.getenv("REMOTE_ROOT", "/remote")),
        data_root=Path(os.getenv("DATA_ROOT", "/app/data")),
        logs_root=Path(os.getenv("LOGS_ROOT", "/app/logs")),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
        command_timeout_seconds=int(os.getenv("COMMAND_TIMEOUT_SECONDS", "3600")),
    )
