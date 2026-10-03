"""Which lines a take speaks, how they are cut into requests, and what that costs.

ElevenLabs recommends at most 2,000 characters per dialogue request (API
reference, read 2026-10-03). A chunk is therefore whole lines of one beat, up to
1,800 characters with their tags, which leaves room under that limit and keeps
every voice change at a speaker turn. A single line longer than that is split at
sentence ends. One line is one dialogue input, so the timestamps the provider
returns map back to script segments.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.schemas.audio import AudioLine, AudioRequest, AudioScript
from app.speech.base import cost_usd

MAX_CHUNK_CHARACTERS = 1800

#: Just after a sentence end, before the space that follows it.
_SENTENCE_END = re.compile(r"(?<=[.!?…])(?=\s)")


def select_lines(script: AudioScript, request: AudioRequest) -> list[AudioLine]:
    """All lines for a full take; for a sample, whole lines from the start up to the size."""
    if request.scope == "full":
        return list(script.lines)
    chosen: list[AudioLine] = []
    total = 0
    for line in script.lines:
        if chosen and total >= request.sample_characters:
            break
        chosen.append(line)
        total += len(line.tagged)
    return chosen


def plan_chunks(
    lines: Sequence[AudioLine], max_characters: int = MAX_CHUNK_CHARACTERS
) -> list[list[AudioLine]]:
    """Whole lines, in order, never across a beat, at most ``max_characters`` each."""
    chunks: list[list[AudioLine]] = []
    current: list[AudioLine] = []
    size = 0
    parts = [part for line in lines for part in _split(line, max_characters)]
    for line in parts:
        length = len(line.tagged)
        new_beat = bool(current) and current[-1].beat_id != line.beat_id
        if current and (new_beat or size + length > max_characters):
            chunks.append(current)
            current, size = [], 0
        current.append(line)
        size += length
    if current:
        chunks.append(current)
    return chunks


def estimate(lines: Sequence[AudioLine], model_id: str) -> tuple[int, float, int]:
    """Characters, price in USD and number of requests for these lines."""
    characters = sum(len(line.tagged) for line in lines)
    return characters, cost_usd(model_id, characters), len(plan_chunks(lines))


def _split(line: AudioLine, max_characters: int) -> list[AudioLine]:
    """A line that does not fit one request, cut at sentence ends into parts.

    The cut keeps every character, so the parts add up to the line's length and
    the price shown at the approval is what is sent.
    """
    if len(line.tagged) <= max_characters:
        return [line]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(line.tagged):
        if current and len(current) + len(sentence) > max_characters:
            parts.append(current)
            current = ""
        current += sentence
    if current:
        parts.append(current)
    return [
        line.model_copy(update={"segment_id": f"{line.segment_id}#{index}", "tagged": text})
        for index, text in enumerate(parts, start=1)
    ]
