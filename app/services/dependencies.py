from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from app.services.commands import CommandError, run_command


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unavailable"


def command_version(arguments: list[str], timeout: int) -> str:
    try:
        result = run_command(arguments, timeout=timeout)
    except CommandError:
        return "unavailable"
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return first_line or "unknown"


def dependency_versions(timeout: int = 10) -> dict[str, str]:
    tesseract = command_version(["tesseract", "--version"], timeout)
    if tesseract.lower().startswith("tesseract "):
        tesseract = tesseract.split(maxsplit=1)[1]
    return {
        "ocrmypdf": package_version("ocrmypdf"),
        "pikepdf": package_version("pikepdf"),
        "ghostscript": command_version(["gs", "--version"], timeout),
        "tesseract": tesseract,
    }
