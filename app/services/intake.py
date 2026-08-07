from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.models import DocumentType, TaskRecord, TaskStatus
from app.services.format_detection import detect_format
from app.services.hash import sha256_file
from app.services.ocr import parse_ocr_languages


def catalog_for_path(path: Path, library_root: Path) -> str:
    try:
        relative = path.relative_to(library_root)
    except ValueError:
        return ""
    return relative.parent.as_posix() if relative.parent.as_posix() != "." else ""


def enqueue_document(
    path: Path,
    *,
    settings: Settings,
    database: Database,
    content_type: str = DocumentType.AUTO,
    ocr_languages: str = "auto",
    catalog: str | None = None,
) -> TaskRecord:
    if content_type not in set(DocumentType):
        raise ValueError("Unsupported document type")
    parse_ocr_languages(ocr_languages)
    document_format = detect_format(path)
    sha256 = sha256_file(path)
    document_id = f"doc-{sha256[:24]}"
    canonical = database.find_canonical_by_hash(sha256)
    try:
        relative_path = path.relative_to(settings.documents_root).as_posix()
    except ValueError:
        relative_path = path.name
    values = dict(
        document_id=document_id,
        sha256=sha256,
        source_path=str(path),
        source_filename=path.name,
        relative_path=relative_path,
        catalog=catalog if catalog is not None else catalog_for_path(path, settings.library_root),
        format=document_format,
        content_type=str(content_type),
        ocr_languages=ocr_languages,
        progress=100 if canonical else 0,
        status=TaskStatus.DUPLICATE if canonical else TaskStatus.QUEUED,
        duplicate_of=canonical.document_id if canonical else None,
    )
    return database.create_task(**values)


def store_upload(source, filename: str, settings: Settings) -> Path:
    safe_name = Path(filename).name
    if safe_name in {"", ".", ".."}:
        raise ValueError("Invalid filename")
    destination = settings.originals_root / f"{uuid.uuid4().hex}-{safe_name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".upload")
    with temporary.open("xb") as target:
        shutil.copyfileobj(source, target)
    temporary.replace(destination)
    return destination
