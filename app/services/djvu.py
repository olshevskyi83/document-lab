from __future__ import annotations

import tempfile
from pathlib import Path

from app.models import ExtractionResult
from app.services.commands import run_command
from app.services.ocr import ocr_image
from app.services.quality import analyze_page_coverage, count_page_markers, evaluate_text_quality


def djvu_page_count(path: Path, timeout: int) -> int | None:
    result = run_command(["djvused", str(path), "-e", "n"], timeout=timeout)
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def extract_hidden_text(path: Path, page_count: int | None, timeout: int) -> str:
    if not page_count:
        result = run_command(["djvutxt", str(path)], timeout=timeout)
        return result.stdout
    pages: list[str] = []
    for number in range(1, page_count + 1):
        result = run_command(["djvutxt", f"--page={number}", str(path)], timeout=timeout)
        pages.append(f"--- PAGE {number} ---\n{result.stdout.strip()}")
    return "\n\n".join(pages)


def process_djvu(path: Path, languages: list[str], timeout: int) -> ExtractionResult:
    page_count = djvu_page_count(path, timeout)
    hidden = extract_hidden_text(path, page_count, timeout)
    quality = evaluate_text_quality(hidden, page_count)
    hidden_coverage = analyze_page_coverage(hidden, page_count)
    hidden_markers = count_page_markers(hidden)
    warnings = list(quality.warnings)
    if page_count is None:
        warnings.append("DJVU page count is unknown because djvused did not return a usable value")
    if page_count and hidden_markers != page_count:
        warnings.append(f"DJVU page-marker mismatch: expected {page_count}, found {hidden_markers}")
    if quality.sufficient and not (hidden_coverage and hidden_coverage.partially_scanned):
        return ExtractionResult(
            text=hidden,
            method="djvu_hidden_text",
            page_count=page_count,
            initial_text_page_count=hidden_coverage.text_page_count if hidden_coverage else None,
            initial_text_coverage=hidden_coverage.ratio if hidden_coverage else None,
            text_page_count=hidden_coverage.text_page_count if hidden_coverage else None,
            text_coverage=hidden_coverage.ratio if hidden_coverage else None,
            warnings=warnings,
        )
    warnings.append("DJVU hidden text was absent or insufficient; OCR fallback used")
    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="document-lab-djvu-") as temporary:
        pattern = Path(temporary) / "page-%06d.tif"
        run_command(["ddjvu", "-format=tiff", "-eachpage", str(path), str(pattern)], timeout=timeout)
        images = sorted(Path(temporary).glob("page-*.tif"))
        if not images:
            raise RuntimeError("ddjvu produced no page images")
        for number, image in enumerate(images, 1):
            pages.append(f"--- PAGE {number} ---\n{ocr_image(image, languages, timeout).strip()}")
    text = "\n\n".join(pages)
    final_page_count = page_count or len(pages)
    final_coverage = analyze_page_coverage(text, final_page_count)
    marker_count = count_page_markers(text)
    if marker_count != final_page_count:
        warnings.append(f"DJVU OCR page-marker mismatch: expected {final_page_count}, found {marker_count}")
    if final_coverage and final_coverage.ratio < 1:
        warnings.append(
            f"DJVU extraction incomplete: {final_coverage.text_page_count}/{final_coverage.page_count} "
            f"pages contain meaningful text ({final_coverage.ratio:.1%} coverage)"
        )
    return ExtractionResult(
        text=text,
        method="djvu_ocr",
        page_count=final_page_count,
        initial_text_page_count=hidden_coverage.text_page_count if hidden_coverage else None,
        initial_text_coverage=hidden_coverage.ratio if hidden_coverage else None,
        text_page_count=final_coverage.text_page_count if final_coverage else None,
        text_coverage=final_coverage.ratio if final_coverage else None,
        partially_scanned=bool(hidden_coverage and hidden_coverage.partially_scanned),
        ocr_used=True,
        ocr_languages=languages,
        ocr_page_count=len(pages),
        ocr_strategy="djvu_tesseract_all_pages",
        warnings=warnings,
    )
