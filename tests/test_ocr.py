from pathlib import Path

from app.services.ocr import (
    build_ocr_pdf_command,
    detect_ghostscript_version,
    ghostscript_redo_affected,
    select_ocr_pdf_strategy,
)


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


def test_affected_ghostscript_uses_force_ocr_compatibility_strategy():
    strategy = select_ocr_pdf_strategy(
        Path("source.pdf"), Path("target.pdf"), ["eng"], redo=True, ghostscript_version="10.02.0"
    )
    assert strategy.name == "force_ocr_ghostscript_compat"
    assert "--force-ocr" in strategy.command
    assert "--redo-ocr" not in strategy.command
    assert "--deskew" not in strategy.command


def test_unknown_ghostscript_uses_conservative_strategy():
    strategy = select_ocr_pdf_strategy(
        Path("source.pdf"), Path("target.pdf"), [], redo=True, ghostscript_version=None
    )
    assert strategy.name == "force_ocr_ghostscript_unknown"
    assert "--redo-ocr" not in strategy.command


def test_unparseable_ghostscript_version_is_conservative():
    strategy = select_ocr_pdf_strategy(
        Path("source.pdf"), Path("target.pdf"), [], redo=True, ghostscript_version="unknown"
    )
    assert strategy.name == "force_ocr_ghostscript_unknown"
    assert "--redo-ocr" not in strategy.command


def test_ghostscript_detection_failure_returns_unknown(monkeypatch):
    from app.services import ocr
    from app.services.commands import CommandError

    monkeypatch.setattr(ocr, "run_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(CommandError("no gs")))
    assert detect_ghostscript_version(10) is None


def test_ghostscript_affected_version_range():
    assert ghostscript_redo_affected("10.00.0")
    assert ghostscript_redo_affected("10.01.2")
    assert ghostscript_redo_affected("10.02.0")
    assert not ghostscript_redo_affected("9.56.1")
    assert not ghostscript_redo_affected("10.03.0")
