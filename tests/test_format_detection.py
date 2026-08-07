import pytest

from app.services.format_detection import detect_format


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("renamed.bin", b"%PDF-1.7", "pdf"),
        ("book.djvu", b"AT&TFORM", "djvu"),
        ("note.txt", b"hello", "txt"),
        ("note.markdown", b"# Hello", "md"),
        ("book.epub", b"PK\x03\x04", "epub"),
        ("document.docx", b"PK\x03\x04", "docx"),
    ],
)
def test_format_detection(tmp_path, name, content, expected):
    path = tmp_path / name
    path.write_bytes(content)
    assert detect_format(path) == expected


def test_unsupported_format(tmp_path):
    path = tmp_path / "image.jpg"
    path.write_bytes(b"image")
    with pytest.raises(ValueError):
        detect_format(path)
