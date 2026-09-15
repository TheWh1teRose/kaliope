"""Normalization (§5.4, steps 2–4).

Every transformation is expressed as edits on individual :class:`TextRun`
objects so that page and bbox provenance survives for every character that
survives. Removals are counted into an :class:`EditLedger`, which is what makes
the INV-3 character-conservation check meaningful rather than decorative.

The pipeline is idempotent (INV-6): after one pass there are no soft hyphens,
no line-break hyphens, no ligatures and no whitespace runs left to act on.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.ingestion.runs import EditLedger, TextEdit, apply_edits, collapse_whitespace, trim_run
from app.lang.resources import conjunctions_for
from app.schemas.document import TextRun

SOFT_HYPHEN = "­"

#: Ligatures PDF fonts routinely emit as single code points.
_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
    "Ĳ": "IJ",
    "ĳ": "ij",
    "Œ": "OE",
    "œ": "oe",
    "Æ": "AE",
    "æ": "ae",
}

#: Typographic variants folded to their ASCII equivalent, so that a 6-gram
#: comparison in gate G3 cannot be defeated by a different apostrophe.
_PUNCTUATION = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "′": "'",
    "″": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "–",
    "—": "—",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
}

#: Zero-width characters carry no text and no geometry.
_ZERO_WIDTH = {"​", "‌", "‍", "﻿"}

#: Letterspacing: share of single-character tokens above which a run is treated
#: as letterspaced, and the minimum token count for the test to apply (§5.4.2).
_LETTERSPACE_SHARE = 0.60
_LETTERSPACE_MIN_TOKENS = 4

_TOKEN_RE = re.compile(r"\S+")
_HYPHEN_END_RE = re.compile(r"([^\W\d_])[-‐‑](\s*)$", re.UNICODE)
_INLINE_HYPHEN_BREAK = re.compile(r"([^\W\d_])[-‐‑]\n\s*([^\W\d_])", re.UNICODE)
_WORD_START_RE = re.compile(r"^\s*([^\W\d_]+)", re.UNICODE)


@dataclass
class NormalizeResult:
    runs: list[TextRun]
    ledger: EditLedger
    letterspaced_runs: int = 0
    dehyphenated_joins: int = 0
    suspended_compounds: int = 0


def normalize_runs(runs: list[TextRun], language: str) -> NormalizeResult:
    """Run steps 2–4 of §5.4 over an already ordered run list."""
    ledger = EditLedger()
    working = [_unicode_normalize(run, ledger) for run in runs]

    letterspaced = 0
    rebuilt: list[TextRun] = []
    for run in working:
        collapsed, changed = _collapse_letterspacing(run, ledger)
        letterspaced += int(changed)
        rebuilt.append(collapsed)

    rebuilt = [_join_inline_hyphen_breaks(run, ledger) for run in rebuilt]
    rebuilt, joins, suspended = _dehyphenate_across_runs(rebuilt, language, ledger)

    final: list[TextRun] = []
    for run in rebuilt:
        run = collapse_whitespace(run, ledger)
        run = trim_run(run, ledger)
        if not run.text:
            ledger.remove("empty_run", 0)
            continue
        final.append(run)

    return NormalizeResult(
        runs=final,
        ledger=ledger,
        letterspaced_runs=letterspaced,
        dehyphenated_joins=joins,
        suspended_compounds=suspended,
    )


# ------------------------------------------------------- step 4: unicode


def _unicode_normalize(run: TextRun, ledger: EditLedger) -> TextRun:
    edits: list[TextEdit] = []
    removed = 0
    for index, char in enumerate(run.text):
        if char == SOFT_HYPHEN or char in _ZERO_WIDTH:
            edits.append(TextEdit(index, index + 1, ""))
            removed += 1
            continue
        replacement = _LIGATURES.get(char) or _PUNCTUATION.get(char)
        if replacement is not None and replacement != char:
            edits.append(TextEdit(index, index + 1, replacement))
            continue
        composed = unicodedata.normalize("NFC", char)
        if composed != char:
            edits.append(TextEdit(index, index + 1, composed))

    ledger.remove("soft_hyphen_and_zero_width", removed)
    out = apply_edits(run, edits)

    # A composed sequence can span two code points (base + combining mark); the
    # per-character pass above cannot see it, so compose the whole string and
    # only accept the result when it did not change the length.
    composed_text = unicodedata.normalize("NFC", out.text)
    if composed_text != out.text and len(composed_text) == len(out.text):
        out = out.model_copy(update={"text": composed_text})
    return out


# ------------------------------------------------- step 2: letterspacing


def _collapse_letterspacing(run: TextRun, ledger: EditLedger) -> tuple[TextRun, bool]:
    tokens = [(m.start(), m.end()) for m in _TOKEN_RE.finditer(run.text)]
    if len(tokens) < _LETTERSPACE_MIN_TOKENS:
        return run, False
    singles = sum(1 for start, end in tokens if end - start == 1)
    if singles / len(tokens) < _LETTERSPACE_SHARE:
        return run, False

    edits: list[TextEdit] = []
    removed = 0
    for (start_a, end_a), (start_b, end_b) in zip(tokens, tokens[1:], strict=False):
        if end_a - start_a == 1 and end_b - start_b == 1 and start_b > end_a:
            edits.append(TextEdit(end_a, start_b, ""))
            removed += start_b - end_a
    if not edits:
        return run, False
    ledger.remove("letterspacing", removed)
    return apply_edits(run, edits), True


# ------------------------------------------------- step 3: dehyphenation


def _join_inline_hyphen_breaks(run: TextRun, ledger: EditLedger) -> TextRun:
    """Handle ``<letter>-\\n<letter>`` inside a single run's text."""
    edits: list[TextEdit] = []
    removed = 0
    for match in _INLINE_HYPHEN_BREAK.finditer(run.text):
        # Drop the hyphen and the line break, keeping both letters.
        start = match.start() + 1
        end = match.end() - 1
        edits.append(TextEdit(start, end, ""))
        removed += end - start
    if not edits:
        return run
    ledger.remove("hyphen_line_break", removed)
    return apply_edits(run, edits)


