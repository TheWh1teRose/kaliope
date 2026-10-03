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
from dataclasses import dataclass

from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.schemas.audio import AudioChunk, AudioLine, AudioRequest, AudioScript, VoiceCast
from app.speech.base import DialogueInput, cost_usd

MAX_CHUNK_CHARACTERS = 1800
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
#: Part of every chunk key. Kept as the render node's name and first version, so
#: chunks spoken before the key moved here are still found.
_KEY_OWNER = ("audio_render", "1.0")

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


@dataclass(frozen=True)
class PlannedChunk:
    """One request as it will be sent, and the key its audio is cached under."""

    index: int
    lines: list[AudioLine]
    inputs: list[DialogueInput]
    key: str

    @property
    def characters(self) -> int:
        return sum(len(item.text) for item in self.inputs)


def plan_requests(
    lines: Sequence[AudioLine],
    cast: VoiceCast,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
) -> list[PlannedChunk]:
    """The requests a take sends for these lines, each with its cache key."""
    planned = []
    for index, chunk_lines in enumerate(plan_chunks(lines)):
        inputs = [
            DialogueInput(text=line.tagged, voice_id=cast.voice_for(line.speaker) or "")
            for line in chunk_lines
        ]
        planned.append(
            PlannedChunk(
                index=index,
                lines=chunk_lines,
                inputs=inputs,
                key=chunk_key(cast, output_format, inputs),
            )
        )
    return planned


def chunk_key(cast: VoiceCast, output_format: str, inputs: Sequence[DialogueInput]) -> str:
    """Everything that shapes a chunk's sound; neighbouring chunks are not part of it."""
    return hash_payload(
        {
            "node": _KEY_OWNER[0],
            "version": _KEY_OWNER[1],
            "model": cast.model_id,
            "stability": cast.stability,
            "seed": cast.seed,
            "language": cast.language_code,
            "format": output_format,
            "inputs": [item.model_dump() for item in inputs],
        }
    )


def cached_chunk(store: ArtifactStore, key: str) -> AudioChunk | None:
    """A chunk already spoken under this key whose audio file is still there."""
    digest = store.get_step(key)
    if digest is None:
        return None
    chunk = AudioChunk.model_validate(store.get_raw(digest))
    return chunk if store.has_blob(chunk.blob, chunk.suffix) else None


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
