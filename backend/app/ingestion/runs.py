"""The :class:`TextRun` edit primitive (§5.2).

Every normalization step is expressed as a list of ``(start, end, replacement)``
edits against a run's text. :func:`apply_edits` is the only place that rewrites
a run, and it is responsible for keeping ``char_x`` — the per-character x
boundaries — aligned with the new text. That is what makes anchors survive
normalization instead of being invalidated by it.

Doing this as plain string operations over concatenated page text would destroy
the mapping back to the PDF, which §5.2 forbids in as many words.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

from app.schemas.document import TextRun


class TextEdit(NamedTuple):
    """Replace ``text[start:end]`` with ``replacement``."""

    start: int
    end: int
    replacement: str


class EditLedger:
    """Counts characters removed, by cause, for the INV-3 conservation check."""

    def __init__(self) -> None:
        self.removed: dict[str, int] = {}
        self.added: dict[str, int] = {}

    def remove(self, cause: str, count: int) -> None:
        if count > 0:
            self.removed[cause] = self.removed.get(cause, 0) + count

    def add(self, cause: str, count: int) -> None:
        if count > 0:
            self.added[cause] = self.added.get(cause, 0) + count

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())

    @property
    def total_added(self) -> int:
        return sum(self.added.values())

    def merge(self, other: EditLedger) -> None:
        for cause, count in other.removed.items():
            self.remove(cause, count)
        for cause, count in other.added.items():
            self.add(cause, count)


def apply_edits(run: TextRun, edits: list[TextEdit]) -> TextRun:
    """Return a copy of ``run`` with ``edits`` applied and geometry preserved.

    Edits must not overlap. The x-interval of a replaced range is divided
    evenly among the replacement characters, which is exact for a deletion and
    a good approximation for a ligature expansion — the only two cases that
    change length in this pipeline.
    """
    if not edits:
        return run

    ordered = sorted(edits, key=lambda e: e.start)
    _assert_disjoint(ordered)

    text = run.text
    has_geom = run.has_geometry()
    char_x = run.char_x if has_geom else []

    out_chars: list[str] = []
    out_x: list[float] = []
    pos = 0

    for start, end, replacement in ordered:
        if start < pos:
            raise ValueError("overlapping edits")
        for i in range(pos, start):
            out_chars.append(text[i])
            if has_geom:
                out_x.append(char_x[i])
        if replacement:
            if has_geom:
                x0, x1 = char_x[start], char_x[end]
                step = (x1 - x0) / len(replacement)
                for j, ch in enumerate(replacement):
                    out_chars.append(ch)
                    out_x.append(x0 + j * step)
            else:
                out_chars.extend(replacement)
        pos = end

    for i in range(pos, len(text)):
        out_chars.append(text[i])
        if has_geom:
            out_x.append(char_x[i])
    if has_geom:
        out_x.append(char_x[len(text)])

    new_text = "".join(out_chars)
    return run.model_copy(update={"text": new_text, "char_x": out_x if has_geom else []})


def _assert_disjoint(edits: list[TextEdit]) -> None:
    last_end = -1
    for start, end, _ in edits:
        if start < last_end:
            raise ValueError(f"overlapping edits at {start} (previous ended at {last_end})")
        if end < start:
            raise ValueError("edit end before start")
        last_end = end


def trim_run(run: TextRun, ledger: EditLedger | None = None) -> TextRun:
    """Strip leading and trailing whitespace, keeping geometry aligned."""
    text = run.text
    stripped = text.strip()
    if stripped == text:
        return run
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    edits = []
    if lead:
        edits.append(TextEdit(0, lead, ""))
    if trail:
        edits.append(TextEdit(len(text) - trail, len(text), ""))
    if ledger is not None:
        ledger.remove("whitespace_trim", lead + trail)
    return apply_edits(run, edits)


_WS_RUN = re.compile(r"\s{2,}|[\t\r\n\f\v   ]")


def collapse_whitespace(run: TextRun, ledger: EditLedger | None = None) -> TextRun:
    """Collapse every internal whitespace sequence to a single space."""
    edits: list[TextEdit] = []
    removed = 0
    for match in _WS_RUN.finditer(run.text):
        span = match.group(0)
        if span == " ":
            continue
        edits.append(TextEdit(match.start(), match.end(), " "))
        removed += len(span) - 1
    if ledger is not None:
        ledger.remove("whitespace_collapse", removed)
    return apply_edits(run, edits)


def normalization_key(text: str) -> str:
    """Aggressive key used only for clustering and comparison, never stored."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def total_chars(runs: list[TextRun]) -> int:
    return sum(len(r.text) for r in runs)


def concat_text(runs: list[TextRun], separator: str = " ") -> str:
    return separator.join(r.text for r in runs)
