from pathlib import Path

from app.services.ocr_language import (
    analyze_language,
    choose_auto_ocr_languages,
    languages_for_profile,
    ocr_plausibility,
)
from app.services.quality import evaluate_text_quality


RUSSIAN = (
    "Это русский научный текст о природе и развитии организма. "
    "В книге есть важные сведения для исследования. "
) * 8
UKRAINIAN = (
    "Це український науковий текст, що містить важливі відомості "
    "про природу, її розвиток і ґрунти. "
) * 8
MIXED_SCIENCE = (
    "Это исследование fungi Candida albicans и DNA marker analysis "
    "для научной работы. "
) * 8
LATIN_LIKE_GARBAGE = (
    "MCHbIue YHCO MNOPAXKOB TpHOOB cTBamu MCHbIue YHCO "
    "MNOPAXKOB TpHOOB cTBamu "
) * 20


def test_russian_cyrillic_selects_russian_model():
    profile = analyze_language(RUSSIAN)
    assert profile.script == "Cyrillic"
    assert profile.language == "Russian"
    assert languages_for_profile(profile) in (["rus"], ["rus", "eng"])
    selection = choose_auto_ocr_languages(hint_text=RUSSIAN)
    assert selection.languages in (["rus"], ["rus", "eng"])


def test_ukrainian_cyrillic_selects_ukrainian_model():
    profile = analyze_language(UKRAINIAN)
    assert profile.script == "Cyrillic"
    assert profile.language == "Ukrainian"
    assert languages_for_profile(profile) in (["ukr"], ["ukr", "eng"])
    selection = choose_auto_ocr_languages(hint_text=UKRAINIAN)
    assert selection.languages in (["ukr"], ["ukr", "eng"])


def test_mixed_scientific_text_includes_english_with_cyrillic():
    profile = analyze_language(MIXED_SCIENCE)
    assert profile.script == "Mixed Cyrillic/Latin"
    languages = languages_for_profile(profile)
    assert "rus" in languages or "ukr" in languages
    assert "eng" in languages


def test_latin_like_wrong_language_ocr_is_implausible():
    assert ocr_plausibility(LATIN_LIKE_GARBAGE, "Cyrillic") < 0.5
    report = evaluate_text_quality(
        LATIN_LIKE_GARBAGE, page_count=1, expected_script="Cyrillic"
    )
    assert report.score < 0.7
    assert not report.sufficient
    assert "OCR text may be corrupted or wrong language model was used" in report.warnings


def test_high_character_count_alone_cannot_produce_quality_one():
    report = evaluate_text_quality(LATIN_LIKE_GARBAGE * 20, page_count=4)
    assert report.character_count > 1000
    assert report.score < 1.0
    assert not report.sufficient


def test_auto_sample_chooses_cyrillic_model_not_tesseract_default():
    images = [Path("page-1.png"), Path("page-2.png")]
    calls = []

    def sample_ocr(_image: Path, languages: list[str]) -> str:
        calls.append(languages)
        if "rus" in languages:
            return RUSSIAN
        if "ukr" in languages:
            return UKRAINIAN
        return LATIN_LIKE_GARBAGE

    selection = choose_auto_ocr_languages(
        sample_images=images,
        ocr_sample=sample_ocr,
    )
    assert selection.languages
    assert "rus" in selection.languages or "ukr" in selection.languages
    assert selection.detected_script == "Cyrillic"
    assert [] not in calls


def test_uncertain_cyrillic_uses_both_language_models():
    neutral = "Мама читала слова про книгу море автора текст работу. " * 10
    profile = analyze_language(neutral)
    assert profile.script == "Cyrillic"
    assert profile.language == "Russian/Ukrainian uncertain"
    assert languages_for_profile(profile) == ["rus", "ukr"]
