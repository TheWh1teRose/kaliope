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
    #: Per-candidate notes, e.g. "über der Schwelle", "doppelt".
    flags: list[str] = Field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(segment.text.split()) for segment in self.segments)


class ParsedAnswer(BaseModel):
    candidates: list[Candidate]
    reasoning: str | None = None
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
    )


def parse_payload(
    payload: Any,
    options: VSOptions,
    *,
    speakers: list[str] | None = None,
    truncated: bool = False,
    expected: int | None = None,
) -> ParsedAnswer:
    """Normalise an already-decoded answer (one call, or several merged)."""
    warnings: list[str] = []
    reasoning: str | None = None

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
        raw_reasoning = payload.get("reasoning")
        if isinstance(raw_reasoning, str) and raw_reasoning.strip():
            reasoning = raw_reasoning.strip()
    else:
        items = None
    if not isinstance(items, list):
        raise VSParseError('Die Antwort enthält keine Liste "responses".')
    if truncated and items:
        # The JSON repair closes the cut-off tail, so the last version parses but
        # stops mid-sentence. It is the one being written when the answer was cut.
        items = items[:-1]

    candidates: list[Candidate] = []
    raw_probabilities: list[float | None] = []
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
        raw_probabilities.append(
            _probability(item.get("probability") if isinstance(item, dict) else None)
        )
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
        candidates, _normalise_probabilities(raw_probabilities, warnings), strict=True
    ):
        candidate.probability = value

    _check(candidates, options, warnings, expected or options.expected_candidates())
    if truncated:
        warnings.append(
            "Die Antwort wurde bei Max. Ausgabetokens abgeschnitten; die unvollständige letzte "
            "Fassung ist verworfen. Max. Ausgabetokens erhöhen oder k senken."
        )
    return ParsedAnswer(candidates=candidates, reasoning=reasoning, warnings=warnings)


def merge_turns(answers: list[ParsedAnswer]) -> ParsedAnswer:
    """VS-Multi: one list across turns, renumbered, warnings kept per turn."""
    candidates: list[Candidate] = []
    warnings: list[str] = []
    reasonings: list[str] = []
    for turn, answer in enumerate(answers, start=1):
        for candidate in answer.candidates:
            candidates.append(candidate.model_copy(update={"index": len(candidates)}))
        warnings.extend(f"Runde {turn}: {warning}" for warning in answer.warnings)
        if answer.reasoning:
            reasonings.append(answer.reasoning)
    return ParsedAnswer(
        candidates=candidates,
        reasoning="\n\n".join(reasonings) or None,
        warnings=warnings,
    )


# ------------------------------------------------------------------ helpers


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


def _probability(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        # "35%" stays on the percent scale; _normalise_probabilities converts the batch.
        try:
            return float(raw.strip().replace(",", ".").rstrip("%").strip())
        except ValueError:
            return None
    return None


def _normalise_probabilities(values: list[float | None], warnings: list[str]) -> list[float | None]:
    present = [v for v in values if v is not None]
    if present and all(0 <= v <= 100 for v in present) and any(v > 1 for v in present):
        warnings.append("Wahrscheinlichkeiten kamen als Prozent und wurden in 0–1 umgerechnet.")
        values = [v / 100 if v is not None else None for v in values]
    out: list[float | None] = []
    clamped = False
    for value in values:
        if value is None:
            out.append(None)
            continue
        bounded = min(1.0, max(0.0, value))
        clamped = clamped or bounded != value
        out.append(round(bounded, 4))
    if clamped:
        warnings.append("Wahrscheinlichkeiten außerhalb von 0–1 wurden begrenzt.")
    return out


def _check(
    candidates: list[Candidate], options: VSOptions, warnings: list[str], expected: int
) -> None:
    if len(candidates) != expected:
        warnings.append(f"{len(candidates)} Fassungen statt {expected}.")

    probabilities = [c.probability for c in candidates if c.probability is not None]
    if options.verbalizes_probability:
        missing = len(candidates) - len(probabilities)
        if missing:
            warnings.append(f"{missing} Fassung(en) ohne Wahrscheinlichkeit.")
        total = sum(probabilities)
        if total > PROBABILITY_SUM_LIMIT:
            shown = f"{total:.2f}".replace(".", ",")
            warnings.append(f"Summe der Wahrscheinlichkeiten {shown} liegt über 1.")
        if len(probabilities) > 1 and len(set(probabilities)) == 1:
            warnings.append("Alle Fassungen haben dieselbe Wahrscheinlichkeit.")
        if options.threshold_mode == "below":
            for candidate in candidates:
                if candidate.probability is not None and candidate.probability >= options.threshold:
                    candidate.flags.append("über der Schwelle")
            above = sum("über der Schwelle" in c.flags for c in candidates)
            if above:
                warnings.append(
                    f"{above} Fassung(en) liegen nicht unter der Schwelle {options.threshold:g}."
                )

    seen: dict[str, int] = {}
    for candidate in candidates:
        key = _WS.sub(" ", " ".join(s.text for s in candidate.segments)).strip().casefold()
        if key in seen:
            candidate.flags.append("doppelt")
            warnings.append(f"Fassung #{candidate.index + 1} ist gleich wie #{seen[key] + 1}.")
        else:
            seen[key] = candidate.index
