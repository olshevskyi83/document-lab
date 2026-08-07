from __future__ import annotations

import re
import tempfile
from pathlib import Path

from app.models import ExtractionResult
from app.services.commands import run_command
from app.services.ocr import ocr_pdf
from app.services.quality import evaluate_text_quality


def pdf_info(path: Path, timeout: int) -> dict[str, str]:
    result = run_command(["pdfinfo", str(path)], timeout=timeout)
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip().lower()] = value.strip()
    return info


def extract_pdf_text(path: Path, timeout: int) -> str:
    result = run_command(["pdftotext", "-layout", str(path), "-"], timeout=timeout)
    pages = result.stdout.split("\f")
    return "\n".join(
        f"--- PAGE {number} ---\n{page.strip()}" for number, page in enumerate(pages, 1) if page.strip()
    )


def process_pdf(path: Path, languages: list[str], timeout: int) -> ExtractionResult:
    info = pdf_info(path, timeout)
    page_count = int(info.get("pages", "0")) or None
    initial_text = extract_pdf_text(path, timeout)
    initial_quality = evaluate_text_quality(initial_text, page_count)
    warnings = list(initial_quality.warnings)
    if initial_quality.sufficient:
        return ExtractionResult(
            text=initial_text,
            method="pdf_text",
            page_count=page_count,
            title=info.get("title", ""),
            author=info.get("author", ""),
            warnings=warnings,
        )
    with tempfile.TemporaryDirectory(prefix="document-lab-pdf-") as temporary:
        ocr_path = Path(temporary) / "ocr.pdf"
        ocr_pdf(path, ocr_path, languages, timeout)
        text = extract_pdf_text(ocr_path, timeout)
    if page_count and len(re.findall(r"^--- PAGE", text, re.MULTILINE)) < page_count:
        warnings.append("OCR output contains fewer text-bearing pages than the PDF page count")
    return ExtractionResult(
        text=text,
        method="pdf_ocr",
        page_count=page_count,
        ocr_used=True,
        ocr_languages=languages,
        title=info.get("title", ""),
        author=info.get("author", ""),
        warnings=warnings,
    )
