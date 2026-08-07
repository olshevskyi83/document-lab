from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
LETTER_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
RUSSIAN_MARKERS = set("ыэёъ")
UKRAINIAN_MARKERS = set("іїєґ")
COMMON_WORDS = {
    "ru": {"и", "в", "не", "на", "что", "это", "как", "для", "из", "по", "к", "с"},
    "uk": {"і", "в", "не", "на", "що", "це", "як", "для", "із", "по", "до", "з"},
    "en": {"the", "and", "of", "to", "in", "is", "for", "with", "that", "meaningful"},
    "de": {"der", "die", "das", "und", "ist", "in", "von", "zu", "mit", "für"},
    "es": {"el", "la", "de", "que", "y", "en", "los", "para", "con", "una"},
}


@dataclass(frozen=True)
class LanguageProfile:
    script: str
    language: str
    latin_ratio: float
    cyrillic_ratio: float
    mixed_token_ratio: float


@dataclass(frozen=True)
class OCRLanguageSelection:
    languages: list[str]
    detected_script: str
    detected_language: str
    plausibility: float
    warnings: list[str]


def analyze_language(text: str) -> LanguageProfile:
    letters = [character for character in text if character.isalpha()]
    total = len(letters)
    cyrillic = sum(bool(CYRILLIC_RE.fullmatch(character)) for character in letters)
    latin = sum(bool(LATIN_RE.fullmatch(character)) for character in letters)
    cyrillic_ratio = cyrillic / total if total else 0.0
    latin_ratio = latin / total if total else 0.0
    if cyrillic_ratio >= 0.75:
        script = "Cyrillic"
    elif latin_ratio >= 0.75:
        script = "Latin"
    elif cyrillic_ratio >= 0.15 and latin_ratio >= 0.15:
        script = "Mixed Cyrillic/Latin"
    else:
        script = "Unknown"

    lowered = text.lower()
    russian = sum(lowered.count(character) for character in RUSSIAN_MARKERS)
    ukrainian = sum(lowered.count(character) for character in UKRAINIAN_MARKERS)
    tokens = LETTER_TOKEN_RE.findall(lowered)
    common_scores = {
        language: sum(token in vocabulary for token in tokens)
        for language, vocabulary in COMMON_WORDS.items()
    }
    if cyrillic_ratio >= 0.15:
        if ukrainian >= max(2, russian * 2):
            language = "Ukrainian"
        elif russian >= max(2, ukrainian * 2):
            language = "Russian"
        elif common_scores["uk"] > common_scores["ru"] * 1.4:
            language = "Ukrainian"
        elif common_scores["ru"] > common_scores["uk"] * 1.4:
            language = "Russian"
        else:
            language = "Russian/Ukrainian uncertain"
    elif latin_ratio >= 0.15:
        best = max(("en", "de", "es"), key=common_scores.get)
        labels = {"en": "English", "de": "German", "es": "Spanish"}
        language = labels[best] if common_scores[best] >= 2 else "Latin language uncertain"
    else:
        language = "Unknown"

    mixed_tokens = 0
    for token in tokens:
        has_cyrillic = bool(CYRILLIC_RE.search(token))
        has_latin = bool(LATIN_RE.search(token))
        mixed_tokens += has_cyrillic and has_latin
    return LanguageProfile(
        script=script,
        language=language,
        latin_ratio=round(latin_ratio, 3),
        cyrillic_ratio=round(cyrillic_ratio, 3),
        mixed_token_ratio=round(mixed_tokens / len(tokens), 3) if tokens else 0.0,
    )


def expected_script_for_languages(languages: list[str]) -> str | None:
    has_cyrillic = bool({"rus", "ukr"} & set(languages))
    has_latin = bool({"eng", "deu", "spa"} & set(languages))
    if has_cyrillic and has_latin:
        return "Mixed Cyrillic/Latin"
    if has_cyrillic:
        return "Cyrillic"
    if has_latin:
        return "Latin"
    return None


