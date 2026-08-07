import pytest

from app.services.library import (
    create_catalog,
    delete_empty_catalog,
    discover_catalogs,
    rename_catalog,
    safe_library_path,
    scan_documents,
)


def test_safe_library_path_blocks_escape(tmp_path):
    root = tmp_path / "Library"
    root.mkdir()
    assert safe_library_path(root, "Biology/Mycology") == root / "Biology/Mycology"
    for unsafe in ("../outside", "/etc", "Biology/../../outside", "C:\\Windows"):
        with pytest.raises(ValueError):
            safe_library_path(root, unsafe)


def test_safe_library_path_blocks_symlink_components(tmp_path):
    root = tmp_path / "Library"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symbolic"):
        safe_library_path(root, "link/new")


def test_catalog_lifecycle_and_discovery(tmp_path):
    root = tmp_path / "Library"
    root.mkdir()
    create_catalog(root, "Biology/Mycology")
    assert discover_catalogs(root) == ["Biology", "Biology/Mycology"]
    renamed = rename_catalog(root, "Biology/Mycology", "Fungi")
    assert renamed == root / "Biology/Fungi"
    delete_empty_catalog(root, "Biology/Fungi")
    assert discover_catalogs(root) == ["Biology"]


def test_delete_rejects_nonempty_catalog(tmp_path):
    root = tmp_path / "Library"
    catalog = root / "Books"
    catalog.mkdir(parents=True)
    (catalog / "book.pdf").write_bytes(b"%PDF-")
    with pytest.raises(ValueError, match="empty"):
        delete_empty_catalog(root, "Books")


def test_scan_is_recursive_and_dynamic(tmp_path):
    root = tmp_path / "Library"
    nested = root / "Any" / "Future" / "Category"
    nested.mkdir(parents=True)
    (nested / "book.epub").write_bytes(b"PK\x03\x04")
    (nested / "notes.md").write_text("text")
    (nested / "ignore.jpg").write_bytes(b"x")
    assert [path.name for path in scan_documents(root)] == ["book.epub", "notes.md"]
