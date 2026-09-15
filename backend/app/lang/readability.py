"""Per-language readability formulas (§8, gate G7).

Directional only. A language with no formula here is not scored, and G7 skips
cleanly (AC-GATE-4) rather than applying an English formula to non-English text.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel

_SENTENCE_SPLIT = re.compile(r"[.!?…]+(?:\s|$)")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_VOWEL_GROUP = re.compile(r"[aeiouyäöüàâéèêëíîïóôõúûùæœ]+", re.IGNORECASE)


class ReadabilityScore(BaseModel):
    formula: str
    score: float
    #: Interpretation band the score falls into, for display only.
    label: str
    words: int
    sentences: int
    syllables: int


class ReadabilityBand(BaseModel):
    """Acceptable range for gate G7. Configurable per run."""

    min_score: float
    max_score: float


def count_syllables(word: str) -> int:
    """Vowel-group count. Crude, adequate for a directional metric."""
    groups = _VOWEL_GROUP.findall(word)
    return max(1, len(groups))


def _basic_stats(text: str) -> tuple[int, int, int]:
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    words = _WORD_RE.findall(text)
    syllables = sum(count_syllables(w) for w in words)
    return len(words), max(1, len(sentences)), syllables


def _flesch_english(text: str) -> ReadabilityScore:
    words, sentences, syllables = _basic_stats(text)
    if words == 0:
        return ReadabilityScore(
            formula="flesch_reading_ease_en",
            score=0.0,
            label="unscorable",
            words=0,
            sentences=sentences,
            syllables=0,
        )
    asl = words / sentences
    asw = syllables / words
    score = 206.835 - 1.015 * asl - 84.6 * asw
    return ReadabilityScore(
        formula="flesch_reading_ease_en",
        score=round(score, 2),
        label=_flesch_label(score),
        words=words,
        sentences=sentences,
        syllables=syllables,
    )


def _flesch_german(text: str) -> ReadabilityScore:
    """Amstad's German adaptation of Flesch Reading Ease."""
    words, sentences, syllables = _basic_stats(text)
    if words == 0:
        return ReadabilityScore(
            formula="flesch_reading_ease_de",
            score=0.0,
            label="unscorable",
            words=0,
            sentences=sentences,
            syllables=0,
        )
    asl = words / sentences
    asw = syllables / words
    score = 180.0 - asl - 58.5 * asw
    return ReadabilityScore(
        formula="flesch_reading_ease_de",
        score=round(score, 2),
        label=_flesch_label(score),
        words=words,
        sentences=sentences,
        syllables=syllables,
    )


def _flesch_label(score: float) -> str:
    if score >= 80:
        return "very_easy"
    if score >= 60:
        return "easy"
    if score >= 50:
        return "medium"
    if score >= 30:
        return "difficult"
    return "very_difficult"


FORMULAS: dict[str, Callable[[str], ReadabilityScore]] = {
    "de": _flesch_german,
    "en": _flesch_english,
}

#: Spoken-word bands. Wider than the written-text conventions because narration
#: is naturally more readable than the prose these formulas were built on.
DEFAULT_BANDS: dict[str, ReadabilityBand] = {
    "de": ReadabilityBand(min_score=30.0, max_score=85.0),
    "en": ReadabilityBand(min_score=40.0, max_score=95.0),
}


def has_formula(language: str) -> bool:
    return language.lower().split("-")[0] in FORMULAS


def score(language: str, text: str) -> ReadabilityScore | None:
    fn = FORMULAS.get(language.lower().split("-")[0])
    return fn(text) if fn else None


def band_for(language: str) -> ReadabilityBand | None:
    return DEFAULT_BANDS.get(language.lower().split("-")[0])
