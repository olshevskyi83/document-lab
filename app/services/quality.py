from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import QualityReport
from app.services.ocr_language import analyze_language, ocr_plausibility


PAGE_MARKER = re.compile(r"^--- PAGE (\d+) ---$", re.MULTILINE)


def count_page_markers(text: str) -> int:
    return len({int(number) for number in PAGE_MARKER.findall(text)})


@dataclass(frozen=True)
class PageCoverage:
    page_count: int
    text_page_count: int
    ratio: float
    partially_scanned: bool


def analyze_page_coverage(text: str, page_count: int | None) -> PageCoverage | None:
    if not page_count:
        return None
    parts = PAGE_MARKER.split(text)
    meaningful_pages: set[int] = set()
    for index in range(1, len(parts), 2):
        page_number = int(parts[index])
        content = parts[index + 1] if index + 1 < len(parts) else ""
        characters = len(content.strip())
        words = len(re.findall(r"\b[^\W_]+\b", content, re.UNICODE))
        if characters >= 40 and words >= 8:
            meaningful_pages.add(page_number)
    text_page_count = len(meaningful_pages)
    ratio = round(text_page_count / page_count, 3)
    return PageCoverage(
        page_count=page_count,
        text_page_count=text_page_count,
        ratio=ratio,
        partially_scanned=0 < text_page_count < page_count,
    )


def evaluate_text_quality(
    text: str, page_count: int | None = None, expected_script: str | None = None
) -> QualityReport:
    stripped = text.strip()
    characters = len(stripped)
    words = len(re.findall(r"\b[^\W_]+(?:[-'][^\W_]+)*\b", stripped, re.UNICODE))
    printable = sum(char.isprintable() or char in "\n\t" for char in stripped)
    printable_ratio = printable / characters if characters else 0.0
    coverage = analyze_page_coverage(stripped, page_count)
    pages_with_text = coverage.text_page_count if coverage else None
    density_target = max(80, (page_count or 1) * 25)
    length_score = min(1.0, characters / density_target)
    word_score = min(1.0, words / max(20, (page_count or 1) * 8))
    profile = analyze_language(stripped)
    plausibility = ocr_plausibility(stripped, expected_script)
    structural_score = (0.45 * length_score) + (0.35 * word_score) + (0.20 * printable_ratio)
    score = round((0.40 * structural_score) + (0.60 * plausibility), 3)
    warnings: list[str] = []
    if printable_ratio < 0.9:
        warnings.append("Low printable-character ratio")
    if plausibility < 0.65:
        warnings.append("OCR text may be corrupted or wrong language model was used")
    if coverage and coverage.partially_scanned:
        warnings.append(
            f"Partial text coverage: {coverage.text_page_count}/{coverage.page_count} pages "
            f"({coverage.ratio:.1%}) contain meaningful text"
        )
    sufficient = (
        characters >= density_target
        and words >= max(20, (page_count or 1) * 8)
        and printable_ratio >= 0.9
        and plausibility >= 0.65
    )
    return QualityReport(
        score=score,
        sufficient=sufficient,
        character_count=characters,
        word_count=words,
        printable_ratio=round(printable_ratio, 3),
        detected_script=profile.script,
        detected_language=profile.language,
        ocr_plausibility=plausibility,
        pages_with_text=pages_with_text,
        page_count=page_count,
        text_coverage=coverage.ratio if coverage else None,
        partially_scanned=coverage.partially_scanned if coverage else False,
        warnings=warnings,
    )
