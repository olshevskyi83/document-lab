from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from app.services.format_detection import SUPPORTED_FORMATS


def safe_library_path(root: Path, relative: str = "") -> Path:
    candidate_text = relative.strip().replace("\\", "/")
    pure = PurePosixPath(candidate_text)
    if re.match(r"^[A-Za-z]:/", candidate_text) or pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        if candidate_text:
            raise ValueError("Unsafe library path")
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed in Library paths")
    candidate = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("Path escapes Library root")
    return candidate


def discover_catalogs(root: Path) -> list[str]:
    if not root.exists():
        return []
    catalogs: list[str] = []
    for current, directories, _ in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if not (Path(current) / name).is_symlink())
        path = Path(current)
        if path != root:
            catalogs.append(path.relative_to(root).as_posix())
    return catalogs


def library_tree(root: Path) -> dict[str, object]:
    def node(path: Path) -> dict[str, object]:
        children = [node(child) for child in sorted(path.iterdir()) if child.is_dir() and not child.is_symlink()]
        return {"name": path.name, "path": "" if path == root else path.relative_to(root).as_posix(), "children": children}

    root.mkdir(parents=True, exist_ok=True)
    return node(root)


def scan_documents(root: Path) -> list[Path]:
    if not root.exists():
        return []
    results: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            extension = path.suffix.lower().lstrip(".")
            if extension == "djv":
                extension = "djvu"
            elif extension == "markdown":
                extension = "md"
            if extension in SUPPORTED_FORMATS:
                results.append(path)
    return sorted(results)


def create_catalog(root: Path, relative: str) -> Path:
    path = safe_library_path(root, relative)
    if path == root:
        raise ValueError("Catalog name is required")
    path.mkdir(parents=True, exist_ok=False)
    return path


def rename_catalog(root: Path, relative: str, new_name: str) -> Path:
    source = safe_library_path(root, relative)
    if source == root or not source.is_dir():
        raise ValueError("Catalog does not exist")
    if not new_name.strip() or "/" in new_name or "\\" in new_name or new_name in {".", ".."}:
        raise ValueError("Invalid catalog name")
    target = safe_library_path(root, (PurePosixPath(relative).parent / new_name.strip()).as_posix())
    source.rename(target)
    return target


def delete_empty_catalog(root: Path, relative: str) -> None:
    path = safe_library_path(root, relative)
    if path == root or not path.is_dir():
        raise ValueError("Catalog does not exist")
    if any(path.iterdir()):
        raise ValueError("Only empty catalogs can be deleted")
    path.rmdir()
