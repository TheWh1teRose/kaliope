"""Turn a VS answer into candidates, tolerantly.

A malformed answer is the normal case with long, multi-version output, so
almost everything is a warning rather than a failure: the user should still see
the complete versions that did come back. Only an answer with no usable version
at all raises :class:`VSParseError`.

Warnings are German because they are shown in the experiment screen.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.experiments.verbalized_sampling.options import VSOptions
from app.llm.base import Completion, LLMError, parse_json
from app.pipeline.nodes.script import _closest_speaker

#: Sum of probabilities above which the answer is flagged. The paper's
#: definition is relative to the full distribution, so sums below 1 are fine.
PROBABILITY_SUM_LIMIT = 1.05

_CUTOFF_STOPS = frozenset({"max_tokens", "length", "MAX_TOKENS"})
_WS = re.compile(r"\s+")


class VSParseError(ValueError):
    """The answer held no usable version. The raw text stays in the run's traces."""


class Citation(BaseModel):
    block_id: str
    quote: str


class CandidateSegment(BaseModel):
    speaker: str
    text: str
    kind: Literal["claim", "pedagogy"]
    citations: list[Citation] = Field(default_factory=list)


class Candidate(BaseModel):
    index: int
    probability: float | None
    segments: list[CandidateSegment]
    #: Per-candidate notes, e.g. "doppelt".
    flags: list[str] = Field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(segment.text.split()) for segment in self.segments)


class ParsedAnswer(BaseModel):
    candidates: list[Candidate]
    warnings: list[str] = Field(default_factory=list)


def parse_completion(
    completion: Completion, options: VSOptions, speakers: list[str] | None = None
) -> ParsedAnswer:
    """Parse one VS call's completion. Raises :class:`VSParseError` on nothing usable."""
    try:
        payload = _responses_payload(completion.text)
    except LLMError as exc:
        raise VSParseError(f"Die Antwort war kein gültiges JSON: {exc}") from exc
    return parse_payload(
        payload,
        options,
        speakers=speakers,
        cutoff=completion.stop_reason is None or completion.stop_reason in _CUTOFF_STOPS,
    )


def parse_payload(
    payload: Any,
    options: VSOptions,
    *,
    speakers: list[str] | None = None,
    cutoff: bool = False,
) -> ParsedAnswer:
    """Normalise an already-decoded answer from one call.

    A candidate counts when it is an object with non-empty segments. A missing
    or unreadable probability stays null. ``cutoff`` is true when the call
    stopped for length.
    """
    warnings: list[str] = []
    items = payload.get("responses") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise VSParseError('Die Antwort enthält keine Liste "responses".')

    candidates: list[Candidate] = []
    raw_probabilities: list[float | None] = []
    percent_tokens: list[bool] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        segments = _segments(item, speakers)
        if not segments:
            dropped += 1
            continue
        value, marked_percent = _probability(item.get("probability"))
        raw_probabilities.append(value)
        percent_tokens.append(marked_percent)
        candidates.append(Candidate(index=len(candidates), probability=None, segments=segments))

    if dropped:
        warnings.append(f"{dropped} Fassung(en) ohne verwertbaren Text verworfen.")
    if not candidates:
        raise VSParseError("Die Antwort enthält keine verwertbare Fassung.")

    for candidate, value in zip(
        candidates,
        _normalise_probabilities(raw_probabilities, percent_tokens, warnings),
        strict=True,
    ):
        candidate.probability = value

    _check(candidates, warnings)
    if len(candidates) < options.k:
        warnings.append(f"{len(candidates)} Fassungen statt {options.k}.")
    elif cutoff:
        warnings.append(
            "Die Antwort wurde abgeschnitten. Max. Ausgabetokens erhöhen oder k senken."
        )
    return ParsedAnswer(candidates=candidates, warnings=warnings)


# ------------------------------------------------------------------ helpers


