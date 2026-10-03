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
