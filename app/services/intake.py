from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.models import DocumentType, TaskRecord, TaskStatus
from app.services.format_detection import detect_format
from app.services.hash import sha256_file
from app.services.library import safe_library_path, scan_documents
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
    source_filename: str | None = None,
    precomputed_sha256: str | None = None,
) -> TaskRecord:
    if content_type not in set(DocumentType):
        raise ValueError("Unsupported document type")
    parse_ocr_languages(ocr_languages)
    document_format = detect_format(path)
    sha256 = precomputed_sha256 or sha256_file(path)
    document_id = f"doc-{sha256[:24]}"
    canonical = database.find_canonical_by_hash(sha256)
    try:
        relative_path = path.relative_to(settings.documents_root).as_posix()
    except ValueError:
        relative_path = path.name
    resolved_path = path.resolve()
    resolved_library = settings.library_root.resolve()
    library_path = (
        str(resolved_path)
        if not path.is_symlink() and resolved_path != resolved_library and resolved_library in resolved_path.parents
        else None
    )
    values = dict(
        document_id=document_id,
        sha256=sha256,
        source_path=str(path),
        source_filename=Path(source_filename).name if source_filename else path.name,
        relative_path=relative_path,
        catalog=catalog if catalog is not None else catalog_for_path(path, settings.library_root),
        format=document_format,
        content_type=str(content_type),
        ocr_languages=ocr_languages,
        progress=100 if canonical else 0,
        status=TaskStatus.DUPLICATE if canonical else TaskStatus.QUEUED,
        stage="duplicate" if canonical else "queued",
        stage_detail=None,
        duplicate_of=canonical.document_id if canonical else None,
        library_path=library_path,
    )
    return database.create_task(**values)


@dataclass(frozen=True)
class LibraryScanResult:
    new: int = 0
    already_known: int = 0
    failed: int = 0


def enqueue_new_library_documents(
    *,
    settings: Settings,
    database: Database,
    content_type: str = DocumentType.AUTO,
    ocr_languages: str = "auto",
) -> LibraryScanResult:
    new = already_known = failed = 0
    for path in scan_documents(settings.library_root):
        try:
            sha256 = sha256_file(path)
            if database.is_known_hash(sha256):
                already_known += 1
                continue
            enqueue_document(
                path,
                settings=settings,
                database=database,
                content_type=content_type,
                ocr_languages=ocr_languages,
                precomputed_sha256=sha256,
            )
            new += 1
        except Exception:
            failed += 1
    return LibraryScanResult(new=new, already_known=already_known, failed=failed)


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


def place_approved_upload(task: TaskRecord, settings: Settings) -> Path | None:
    source = Path(task.source_path).resolve()
    library_root = settings.library_root.resolve()
    if source == library_root or library_root in source.parents:
        return None
    originals_root = settings.originals_root.resolve()
    if originals_root not in source.parents:
        return None
    catalog = safe_library_path(settings.library_root, task.catalog)
    if not catalog.is_dir():
        raise ValueError("Selected Library catalog no longer exists")
    filename = Path(task.source_filename).name
    candidate = catalog / filename
    if candidate.exists() and sha256_file(candidate) == task.sha256:
        return candidate
    if candidate.exists():
        stem, suffix = Path(filename).stem, Path(filename).suffix
        candidate = catalog / f"{stem}-{task.sha256[:8]}{suffix}"
        counter = 2
        while candidate.exists():
            if sha256_file(candidate) == task.sha256:
                return candidate
            candidate = catalog / f"{stem}-{task.sha256[:8]}-{counter}{suffix}"
            counter += 1
    temporary = catalog / f".{candidate.name}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
        temporary.replace(candidate)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return candidate
