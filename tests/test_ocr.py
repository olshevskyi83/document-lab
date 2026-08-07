from pathlib import Path

from app.services.ocr import build_ocr_pdf_command


def test_redo_ocr_command_excludes_incompatible_cleanup_flags():
    command = build_ocr_pdf_command(Path("source.pdf"), Path("target.pdf"), ["ukr", "eng"], redo=True)
    assert "--redo-ocr" in command
    for incompatible in ("--deskew", "--clean-final", "--remove-background", "--force-ocr"):
        assert incompatible not in command
    assert command[command.index("-l") + 1] == "ukr+eng"


def test_full_scan_ocr_uses_compatible_cleanup_mode():
    command = build_ocr_pdf_command(Path("source.pdf"), Path("target.pdf"), ["eng"], redo=False)
    assert "--force-ocr" in command
    assert "--deskew" in command
    assert "--redo-ocr" not in command