def ocr_plausibility(text: str, expected_script: str | None = None) -> float:
    profile = analyze_language(text)
    tokens = LETTER_TOKEN_RE.findall(text)
    if len(tokens) < 4:
        return 0.0
    letters = [character for character in text if character.isalpha()]
    uppercase_ratio = sum(character.isupper() for character in letters) / max(1, len(letters))
    vowel_sets = {
        "Cyrillic": set("аеёиоуыэюяіїєАЕЁИОУЫЭЮЯІЇЄ"),
        "Latin": set("aeiouyAEIOUYáéíóúüÁÉÍÓÚÜ"),
    }
    consonant_noise = 0
    malformed = 0
    suspicious_case = 0
    for token in re.findall(r"\S+", text):
        alphabetic = sum(character.isalpha() for character in token)
        if len(token) >= 4 and alphabetic / len(token) < 0.65:
            malformed += 1
    for token in tokens:
        script = "Cyrillic" if CYRILLIC_RE.search(token) else "Latin"
        vowels = vowel_sets[script]
        if len(token) >= 5 and not any(character in vowels for character in token):
            consonant_noise += 1
        uppercase = sum(character.isupper() for character in token)
        lowercase = sum(character.islower() for character in token)
        if len(token) >= 5 and uppercase >= 2 and lowercase >= 1:
            suspicious_case += 1

    score = 1.0
    score -= min(0.4, profile.mixed_token_ratio * 2.5)
    score -= min(0.3, consonant_noise / len(tokens) * 1.2)
    score -= min(0.25, malformed / max(1, len(tokens)) * 1.5)
    score -= min(0.35, suspicious_case / len(tokens) * 1.1)
    if uppercase_ratio > 0.35:
        score -= min(0.3, (uppercase_ratio - 0.35) * 0.8)
    if expected_script == "Cyrillic" and profile.cyrillic_ratio < 0.65:
        score -= 0.65
    elif expected_script == "Latin" and profile.latin_ratio < 0.65:
        score -= 0.65
    elif expected_script == "Mixed Cyrillic/Latin" and min(
        profile.cyrillic_ratio, profile.latin_ratio
    ) < 0.03:
        score -= 0.12
    return round(max(0.0, min(1.0, score)), 3)


def languages_for_profile(profile: LanguageProfile) -> list[str]:
    mixed = profile.script == "Mixed Cyrillic/Latin"
    if profile.language == "Russian":
        return ["rus", "eng"] if mixed else ["rus"]
    if profile.language == "Ukrainian":
        return ["ukr", "eng"] if mixed else ["ukr"]
    if profile.script in {"Cyrillic", "Mixed Cyrillic/Latin"}:
        return ["rus", "ukr", "eng"] if mixed else ["rus", "ukr"]
    if profile.language == "German":
        return ["deu"]
    if profile.language == "Spanish":
        return ["spa"]
    return ["eng"]


def choose_auto_ocr_languages(
    *,
    hint_text: str = "",
    sample_images: list[Path] | None = None,
    ocr_sample: Callable[[Path, list[str]], str] | None = None,
) -> OCRLanguageSelection:
    hint_profile = analyze_language(hint_text)
    if len(LETTER_TOKEN_RE.findall(hint_text)) >= 12 and hint_profile.script != "Unknown":
        languages = languages_for_profile(hint_profile)
        plausibility = ocr_plausibility(hint_text, expected_script_for_languages(languages))
        return OCRLanguageSelection(
            languages, hint_profile.script, hint_profile.language, plausibility, []
        )

    if not sample_images or not ocr_sample:
        return OCRLanguageSelection(
            ["eng"],
            hint_profile.script,
            hint_profile.language,
            ocr_plausibility(hint_text, "Latin"),
            ["Auto OCR had insufficient language evidence; English was selected"],
        )

    candidates = [["rus"], ["ukr"], ["rus", "ukr", "eng"], ["eng"]]
    results: list[tuple[float, list[str], str, LanguageProfile]] = []
    for languages in candidates:
        sample = "\n".join(ocr_sample(image, languages) for image in sample_images[:3])
        profile = analyze_language(sample)
        score = ocr_plausibility(sample, expected_script_for_languages(languages))
        if profile.language == "Russian" and "rus" in languages:
            score = min(1.0, score + 0.08)
        if profile.language == "Ukrainian" and "ukr" in languages:
            score = min(1.0, score + 0.08)
        results.append((score, languages, sample, profile))
    score, languages, _sample, profile = max(results, key=lambda result: result[0])
    if profile.language == "Russian/Ukrainian uncertain":
        languages = (
            ["rus", "ukr", "eng"]
            if profile.script == "Mixed Cyrillic/Latin"
            else ["rus", "ukr"]
        )
    warnings = []
    if score < 0.6:
        warnings.append("OCR language auto-detection confidence is low")
    return OCRLanguageSelection(
        languages=languages,
        detected_script=profile.script,
        detected_language=profile.language,
        plausibility=round(score, 3),
        warnings=warnings,
    )
