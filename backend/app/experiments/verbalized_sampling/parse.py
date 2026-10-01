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
from app.llm.base import Completion, LLMError
from app.pipeline.nodes.script import _closest_speaker

#: Sum of probabilities above which the answer is flagged. The paper's
#: definition is relative to the full distribution, so sums below 1 are fine.
PROBABILITY_SUM_LIMIT = 1.05

_LIST_KEYS = ("responses", "candidates", "versions")
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
        payload = completion.json_payload()
    except LLMError as exc:
        raise VSParseError(f"Die Antwort war kein gültiges JSON: {exc}") from exc
    return parse_payload(
        payload,
        options,
        speakers=speakers,
        truncated=completion.stop_reason == "max_tokens",
        source=completion.text,
    )


def parse_payload(
    payload: Any,
    options: VSOptions,
    *,
    speakers: list[str] | None = None,
    truncated: bool = False,
    source: str | None = None,
) -> ParsedAnswer:
    """Normalise an already-decoded answer from one call.

    ``truncated`` is the call's stop reason. ``source`` is the raw completion,
    used to drop a response only when the text ends inside that response.
    """
    warnings: list[str] = []

    items: Any
    if isinstance(payload, list):
        items = payload
        warnings.append('Antwort war eine bloße Liste statt {"responses": [...]}.')
    elif isinstance(payload, dict):
        items = None
        for key in _LIST_KEYS:
            if isinstance(payload.get(key), list):
                items = payload[key]
                if key != "responses":
                    warnings.append(f'Fassungen standen unter "{key}" statt "responses".')
                break
    else:
        items = None
    if not isinstance(items, list):
        raise VSParseError('Die Antwort enthält keine Liste "responses".')
    cut_off = False
    if truncated and source is not None and items:
        closed, inside = _response_closure(source)
        if inside and len(items) > closed:
            items = items[:closed]
            cut_off = True

    candidates: list[Candidate] = []
    raw_probabilities: list[float | None] = []
    percent_tokens: list[bool] = []
    dropped = 0
    for item in items:
        segments, text_only = _segments(item, speakers)
        if not segments:
            dropped += 1
            continue
        if text_only:
            warnings.append(
                f"Fassung #{len(candidates) + 1} kam als bloßer Text ohne Sprecher; "
                "sie steht als ein Abschnitt da."
            )
        value, marked_percent = _probability(
            item.get("probability") if isinstance(item, dict) else None
        )
        raw_probabilities.append(value)
        percent_tokens.append(marked_percent)
        candidates.append(Candidate(index=len(candidates), probability=None, segments=segments))

    if dropped:
        warnings.append(f"{dropped} Fassung(en) ohne verwertbaren Text verworfen.")
    if not candidates:
        if truncated:
            raise VSParseError(
                "Die Antwort wurde bei Max. Ausgabetokens abgeschnitten, bevor eine Fassung "
                "vollständig war. Max. Ausgabetokens erhöhen oder k senken."
            )
        raise VSParseError("Die Antwort enthält keine verwertbare Fassung.")

    for candidate, value in zip(
        candidates,
        _normalise_probabilities(raw_probabilities, percent_tokens, warnings),
        strict=True,
    ):
        candidate.probability = value

    _check(candidates, options, warnings)
    if cut_off:
        warnings.append(
            "Die Antwort wurde bei Max. Ausgabetokens abgeschnitten; die unvollständige letzte "
            "Fassung ist verworfen. Max. Ausgabetokens erhöhen oder k senken."
        )
    return ParsedAnswer(candidates=candidates, warnings=warnings)


# ------------------------------------------------------------------ helpers


def _response_closure(text: str) -> tuple[int, bool]:
    """Closed response objects, and whether the text ends inside one of them."""
    containers: list[dict[str, Any]] = []
    closed = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == '"':
            i += 1
            word: list[str] = []
            while i < n:
                if text[i] == "\\":
                    if i + 1 >= n:
                        return closed, depth > 0
                    word.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    break
                word.append(text[i])
                i += 1
            if i >= n:
                return closed, depth > 0
            i += 1
            frame = containers[-1] if containers else None
            if frame is not None and frame["phase"] == "key":
                frame["key"] = "".join(word)
                frame["phase"] = "colon"
            elif frame is not None and frame["phase"] == "value":
                frame["phase"] = "next"
            continue
        if char == "{":
            if (
                containers
                and containers[-1]["kind"] == "arr"
                and containers[-1]["responses"]
                and depth == 0
            ):
                depth = 1
            elif depth:
                depth += 1
            containers.append({"kind": "obj", "responses": False, "phase": "key"})
            i += 1
            continue
        if char == "}":
            if depth:
                depth -= 1
                if depth == 0:
                    closed += 1
            if containers:
                containers.pop()
            if containers:
                containers[-1]["phase"] = "next"
            i += 1
            continue
        if char == "[":
            key = containers[-1].get("key") if containers else None
            responses = not containers or (
                containers[-1]["kind"] == "obj"
                and containers[-1]["phase"] == "value"
                and key in _LIST_KEYS
                and not any(container["responses"] for container in containers)
            )
            containers.append({"kind": "arr", "responses": responses, "phase": "value"})
            i += 1
            continue
        if char == "]":
            if containers:
                containers.pop()
            if containers:
                containers[-1]["phase"] = "next"
            i += 1
            continue
        if char == ":" and containers and containers[-1]["phase"] == "colon":
            containers[-1]["phase"] = "value"
            i += 1
            continue
        if char == ",":
            if containers and containers[-1]["phase"] == "next":
                kind = containers[-1]["kind"]
                containers[-1]["phase"] = "key" if kind == "obj" else "value"
            i += 1
            continue
        if containers and containers[-1]["phase"] == "value" and (
            char in "-0123456789" or char in "tfn"
        ):
            i += 1
            while i < n and (text[i].isalnum() or text[i] in "+-.eE"):
                i += 1
            containers[-1]["phase"] = "next"
            continue
        i += 1
    return closed, depth > 0


def _segments(item: Any, speakers: list[str] | None) -> tuple[list[CandidateSegment], bool]:
    if isinstance(item, str):
        item = {"text": item}
    if not isinstance(item, dict):
        return [], False

    raw = item.get("segments")
    if not isinstance(raw, list):
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return [CandidateSegment(speaker="?", text=text.strip(), kind="pedagogy")], True
        return [], False

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
    return segments, False


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
    batch_percent = bool(present) and all(value > 1 for value in present)
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


def _check(candidates: list[Candidate], options: VSOptions, warnings: list[str]) -> None:
    if len(candidates) != options.k:
        warnings.append(f"{len(candidates)} Fassungen statt {options.k}.")

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
