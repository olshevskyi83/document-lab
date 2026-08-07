from app.services.quality import evaluate_text_quality
from app.services.text_cleanup import clean_text


def test_text_cleanup_preserves_paragraphs_and_repairs_hyphenation():
    raw = "  First   para-\ngraph.\r\n\r\n\r\nSecond\t paragraph.\x00 "
    assert clean_text(raw) == "First paragraph.\n\nSecond paragraph.\n"


def test_quality_rejects_empty_text():
    report = evaluate_text_quality("", page_count=2)
    assert not report.sufficient
    assert report.score < 0.5


def test_quality_accepts_substantial_text():
    text = " ".join(["meaningful"] * 100)
    report = evaluate_text_quality(text, page_count=1)
    assert report.sufficient
    assert report.score > 0.9


def test_quality_warns_about_partial_page_text():
    text = "--- PAGE 1 ---\n" + "word " * 100 + "\n--- PAGE 2 ---\n"
    report = evaluate_text_quality(text, page_count=2)
    assert any("part" in warning.lower() for warning in report.warnings)
