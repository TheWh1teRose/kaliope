"""Language detection (§1.4).

Stopword-profile scoring over the languages in ``resources.py``. Two signals:
the share of tokens that are stopwords of a candidate language, and a small
contribution from script-level character frequencies, which separates languages
whose stopword sets overlap. Confidence is the margin between the best and the
runner-up, so a document that scores two languages equally reports low
confidence instead of picking one.

No external model and no network call — detection runs before any LLM does, and
must work on a document whose language is not in the table (it returns
``UNKNOWN_LANGUAGE`` with zero confidence, and every consumer degrades).
"""

from __future__ import annotations

import re
from collections import Counter

from app.lang.resources import LANGUAGES, UNKNOWN_LANGUAGE
from app.schemas.document import LanguageDetection

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

#: Characters that occur far more often in one language than in the others.
#: Purely orthographic, and derived from the alphabets themselves.
_CHAR_HINTS: dict[str, str] = {
    "de": "äöüßÄÖÜ",
    "fr": "àâçéèêëîïôùûœ",
    "es": "áéíóúñ¿¡",
    "it": "àèéìòù",
    "pt": "ãõçáéíóúâê",
    "nl": "ij",
    "en": "",
}

#: Below this many tokens the stopword statistics are noise. Kept low because
#: slide exports and abstracts are legitimately sparse (§5.3), and reporting an
#: undetectable language costs every downstream language-specific step.
_MIN_TOKENS = 20


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def detect_language(text: str) -> LanguageDetection:
    tokens = tokenize(text)
    if len(tokens) < _MIN_TOKENS:
        return LanguageDetection(language=UNKNOWN_LANGUAGE, confidence=0.0)

    counts = Counter(tokens)
    total = len(tokens)
    lowered = text.lower()
    text_len = max(len(lowered), 1)

    scores: dict[str, float] = {}
    for code, res in LANGUAGES.items():
        hits = sum(n for word, n in counts.items() if word in res.stopwords)
        stopword_share = hits / total
        hint_chars = _CHAR_HINTS.get(code, "")
        hint_share = sum(lowered.count(ch) for ch in hint_chars) / text_len if hint_chars else 0.0
        # The hint term is a tie-breaker, not a decision: capped so that
        # orthography alone can never outvote the stopword evidence.
        scores[code] = stopword_share + min(hint_share * 4.0, 0.08)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_code, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    # A document in an unlisted language scores near zero against every profile.
    if best_score < 0.06:
        return LanguageDetection(
            language=UNKNOWN_LANGUAGE,
            confidence=0.0,
            alternatives={c: round(s, 4) for c, s in ranked[:3]},
        )

    margin = (best_score - runner_up) / best_score if best_score else 0.0
    confidence = round(min(1.0, 0.5 * min(best_score / 0.25, 1.0) + 0.5 * margin), 4)
    return LanguageDetection(
        language=best_code,
        confidence=confidence,
        alternatives={c: round(s, 4) for c, s in ranked[:3]},
    )