def _dehyphenate_across_runs(
    runs: list[TextRun], language: str, ledger: EditLedger
) -> tuple[list[TextRun], int, int]:
    """Join ``<letter>-`` at the end of a line with the next line's word.

    The suspended-compound exception (§5.4.3) is what keeps German constructions
    like ``Hormon-\\nund Nervensystem`` intact: when the following token is a
    coordinating conjunction the hyphen is meaningful and the join is refused.
    """
    conjunctions = conjunctions_for(language)
    out = list(runs)
    joins = 0
    suspended = 0
    removed = 0

    for index in range(len(out) - 1):
        current = out[index]
        following = out[index + 1]
        match = _HYPHEN_END_RE.search(current.text)
        if match is None:
            continue
        if not _is_line_break(current, following):
            continue
        next_word = _WORD_START_RE.match(following.text)
        if next_word is None:
            continue
        if next_word.group(1).casefold() in conjunctions:
            suspended += 1
            continue

        hyphen_start = match.start() + 1
        out[index] = apply_edits(current, [TextEdit(hyphen_start, len(current.text), "")])
        removed += len(current.text) - hyphen_start
        out[index] = out[index].model_copy(update={"tight_join_next": True})
        joins += 1

    ledger.remove("hyphen_line_break", removed)
    return out, joins, suspended


def _is_line_break(current: TextRun, following: TextRun) -> bool:
    """True when the two runs are not on the same visual line.

    An intra-line hyphen belongs to the word (``well-known`` split across two
    font spans) and must never be removed.
    """
    if current.page != following.page or current.column != following.column:
        return True
    top = max(current.bbox[1], following.bbox[1])
    bottom = min(current.bbox[3], following.bbox[3])
    if bottom <= top:
        return True
    smaller = min(current.bbox[3] - current.bbox[1], following.bbox[3] - following.bbox[1])
    if smaller <= 0:
        return True
    return (bottom - top) / smaller < 0.5


# ------------------------------------------------------------- utilities


def normalize_text(text: str, language: str = "und") -> str:
    """Apply the character-level parts of the pipeline to a bare string.

    Used by the anchor round-trip check to bring re-extracted PDF text onto the
    same footing as normalized block text (INV-4), and by gate comparisons.
    """
    run = TextRun(
        page=0,
        bbox=(0.0, 0.0, 0.0, 0.0),
        text=text,
        font_size=0.0,
        font_name="",
        bold=False,
        in_filled_rect=False,
        raw_index=0,
    )
    ledger = EditLedger()
    run = _unicode_normalize(run, ledger)
    run, _ = _collapse_letterspacing(run, ledger)
    run = _join_inline_hyphen_breaks(run, ledger)
    run = collapse_whitespace(run, ledger)
    return run.text.strip()


def has_residual_hyphenation(text: str) -> bool:
    """INV-2 helper."""
    return SOFT_HYPHEN in text or _INLINE_HYPHEN_BREAK.search(text) is not None
