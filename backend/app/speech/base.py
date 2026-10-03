"""Speech synthesis behind one interface.

A :class:`SpeechProvider` turns a dialogue (lines with a voice each) into audio.
ElevenLabs is the one provider today (``speech/elevenlabs.py``); tests register
a stub, so no test ever calls a real API. Every call goes through
:class:`SpeechClient`, which retries what is worth retrying and adds up the
characters and the cost, the way ``LLMClient`` does for model calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field

#: USD per 1,000 characters on the API, read from elevenlabs.io/pricing/api on
#: 2026-10-03. Eleven v4 was on promotion at $0.022 until 2026-10-12; the regular
#: price is used so an estimate never undershoots.
USD_PER_1K_CHARACTERS: dict[str, float] = {
    "eleven_v4": 0.08,
    "eleven_v3": 0.08,
}
DEFAULT_RATE = 0.08

#: The models offered for dialogue, best first.
DIALOGUE_MODELS: tuple[str, ...] = ("eleven_v4", "eleven_v3")


def cost_usd(model_id: str, characters: int) -> float:
    return round(characters / 1000 * USD_PER_1K_CHARACTERS.get(model_id, DEFAULT_RATE), 6)


class DialogueInput(BaseModel):
    text: str
    voice_id: str


class DialogueRequest(BaseModel):
    inputs: list[DialogueInput]
    model_id: str = "eleven_v4"
    language_code: str | None = None
    stability: float | None = Field(default=0.5, ge=0, le=1)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    #: Earlier generations this one continues (at most three, newest last).
    previous_request_ids: list[str] = Field(default_factory=list)
    output_format: str = "mp3_44100_128"

    def characters(self) -> int:
        return sum(len(item.text) for item in self.inputs)


class VoiceSegment(BaseModel):
    """When one input of the dialogue is heard in the audio."""

    input_index: int
    start_s: float
    end_s: float


class SpeechResult(BaseModel):
    audio: bytes
    request_id: str | None = None
    #: Characters the provider billed, when it says so.
    character_cost: int | None = None
    segments: list[VoiceSegment] = Field(default_factory=list)
    latency_ms: int = 0


class Voice(BaseModel):
    voice_id: str
    name: str
    category: str | None = None
    description: str | None = None
    language: str | None = None
    preview_url: str | None = None


class SpeechError(RuntimeError):
    """A provider refused or failed a request; ``retryable`` says whether waiting helps."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


class SpeechProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def dialogue(self, request: DialogueRequest) -> SpeechResult: ...

    def voices(self) -> list[Voice]: ...


class SpeechClient:
    """Run-scoped facade over one provider: retries, character and cost totals."""

    def __init__(
        self,
        provider: SpeechProvider,
        *,
        retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.provider = provider
        self.retries = retries
        self._sleep = sleep
        self._should_stop = should_stop or (lambda: False)
        self.node_name: str | None = None
        self.total_cost_usd = 0.0
        self.total_characters = 0
        self.calls = 0
        self.traces: list[dict[str, Any]] = []

    def dialogue(self, request: DialogueRequest) -> SpeechResult:
        from app.pipeline.framework.cancel import RunStopped

        if self._should_stop():
            raise RunStopped()
        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                result = self.provider.dialogue(request)
            except SpeechError as exc:
                if not exc.retryable or attempt > self.retries:
                    raise
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if not result.latency_ms:
                result.latency_ms = int((time.perf_counter() - started) * 1000)
            break

        characters = result.character_cost or request.characters()
        cost = cost_usd(request.model_id, characters)
        self.total_cost_usd += cost
        self.total_characters += characters
        self.calls += 1
        self.traces.append(
            {
                "node_name": self.node_name,
                "provider": self.provider.name,
                "model": request.model_id,
                "inputs": len(request.inputs),
                "characters": characters,
                "request_id": result.request_id,
                "attempts": attempt,
                "latency_ms": result.latency_ms,
                "cost_usd": cost,
            }
        )
        return result
