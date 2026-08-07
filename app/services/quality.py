from __future__ import annotations

import re

from app.models import QualityReport


PAGE_MARKER = re.compile(r"^--- PAGE (\d+) ---$", re.MULTILINE)


def evaluate_text_quality(text: str, page_count: int | None = None) -> QualityReport:
    stripped = text.strip()
    characters = len(stripped)
    words = len(re.findall(r"\b[^\W_]+(?:[-'][^\W_]+)*\b", stripped, re.UNICODE))
    printable = sum(char.isprintable() or char in "\n\t" for char in stripped)
    printable_ratio = printable / characters if characters else 0.0
    pages = PAGE_MARKER.split(stripped)[1::2]
    chunks = PAGE_MARKER.split(stripped)[2::2]
    pages_with_text = sum(len(chunk.strip()) >= 40 for chunk in chunks) if pages else None
    density_target = max(80, (page_count or 1) * 25)
    length_score = min(1.0, characters / density_target)
    word_score = min(1.0, words / max(20, (page_count or 1) * 8))
    score = round((0.45 * length_score) + (0.35 * word_score) + (0.20 * printable_ratio), 3)
    warnings: list[str] = []
    if printable_ratio < 0.9:
        warnings.append("Low printable-character ratio")
    if page_count and pages_with_text is not None and pages_with_text < page_count:
        warnings.append("Only part of the document appears to contain useful text")
    sufficient = characters >= density_target and words >= max(20, (page_count or 1) * 8) and printable_ratio >= 0.9
    return QualityReport(
        score=score,
        sufficient=sufficient,
        character_count=characters,
        word_count=words,
        printable_ratio=round(printable_ratio, 3),
        pages_with_text=pages_with_text,
        page_count=page_count,
        warnings=warnings,
    )
