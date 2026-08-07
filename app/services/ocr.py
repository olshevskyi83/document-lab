from __future__ import annotations

from pathlib import Path

from app.services.commands import run_command


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


def ocr_pdf(source: Path, target: Path, languages: list[str], timeout: int) -> None:
    arguments = ["ocrmypdf", "--redo-ocr", "--deskew", "--rotate-pages"]
    for language in languages:
        arguments.extend(["-l", language])
    arguments.extend([str(source), str(target)])
    run_command(arguments, timeout=timeout)
