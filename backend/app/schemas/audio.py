"""The script prepared for speech synthesis: one line per script segment.

The tagging node keeps the words of the script and adds only two things: audio
tags in square brackets (``[curious]``, ``[short pause]``) that direct the
delivery, and declared spoken forms (``1,5 %`` → ``eins Komma fünf Prozent``)
for what a speaker says differently from how it is written. Every line carries
the result of the guard that checked exactly that.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GuardStatus = Literal["pass", "fallback"]


class SpokenForm(BaseModel):
    """A stretch of the written line and how it is said."""

    original: str
    spoken: str


class AudioLine(BaseModel):
    segment_id: str
    beat_id: str | None = None
    speaker: str
    kind: str | None = None
    #: The script's text, unchanged.
    text: str
    #: ``text`` with the declared spoken forms applied: what is said.
    spoken: str
    #: ``spoken`` with the audio tags: what goes to the speech model.
    tagged: str
    spoken_forms: list[SpokenForm] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    #: ``fallback`` when the tagged line failed the guard and the plain text is used.
    guard: GuardStatus = "pass"
    #: Why the guard refused the model's line, for a ``fallback``.
    problem: str | None = None


class AudioScript(BaseModel):
    lines: list[AudioLine]
    #: The model that placed the tags.
    model: str | None = None
    #: Language the tags are written in (``en`` or ``de``).
    tag_language: str = "en"

    def fallbacks(self) -> list[AudioLine]:
        return [line for line in self.lines if line.guard == "fallback"]

    def character_count(self) -> int:
        """What a speech model would bill: the tagged text of every line."""
        return sum(len(line.tagged) for line in self.lines)


# ------------------------------------------------------------------ speech


class VoiceChoice(BaseModel):
    #: The speaker's name as it appears in the script.
    speaker: str
    voice_id: str
    voice_name: str | None = None


class VoiceCast(BaseModel):
    """Who speaks with which voice, and how. Its own seed, never part of the format,
    so changing a voice never invalidates a cached script."""

    voices: list[VoiceChoice] = Field(default_factory=list)
    model_id: str = "eleven_v4"
    stability: float = Field(default=0.5, ge=0, le=1)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    language_code: str | None = None

    def voice_for(self, speaker: str) -> str | None:
        wanted = speaker.strip().lower()
        for choice in self.voices:
            if choice.speaker.strip().lower() == wanted and choice.voice_id.strip():
                return choice.voice_id.strip()
        return None


AudioScope = Literal["sample", "full"]


class AudioRequest(BaseModel):
    """What one audio take covers."""

    scope: AudioScope = "sample"
    #: For a sample: whole lines from the start until about this many characters
    #: (about one minute of speech).
    sample_characters: int = Field(default=1000, ge=100, le=5000)


class AudioApproval(BaseModel):
    """The person's go-ahead to spend credits, with the price they saw."""

    approved_by: str | None = None
    approved_at: str
    characters: int
    estimate_usd: float


class LineTiming(BaseModel):
    segment_id: str
    start_s: float
    end_s: float


class AudioChunk(BaseModel):
    """One generated stretch of dialogue: whole lines of one beat."""

    index: int
    segment_ids: list[str]
    characters: int
    #: Content hash of the audio file in the artifact store's media folder.
    blob: str
    suffix: str = ".mp3"
    request_id: str | None = None
    character_cost: int | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    #: Where each line is heard inside this chunk.
    lines: list[LineTiming] = Field(default_factory=list)


class Audio(BaseModel):
    chunks: list[AudioChunk]
    model_id: str
    scope: AudioScope
    characters: int
    #: The price of every chunk, including chunks reused from an earlier take;
    #: what a take actually paid is on the take.
    cost_usd: float
    duration_s: float
