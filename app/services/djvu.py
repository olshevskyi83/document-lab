from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from app.models import ExtractionResult
from app.services.commands import CommandCancelledError, run_command
from app.services.ocr import ocr_image
from app.services.ocr_language import (
    analyze_language,
    choose_auto_ocr_languages,
    expected_script_for_languages,
    ocr_plausibility,
)
from app.services.quality import analyze_page_coverage, count_page_markers, evaluate_text_quality


ProgressCallback = Callable[[str, str | None, int | None], None]


def djvu_page_count(
    path: Path, timeout: int, cancel_check: Callable[[], bool] | None = None
) -> int | None:
    result = run_command(
        ["djvused", str(path), "-e", "n"], timeout=timeout, cancel_check=cancel_check
    )
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def extract_hidden_text(
    path: Path,
    page_count: int | None,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    if not page_count:
        result = run_command(["djvutxt", str(path)], timeout=timeout, cancel_check=cancel_check)
        return result.stdout
    pages: list[str] = []
    for number in range(1, page_count + 1):
        result = run_command(
            ["djvutxt", f"--page={number}", str(path)],
            timeout=timeout,
            cancel_check=cancel_check,
        )
        pages.append(f"--- PAGE {number} ---\n{result.stdout.strip()}")
    return "\n\n".join(pages)


def process_djvu(
    path: Path,
    languages: list[str],
    timeout: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ExtractionResult:
    if progress_callback:
        progress_callback("analyzing", "Аналіз DJVU", 10)
    page_count = djvu_page_count(path, timeout, cancel_check)
    if progress_callback:
        progress_callback("djvu_hidden_text", "Витягування прихованого тексту", 20)
    hidden = extract_hidden_text(path, page_count, timeout, cancel_check)
    quality = evaluate_text_quality(hidden, page_count)
    hidden_coverage = analyze_page_coverage(hidden, page_count)
    hidden_markers = count_page_markers(hidden)
    warnings = list(quality.warnings)
    if page_count is None:
        warnings.append("DJVU page count is unknown because djvused did not return a usable value")
    if page_count and hidden_markers != page_count:
        warnings.append(f"DJVU page-marker mismatch: expected {page_count}, found {hidden_markers}")
    if quality.sufficient and not (hidden_coverage and hidden_coverage.partially_scanned):
        profile = analyze_language(hidden)
        return ExtractionResult(
            text=hidden,
            method="djvu_hidden_text",
            page_count=page_count,
            initial_text_page_count=hidden_coverage.text_page_count if hidden_coverage else None,
            initial_text_coverage=hidden_coverage.ratio if hidden_coverage else None,
            text_page_count=hidden_coverage.text_page_count if hidden_coverage else None,
            text_coverage=hidden_coverage.ratio if hidden_coverage else None,
            detected_script=profile.script,
            detected_language=profile.language,
            ocr_plausibility=ocr_plausibility(hidden),
            warnings=warnings,
        )
    warnings.append("DJVU hidden text was absent or insufficient; OCR fallback used")
    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="document-lab-djvu-") as temporary:
        pattern = Path(temporary) / "page-%06d.tif"
        if progress_callback:
            progress_callback("rendering", "Рендеринг сторінок", 25)
        run_command(
            ["ddjvu", "-format=tiff", "-eachpage", str(path), str(pattern)],
            timeout=timeout,
            cancel_check=cancel_check,
        )
        images = sorted(Path(temporary).glob("page-*.tif"))
        if not images:
            raise RuntimeError("ddjvu produced no page images")
        selected_languages = languages
        if not selected_languages:
            sample_images = images[:3]
            if len(images) > 3:
                sample_images = [images[0], images[len(images) // 2], images[-1]]
            selection = choose_auto_ocr_languages(
                hint_text=hidden,
                sample_images=sample_images,
                ocr_sample=lambda image, candidate: ocr_image(
                    image, candidate, timeout, cancel_check
                ),
            )
            selected_languages = selection.languages
            warnings.extend(selection.warnings)
        for number, image in enumerate(images, 1):
            if cancel_check and cancel_check():
                raise CommandCancelledError("DJVU OCR cancelled")
            pages.append(
                f"--- PAGE {number} ---\n"
                f"{ocr_image(image, selected_languages, timeout, cancel_check).strip()}"
            )
            if progress_callback:
                total = page_count or len(images)
                progress = 25 + int(50 * number / total)
                progress_callback("djvu_ocr", f"Сторінка {number} з {total}", progress)
    text = "\n\n".join(pages)
    profile = analyze_language(text)
    plausibility = ocr_plausibility(
        text, expected_script_for_languages(selected_languages)
    )
    if plausibility < 0.65:
        warnings.append("OCR text may be corrupted or wrong language model was used")
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
        ocr_languages=selected_languages,
        ocr_page_count=len(pages),
        ocr_strategy="djvu_tesseract_all_pages",
        detected_script=profile.script,
        detected_language=profile.language,
        ocr_plausibility=plausibility,
        warnings=warnings,
    )
