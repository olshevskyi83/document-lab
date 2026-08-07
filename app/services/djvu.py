from __future__ import annotations

import tempfile
from pathlib import Path

from app.models import ExtractionResult
from app.services.commands import run_command
from app.services.ocr import ocr_image
from app.services.quality import evaluate_text_quality


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
    if quality.sufficient:
        return ExtractionResult(
            text=hidden,
            method="djvu_hidden_text",
            page_count=page_count,
            warnings=quality.warnings,
        )
    warnings = list(quality.warnings)
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
    return ExtractionResult(
        text="\n\n".join(pages),
        method="djvu_ocr",
        page_count=page_count or len(pages),
        ocr_used=True,
        ocr_languages=languages,
        warnings=warnings,
    )
