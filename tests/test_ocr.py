from pathlib import Path

from app.services.ocr import (
    OCREngineError,
    OCRPDFTimeoutError,
    PDFPostprocessingError,
    build_ocr_pdf_command,
    detect_ghostscript_version,
    ghostscript_redo_affected,
    select_ocr_pdf_strategy,
)


def assert_plain_pdf_output(command):
    assert command[command.index("--output-type") + 1] == "pdf"
    assert not any(value.lower().startswith("pdfa") for value in command)


def test_redo_ocr_command_excludes_incompatible_cleanup_flags():
    command = build_ocr_pdf_command(Path("source.pdf"), Path("target.pdf"), ["ukr", "eng"], redo=True)
    assert "--redo-ocr" in command
    for incompatible in ("--deskew", "--clean-final", "--remove-background", "--force-ocr"):
        assert incompatible not in command
    assert command[command.index("-l") + 1] == "ukr+eng"
    assert_plain_pdf_output(command)


def test_full_scan_ocr_uses_compatible_cleanup_mode():
    command = build_ocr_pdf_command(Path("source.pdf"), Path("target.pdf"), ["eng"], redo=False)
    assert "--force-ocr" in command
    assert "--deskew" in command
    assert "--redo-ocr" not in command
    assert_plain_pdf_output(command)


def test_affected_ghostscript_uses_force_ocr_compatibility_strategy():
    strategy = select_ocr_pdf_strategy(
        Path("source.pdf"), Path("target.pdf"), ["eng"], redo=True, ghostscript_version="10.00.0"
    )
    assert strategy.name == "force_ocr_ghostscript_compat"
    assert "--force-ocr" in strategy.command
    assert "--redo-ocr" not in strategy.command
    assert "--deskew" not in strategy.command
    assert_plain_pdf_output(strategy.command)


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


def test_ocrmypdf_postprocessing_error_preserves_stderr(monkeypatch, tmp_path):
    from app.services import ocr
    from app.services.commands import CommandExecutionError

    calls = iter([
        type("Result", (), {"stdout": "10.00.0"})(),
        CommandExecutionError("ocrmypdf", 15, "Ghostscript PDF/A rendering failed /typecheck in --runpdf--"),
    ])

    def fake_run(*_args, **_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ocr, "run_command", fake_run)
    try:
        ocr.ocr_pdf(tmp_path / "in.pdf", tmp_path / "out.pdf", ["eng"], 10, redo=True)
    except PDFPostprocessingError as exc:
        assert "Ghostscript PDF/A rendering failed" in str(exc)
        assert "/typecheck in --runpdf--" in str(exc)
    else:
        raise AssertionError("Expected PDFPostprocessingError")


def test_ocrmypdf_nonzero_tesseract_failure_is_classified(monkeypatch, tmp_path):
    from app.services import ocr
    from app.services.commands import CommandExecutionError

    monkeypatch.setattr(ocr, "detect_ghostscript_version", lambda _timeout: "10.03.0")
    monkeypatch.setattr(
        ocr,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CommandExecutionError("ocrmypdf", 6, "Tesseract failed to process page 2")
        ),
    )
    try:
        ocr.ocr_pdf(tmp_path / "in.pdf", tmp_path / "out.pdf", ["eng"], 10)
    except OCREngineError as exc:
        assert "Tesseract failed to process page 2" in str(exc)
    else:
        raise AssertionError("Expected OCREngineError")


def test_ocrmypdf_timeout_is_classified(monkeypatch, tmp_path):
    from app.services import ocr
    from app.services.commands import CommandTimeoutError

    monkeypatch.setattr(ocr, "detect_ghostscript_version", lambda _timeout: "10.03.0")
    monkeypatch.setattr(
        ocr,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CommandTimeoutError("Command timed out: ocrmypdf")),
    )
    try:
        ocr.ocr_pdf(tmp_path / "in.pdf", tmp_path / "out.pdf", ["eng"], 10)
    except OCRPDFTimeoutError as exc:
        assert "command timeout" in str(exc)
    else:
        raise AssertionError("Expected OCRPDFTimeoutError")


def test_successful_tesseract_warning_is_not_fatal(monkeypatch, tmp_path):
    from app.services import ocr

    monkeypatch.setattr(ocr, "detect_ghostscript_version", lambda _timeout: "10.03.0")
    monkeypatch.setattr(
        ocr,
        "run_command",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": "", "stderr": "Too few characters. Skipping this page"}
        )(),
    )
    strategy = ocr.ocr_pdf(tmp_path / "in.pdf", tmp_path / "out.pdf", ["eng"], 10)
    assert strategy.name == "force_ocr_full_scan"
