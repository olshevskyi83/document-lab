from __future__ import annotations

import re
import tempfile
from pathlib import Path

from app.models import ExtractionResult
from app.services.commands import run_command
from app.services.ocr import ocr_pdf
from app.services.quality import analyze_page_coverage, evaluate_text_quality


def pdf_info(path: Path, timeout: int) -> dict[str, str]:
    result = run_command(["pdfinfo", str(path)], timeout=timeout)
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip().lower()] = value.strip()
    return info


def extract_pdf_text(path: Path, timeout: int, page_count: int | None = None) -> str:
    result = run_command(["pdftotext", "-layout", str(path), "-"], timeout=timeout)
    pages = result.stdout.split("\f")
    if page_count:
        pages = (pages + [""] * page_count)[:page_count]
        return "\n".join(
            f"--- PAGE {number} ---\n{page.strip()}" for number, page in enumerate(pages, 1)
        )
    return "\n".join(
        f"--- PAGE {number} ---\n{page.strip()}" for number, page in enumerate(pages, 1) if page.strip()
    )


def process_pdf(path: Path, languages: list[str], timeout: int) -> ExtractionResult:
    info = pdf_info(path, timeout)
    page_count = int(info.get("pages", "0")) or None
    initial_text = extract_pdf_text(path, timeout, page_count)
    initial_quality = evaluate_text_quality(initial_text, page_count)
    initial_coverage = analyze_page_coverage(initial_text, page_count)
    warnings = list(initial_quality.warnings)
    if initial_quality.sufficient and not (initial_coverage and initial_coverage.partially_scanned):
        return ExtractionResult(
            text=initial_text,
            method="pdf_text",
            page_count=page_count,
            initial_text_page_count=initial_coverage.text_page_count if initial_coverage else None,
            initial_text_coverage=initial_coverage.ratio if initial_coverage else None,
            text_page_count=initial_coverage.text_page_count if initial_coverage else None,
            text_coverage=initial_coverage.ratio if initial_coverage else None,
            title=info.get("title", ""),
            author=info.get("author", ""),
            warnings=warnings,
        )
    partial_fallback = bool(initial_coverage and initial_coverage.partially_scanned)
    if partial_fallback:
        warnings.append(
            f"Partial OCR fallback started: {initial_coverage.text_page_count}/{initial_coverage.page_count} "
            f"pages had meaningful text before OCR ({initial_coverage.ratio:.1%} coverage)"
        )
    with tempfile.TemporaryDirectory(prefix="document-lab-pdf-") as temporary:
        ocr_path = Path(temporary) / "ocr.pdf"
        ocr_pdf(path, ocr_path, languages, timeout, redo=partial_fallback)
        text = extract_pdf_text(ocr_path, timeout, page_count)
    final_coverage = analyze_page_coverage(text, page_count)
    if page_count and len(re.findall(r"^--- PAGE", text, re.MULTILINE)) < page_count:
        warnings.append("OCR output contains fewer text-bearing pages than the PDF page count")
    return ExtractionResult(
        text=text,
        method="pdf_partial_ocr" if partial_fallback else "pdf_ocr",
        page_count=page_count,
        initial_text_page_count=initial_coverage.text_page_count if initial_coverage else None,
        initial_text_coverage=initial_coverage.ratio if initial_coverage else None,
        text_page_count=final_coverage.text_page_count if final_coverage else None,
        text_coverage=final_coverage.ratio if final_coverage else None,
        partially_scanned=partial_fallback,
        ocr_used=True,
        ocr_languages=languages,
        title=info.get("title", ""),
        author=info.get("author", ""),
        warnings=warnings,
    )
