from app.services import pdf
from app.services.ocr import OCRPDFStrategy


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
    def fake_ocr(*args, **kwargs):
        ocr_calls.append((args, kwargs))
        return OCRPDFStrategy("redo_ocr", ["ocrmypdf"], "10.03.0")

    monkeypatch.setattr(pdf, "ocr_pdf", fake_ocr)
    result = pdf.process_pdf(source, ["eng"], 60)
    assert ocr_calls
    assert ocr_calls[0][1]["redo"] is True
    assert result.method == "pdf_partial_ocr"
    assert result.ocr_used
    assert result.partially_scanned
    assert result.initial_text_page_count == 3
    assert result.initial_text_coverage == 0.75
    assert result.text_page_count == 4
    assert result.text_coverage == 1.0
    assert result.ocr_page_count == 1
    assert result.ocr_strategy == "redo_ocr"
    assert any("Partial OCR fallback" in warning for warning in result.warnings)


def test_full_text_pdf_reports_complete_coverage_without_ocr(monkeypatch, tmp_path):
    source = tmp_path / "full.pdf"
    source.write_bytes(b"%PDF-1.7")
    text = "\n".join([page(1), page(2), page(3)])
    monkeypatch.setattr(pdf, "pdf_info", lambda *_: {"pages": "3"})
    monkeypatch.setattr(pdf, "extract_pdf_text", lambda *_: text)
    monkeypatch.setattr(pdf, "ocr_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    result = pdf.process_pdf(source, ["eng"], 60)
    assert result.method == "pdf_text"
    assert not result.ocr_used
    assert result.page_count == 3
    assert result.text_page_count == 3
    assert result.text_coverage == 1.0
    assert result.ocr_page_count == 0
