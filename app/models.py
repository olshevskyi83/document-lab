from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class DocumentType(StrEnum):
    AUTO = "auto"
    BOOK = "book"
    DOCUMENT = "document"
    NOTE = "note"


class DocumentMetadata(BaseModel):
    document_id: str
    sha256: str
    source_filename: str
    source_type: str
    content_type: str
    catalog: str = ""
    relative_path: str
    title: str = ""
    author: str = ""
    language: str = ""
    year: int | None = None
    tags: list[str] = Field(default_factory=list)
    format: str
    page_count: int | None = None
    initial_text_page_count: int | None = None
    initial_text_coverage: float | None = None
    text_page_count: int | None = None
    text_coverage: float | None = None
    partially_scanned: bool = False
    extraction_method: str
    ocr_used: bool = False
    ocr_languages: list[str] = Field(default_factory=list)
    ocr_page_count: int = 0
    ocr_strategy: str = "none"
    ghostscript_version: str | None = None
    detected_script: str = "Unknown"
    detected_language: str = "Unknown"
    ocr_plausibility: float = 0.0
    character_count: int = 0
    word_count: int = 0
    created_at: str
    processed_at: str
    pipeline_version: str


class QualityReport(BaseModel):
    score: float
    sufficient: bool
    character_count: int
    word_count: int
    printable_ratio: float
    detected_script: str = "Unknown"
    detected_language: str = "Unknown"
    ocr_plausibility: float = 0.0
    pages_with_text: int | None = None
    page_count: int | None = None
    text_coverage: float | None = None
    partially_scanned: bool = False
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    text: str
    method: str
    page_count: int | None = None
    initial_text_page_count: int | None = None
    initial_text_coverage: float | None = None
    text_page_count: int | None = None
    text_coverage: float | None = None
    partially_scanned: bool = False
    ocr_used: bool = False
    ocr_languages: list[str] = Field(default_factory=list)
    ocr_page_count: int = 0
    ocr_strategy: str = "none"
    ghostscript_version: str | None = None
    detected_script: str = "Unknown"
    detected_language: str = "Unknown"
    ocr_plausibility: float = 0.0
    title: str = ""
    author: str = ""
    warnings: list[str] = Field(default_factory=list)


class ProcessingReport(BaseModel):
    document_id: str
    status: str
    metadata: DocumentMetadata
    quality: QualityReport
    warnings: list[str] = Field(default_factory=list)
    source_reference: str
    generated_at: str = Field(default_factory=utc_now)


class TaskRecord(BaseModel):
    id: int
    document_id: str
    sha256: str
    source_path: str
    source_filename: str
    relative_path: str
    catalog: str
    format: str
    content_type: str
    ocr_languages: str
    status: str
    progress: int
    stage: str = "queued"
    stage_detail: str | None = None
    error: str | None = None
    duplicate_of: str | None = None
    text_path: str | None = None
    metadata_path: str | None = None
    report_path: str | None = None
    library_path: str | None = None
    created_at: str
    updated_at: str
    approved_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "TaskRecord":
        return cls(**dict(row))
