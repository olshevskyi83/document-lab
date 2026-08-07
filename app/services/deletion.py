from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.models import TaskRecord, TaskStatus
from app.services.hash import sha256_file


LOGGER = logging.getLogger(__name__)


class DeletionError(RuntimeError):
    """Raised when a managed document cannot be deleted safely."""


@dataclass(frozen=True)
class ManagedFile:
    path: Path
    root: Path
    label: str
    verify_sha256: bool = False


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _validate_managed_file(target: ManagedFile, expected_sha256: str) -> Path:
    if not target.path.is_absolute():
        raise DeletionError(f"Refusing to delete {target.label}: stored path is not absolute")
    if ".." in target.path.parts:
        raise DeletionError(f"Refusing to delete {target.label}: path traversal is not allowed")

    root = target.root.absolute()
    lexical_path = Path(os.path.abspath(target.path))
    if lexical_path == root or not _is_within(lexical_path, root):
        raise DeletionError(f"Refusing to delete {target.label}: path is outside its managed root")

    relative = lexical_path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DeletionError(f"Refusing to delete {target.label}: symlinks are not allowed")

    resolved_root = root.resolve()
    resolved_path = lexical_path.resolve(strict=False)
    if resolved_path == resolved_root or not _is_within(resolved_path, resolved_root):
        raise DeletionError(f"Refusing to delete {target.label}: resolved path is outside its managed root")

    if resolved_path.exists():
        if not resolved_path.is_file():
            raise DeletionError(f"Refusing to delete {target.label}: target is not a regular file")
        if target.verify_sha256 and sha256_file(resolved_path) != expected_sha256:
            raise DeletionError(f"Refusing to delete {target.label}: file content no longer matches the task")
    return resolved_path


def _managed_files(task: TaskRecord, settings: Settings) -> list[ManagedFile]:
    targets: list[ManagedFile] = []
    if task.text_path:
        targets.append(ManagedFile(Path(task.text_path), settings.ready_root, "extracted text"))
    if task.metadata_path:
        targets.append(ManagedFile(Path(task.metadata_path), settings.ready_root, "metadata"))
    if task.report_path:
        targets.append(ManagedFile(Path(task.report_path), settings.reports_root, "report"))

    source = Path(task.source_path)
    source_absolute = Path(os.path.abspath(source)) if source.is_absolute() else source
    library_root = settings.library_root.absolute()
    originals_root = settings.originals_root.absolute()
    if source.is_absolute() and _is_within(source_absolute, library_root):
        targets.append(ManagedFile(source, settings.library_root, "Library source", True))
    elif source.is_absolute() and _is_within(source_absolute, originals_root):
        targets.append(ManagedFile(source, settings.originals_root, "uploaded original", True))
    else:
        raise DeletionError("Refusing to delete task: source path is outside managed Document Lab roots")

    if task.library_path:
        targets.append(ManagedFile(Path(task.library_path), settings.library_root, "Library file", True))
    return targets


def delete_managed_document(task_id: int, settings: Settings, database: Database) -> None:
    task = database.get_task(task_id)
    if task is None:
        raise DeletionError("Task not found")
    if task.status == TaskStatus.PROCESSING:
        raise DeletionError("A task cannot be deleted while it is processing")

    validated: list[tuple[ManagedFile, Path]] = []
    seen: set[Path] = set()
    for target in _managed_files(task, settings):
        path = _validate_managed_file(target, task.sha256)
        if path not in seen:
            validated.append((target, path))
            seen.add(path)

    for target, path in validated:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise DeletionError(f"Could not delete {target.label} at {path}: {exc}") from exc

    # Generated output directories are task-owned; remove them only when empty.
    ready_root = settings.ready_root.resolve()
    for target, path in validated:
        if target.root == settings.ready_root and path.parent != ready_root:
            try:
                path.parent.rmdir()
            except OSError:
                pass

    if not database.delete_task_record(task_id):
        raise DeletionError("Task record could not be deleted")


def backfill_managed_library_paths(settings: Settings, database: Database) -> None:
    """Safely associate pre-migration Library tasks with their exact existing file."""
    library_root = settings.library_root.resolve()
    for task in database.list_tasks_missing_library_path():
        candidates: list[Path] = []
        source = Path(task.source_path)
        if source.is_absolute():
            candidates.append(source)
        if task.approved_at and task.relative_path.startswith("Library/"):
            candidates.append(settings.documents_root / task.relative_path)

        for candidate in candidates:
            try:
                validated = _validate_managed_file(
                    ManagedFile(candidate, settings.library_root, "legacy Library file", True),
                    task.sha256,
                )
            except DeletionError:
                continue
            if validated.exists() and _is_within(validated, library_root):
                database.update_task(task.id, library_path=str(validated))
                break
        else:
            if task.approved_at:
                LOGGER.warning("Could not safely backfill Library path for task %s", task.id)
