from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.services.commands import (
    CommandError,
    CommandExecutionError,
    CommandTimeoutError,
    run_command,
)


OCR_LANGUAGE_OPTIONS = {
    "auto": [],
    "ukr": ["ukr"],
    "rus": ["rus"],
    "eng": ["eng"],
    "deu": ["deu"],
    "spa": ["spa"],
    "ukr+eng": ["ukr", "eng"],
    "rus+eng": ["rus", "eng"],
    "deu+eng": ["deu", "eng"],
    "spa+eng": ["spa", "eng"],
}


def parse_ocr_languages(value: str) -> list[str]:
    if value not in OCR_LANGUAGE_OPTIONS:
        raise ValueError("Unsupported OCR language selection")
    return OCR_LANGUAGE_OPTIONS[value]


def ocr_image(image_path: Path, languages: list[str], timeout: int) -> str:
    arguments = ["tesseract", str(image_path), "stdout"]
    if languages:
        arguments.extend(["-l", "+".join(languages)])
    result = run_command(arguments, timeout=timeout)
    return result.stdout


@dataclass(frozen=True)
class OCRPDFStrategy:
    name: str
    command: list[str]
    ghostscript_version: str | None


class OCRPDFError(CommandError):
    pass


class OCRPDFTimeoutError(OCRPDFError):
    pass


class OCREngineError(OCRPDFError):
    pass


class PDFPostprocessingError(OCRPDFError):
    pass


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def ghostscript_redo_affected(version: str | None) -> bool:
    parsed = parse_version(version or "")
    return parsed is not None and (10, 0, 0) <= parsed <= (10, 2, 0)


def detect_ghostscript_version(timeout: int) -> str | None:
    try:
        result = run_command(["gs", "--version"], timeout=timeout)
    except CommandError:
        return None
    return result.stdout.strip() or None


def select_ocr_pdf_strategy(
    source: Path,
    target: Path,
    languages: list[str],
    *,
    redo: bool,
    ghostscript_version: str | None,
) -> OCRPDFStrategy:
    parsed_version = parse_version(ghostscript_version or "")
    if redo and parsed_version and not ghostscript_redo_affected(ghostscript_version):
        arguments = ["ocrmypdf", "--redo-ocr", "--rotate-pages"]
        name = "redo_ocr"
    elif redo:
        arguments = ["ocrmypdf", "--force-ocr", "--rotate-pages"]
        name = "force_ocr_ghostscript_compat" if parsed_version else "force_ocr_ghostscript_unknown"
    else:
        arguments = ["ocrmypdf", "--force-ocr", "--deskew", "--rotate-pages"]
        name = "force_ocr_full_scan"
    if languages:
        arguments.extend(["-l", "+".join(languages)])
    arguments.extend(["--output-type", "pdf"])
    arguments.extend([str(source), str(target)])
    return OCRPDFStrategy(name=name, command=arguments, ghostscript_version=ghostscript_version)


def build_ocr_pdf_command(
    source: Path,
    target: Path,
    languages: list[str],
    *,
    redo: bool,
    ghostscript_version: str | None = "10.03.0",
) -> list[str]:
    return select_ocr_pdf_strategy(
        source, target, languages, redo=redo, ghostscript_version=ghostscript_version
    ).command


def ocr_pdf(
    source: Path, target: Path, languages: list[str], timeout: int, *, redo: bool = False
) -> OCRPDFStrategy:
    version = detect_ghostscript_version(timeout)
    strategy = select_ocr_pdf_strategy(
        source, target, languages, redo=redo, ghostscript_version=version
    )
    try:
        run_command(strategy.command, timeout=timeout)
    except CommandTimeoutError as exc:
        raise OCRPDFTimeoutError(f"OCRmyPDF command timeout: {exc}") from exc
    except CommandExecutionError as exc:
        stderr = exc.stderr
        normalized = stderr.lower()
        postprocessing_markers = (
            "ghostscript",
            "pdf/a",
            "postprocess",
            "post-process",
            "runpdf",
            "qpdf",
            "pdf rendering",
        )
        if any(marker in normalized for marker in postprocessing_markers):
            raise PDFPostprocessingError(f"PDF postprocessing failure: {stderr}") from exc
        raise OCREngineError(f"OCR/Tesseract failure: {stderr}") from exc
    return strategy
