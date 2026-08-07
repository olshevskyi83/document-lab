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
    extraction_method: str
    ocr_used: bool = False
    ocr_languages: list[str] = Field(default_factory=list)
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
    pages_with_text: int | None = None
    page_count: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    text: str
    method: str
    page_count: int | None = None
    ocr_used: bool = False
    ocr_languages: list[str] = Field(default_factory=list)
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
    error: str | None = None
    duplicate_of: str | None = None
    text_path: str | None = None
    metadata_path: str | None = None
    report_path: str | None = None
    created_at: str
    updated_at: str
    approved_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "TaskRecord":
        return cls(**dict(row))
