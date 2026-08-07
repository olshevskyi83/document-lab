from __future__ import annotations

from pathlib import Path


SUPPORTED_FORMATS = {"pdf", "djvu", "txt", "md", "epub", "docx"}
EXTENSION_ALIASES = {".djv": "djvu", ".markdown": "md"}
SIGNATURES = (
    (b"%PDF-", "pdf"),
    (b"AT&TFORM", "djvu"),
    (b"PK\x03\x04", "zip"),
)


def detect_format(path: Path) -> str:
    extension = EXTENSION_ALIASES.get(path.suffix.lower(), path.suffix.lower().lstrip("."))
    with path.open("rb") as source:
        header = source.read(16)
    signature = next((name for prefix, name in SIGNATURES if header.startswith(prefix)), None)
    if signature in {"pdf", "djvu"}:
        return signature
    if signature == "zip" and extension in {"docx", "epub"}:
        return extension
    if extension in SUPPORTED_FORMATS:
        return extension
    raise ValueError(f"Unsupported document format: {path.suffix or 'unknown'}")
