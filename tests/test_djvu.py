from types import SimpleNamespace

from app.services import djvu


def page(number, words=100):
    return f"--- PAGE {number} ---\n" + "meaningful " * words


def test_djvu_partial_hidden_text_falls_back_and_reports_coverage(monkeypatch, tmp_path):
    source = tmp_path / "partial.djvu"
    source.write_bytes(b"AT&TFORM")
    hidden = "\n".join([page(1), page(2)])
    monkeypatch.setattr(djvu, "djvu_page_count", lambda *_: 3)
    monkeypatch.setattr(djvu, "extract_hidden_text", lambda *_: hidden)

    def fake_run(arguments, **_kwargs):
        pattern = arguments[-1]
        for number in range(1, 4):
            path = pattern.replace("%06d", f"{number:06d}")
            open(path, "wb").close()
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(djvu, "run_command", fake_run)
    monkeypatch.setattr(
        djvu,
        "ocr_image",
        lambda image, *_: "meaningful " * 100 if image.name != "page-000003.tif" else "",
    )
    result = djvu.process_djvu(source, ["eng"], 60)
    assert result.method == "djvu_ocr"
    assert result.page_count == 3
    assert result.ocr_page_count == 3
    assert result.text_page_count == 2
    assert result.text_coverage == 0.667
    assert any("page-marker mismatch" in warning for warning in result.warnings)
    assert any("extraction incomplete" in warning.lower() for warning in result.warnings)
