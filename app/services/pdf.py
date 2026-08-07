from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from app.models import ExtractionResult
from app.services.commands import run_command
from app.services.ocr import ocr_pdf
from app.services.quality import analyze_page_coverage, count_page_markers, evaluate_text_quality


ProgressCallback = Callable[[str, str | None, int | None], None]


def pdf_info(
    path: Path, timeout: int, cancel_check: Callable[[], bool] | None = None
) -> dict[str, str]:
    result = run_command(["pdfinfo", str(path)], timeout=timeout, cancel_check=cancel_check)
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip().lower()] = value.strip()
    return info


def extract_pdf_text(
    path: Path,
    timeout: int,
    page_count: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    result = run_command(
        ["pdftotext", "-layout", str(path), "-"],
        timeout=timeout,
        cancel_check=cancel_check,
    )
    pages = result.stdout.split("\f")
    if page_count:
        pages = (pages + [""] * page_count)[:page_count]
        return "\n".join(
            f"--- PAGE {number} ---\n{page.strip()}" for number, page in enumerate(pages, 1)
        )
    return "\n".join(
        f"--- PAGE {number} ---\n{page.strip()}" for number, page in enumerate(pages, 1) if page.strip()
    )


def process_pdf(
    path: Path,
    languages: list[str],
    timeout: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ExtractionResult:
    if progress_callback:
        progress_callback("analyzing", "Аналіз PDF", 10)
    info = pdf_info(path, timeout, cancel_check)
    page_value = info.get("pages", "").strip()
    page_count = int(page_value) if page_value.isdigit() and int(page_value) > 0 else None
    if progress_callback:
        progress_callback("pdf_text", "Витягування текстового шару", 20)
    initial_text = extract_pdf_text(path, timeout, page_count, cancel_check)
    initial_quality = evaluate_text_quality(initial_text, page_count)
    initial_coverage = analyze_page_coverage(initial_text, page_count)
    warnings = list(initial_quality.warnings)
    if page_count is None:
        warnings.append("PDF page count is unknown because pdfinfo did not return a usable value")
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
        if progress_callback:
            page_detail = f"{page_count} сторінок" if page_count else "Кількість сторінок невідома"
            progress_callback("pdf_ocr", page_detail, None)
        strategy = ocr_pdf(
            path,
            ocr_path,
            languages,
            timeout,
            redo=partial_fallback,
            cancel_check=cancel_check,
        )
        if progress_callback:
            progress_callback("postprocessing", "Витягування OCR-тексту", 80)
        text = extract_pdf_text(ocr_path, timeout, page_count, cancel_check)
    final_coverage = analyze_page_coverage(text, page_count)
    marker_count = count_page_markers(text)
    if page_count and marker_count != page_count:
        warnings.append(f"PDF page-marker mismatch: expected {page_count}, found {marker_count}")
    if final_coverage and final_coverage.ratio < 1:
        warnings.append(
            f"Extraction incomplete after OCR: {final_coverage.text_page_count}/{final_coverage.page_count} "
            f"pages contain meaningful text ({final_coverage.ratio:.1%} coverage)"
        )
    if strategy.name.startswith("force_ocr"):
        ocr_page_count = page_count or (final_coverage.text_page_count if final_coverage else 0)
    else:
        before = initial_coverage.text_page_count if initial_coverage else 0
        ocr_page_count = max(0, page_count - before) if page_count else 0
    if partial_fallback and strategy.name != "redo_ocr":
        warnings.append(
            f"Partial-PDF redo was replaced with {strategy.name} for Ghostscript "
            f"{strategy.ghostscript_version or 'unknown'} compatibility"
        )
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
        ocr_page_count=ocr_page_count,
        ocr_strategy=strategy.name,
        ghostscript_version=strategy.ghostscript_version,
        title=info.get("title", ""),
        author=info.get("author", ""),
        warnings=warnings,
    )
