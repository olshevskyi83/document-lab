from app.services import pdf


def page(number, words=100):
    return f"--- PAGE {number} ---\n" + "meaningful " * words


def test_partial_pdf_coverage_triggers_ocr(monkeypatch, tmp_path):
    source = tmp_path / "partial.pdf"
    source.write_bytes(b"%PDF-1.7")
    before = "\n".join([page(1), page(2), page(3)])
    after = "\n".join([page(1), page(2), page(3), page(4)])
    extracted = iter([before, after])
    ocr_calls = []
    monkeypatch.setattr(pdf, "pdf_info", lambda *_: {"pages": "4"})
    monkeypatch.setattr(pdf, "extract_pdf_text", lambda *_: next(extracted))
    monkeypatch.setattr(pdf, "ocr_pdf", lambda *args: ocr_calls.append(args))
    result = pdf.process_pdf(source, ["eng"], 60)
    assert ocr_calls
    assert result.method == "pdf_partial_ocr"
    assert result.ocr_used
    assert result.partially_scanned
    assert result.initial_text_page_count == 3
    assert result.initial_text_coverage == 0.75
    assert result.text_page_count == 4
    assert result.text_coverage == 1.0
    assert any("Partial OCR fallback" in warning for warning in result.warnings)