def _responses_payload(text: str) -> Any:
    """The last JSON object in ``text`` that has a ``responses`` list."""
    chosen: Any = None
    marker = '"responses"'
    start = 0
    while True:
        index = text.find(marker, start)
        if index < 0:
            break
        brace = text.rfind("{", 0, index)
        if brace >= 0:
            try:
                data = parse_json(text[brace:])
            except LLMError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("responses"), list):
                chosen = data
        start = index + len(marker)
    if chosen is None:
        raise VSParseError('Die Antwort enthält keine Liste "responses".')
    return chosen


def _segments(item: Any, speakers: list[str] | None) -> list[CandidateSegment]:
    if not isinstance(item, dict):
        return []
    raw = item.get("segments")
    if not isinstance(raw, list):
        return []

    segments: list[CandidateSegment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        speaker = str(entry.get("speaker", "")).strip()
        if speakers:
            speaker = _closest_speaker(speaker, speakers)
        segments.append(
            CandidateSegment(
                speaker=speaker or "?",
                text=text,
                kind="claim" if str(entry.get("kind")) == "claim" else "pedagogy",
                citations=_citations(entry.get("citations")),
            )
        )
    return segments


def _citations(raw: Any) -> list[Citation]:
    if not isinstance(raw, list):
        return []
    out: list[Citation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("block_id", "")).strip()
        if block_id:
            out.append(Citation(block_id=block_id, quote=str(entry.get("quote", "")).strip()))
    return out


def _probability(raw: Any) -> tuple[float | None, bool]:
    """Return the number and whether its raw token was written as a percent."""
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None, False
    if isinstance(raw, int | float):
        return float(raw), False
    token = raw.strip().replace(",", ".")
    marked_percent = token.endswith("%")
    if marked_percent:
        token = token[:-1].strip()
    try:
        return float(token), marked_percent
    except ValueError:
        return None, False


def _normalise_probabilities(
    values: list[float | None], percent_tokens: list[bool], warnings: list[str]
) -> list[float | None]:
    present = [value for value in values if value is not None]
    batch_percent = bool(present) and all(value >= 2 for value in present)
    scaled: list[float | None] = []
    converted = False
    for value, marked_percent in zip(values, percent_tokens, strict=True):
        if value is None:
            scaled.append(None)
            continue
        if marked_percent or batch_percent:
            scaled.append(value / 100)
            converted = True
        else:
            scaled.append(value)
    if converted:
        warnings.append("Wahrscheinlichkeiten kamen als Prozent und wurden in 0–1 umgerechnet.")
    out: list[float | None] = []
    clamped = False
    for value in scaled:
        if value is None:
            out.append(None)
            continue
        bounded = min(1.0, max(0.0, value))
        clamped = clamped or bounded != value
        out.append(round(bounded, 4))
    if clamped:
        warnings.append("Wahrscheinlichkeiten außerhalb von 0–1 wurden begrenzt.")
    return out


def _check(candidates: list[Candidate], warnings: list[str]) -> None:
    probabilities = [c.probability for c in candidates if c.probability is not None]
    missing = len(candidates) - len(probabilities)
    if missing:
        warnings.append(f"{missing} Fassung(en) ohne Wahrscheinlichkeit.")
    total = sum(probabilities)
    if total > PROBABILITY_SUM_LIMIT:
        shown = f"{total:.2f}".replace(".", ",")
        warnings.append(f"Summe der Wahrscheinlichkeiten {shown} liegt über 1.")
    if len(probabilities) > 1 and len(set(probabilities)) == 1:
        warnings.append("Alle Fassungen haben dieselbe Wahrscheinlichkeit.")

    seen: dict[str, int] = {}
    for candidate in candidates:
        key = _WS.sub(" ", " ".join(s.text for s in candidate.segments)).strip().casefold()
        if key in seen:
            candidate.flags.append("doppelt")
            warnings.append(f"Fassung #{candidate.index + 1} ist gleich wie #{seen[key] + 1}.")
        else:
            seen[key] = candidate.index
