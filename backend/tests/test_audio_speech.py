"""Speech: the ElevenLabs provider over mock HTTP, the client, chunks and the audio nodes.

No test reaches ElevenLabs. The provider is exercised against an
``httpx.MockTransport`` that checks the request shape from the API reference;
everything else uses :class:`StubSpeech`.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.llm.base import LLMClient
from app.pipeline.audio_chunks import MAX_CHUNK_CHARACTERS, estimate, plan_chunks, select_lines
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext, NodeError, NodePause
from app.pipeline.nodes.audio_approval import AudioApprovalInput, AudioApprovalNode
from app.pipeline.nodes.audio_render import AudioRenderInput, AudioRenderNode
from app.schemas.audio import (
    Audio,
    AudioApproval,
    AudioLine,
    AudioRequest,
    AudioScript,
    VoiceCast,
    VoiceChoice,
)
from app.speech.base import (
    DialogueInput,
    DialogueRequest,
    SpeechClient,
    SpeechError,
    SpeechResult,
)
from app.speech.elevenlabs import ElevenLabsProvider
from tests.support import StubSpeech


def _line(index: int, beat: str, speaker: str, text: str) -> AudioLine:
    return AudioLine(
        segment_id=f"{beat}-s{index:04d}",
        beat_id=beat,
        speaker=speaker,
        text=text,
        spoken=text,
        tagged=text,
    )


def _script(*sizes: tuple[str, int]) -> AudioScript:
    """Lines of the given beats and lengths, alternating speakers."""
    lines = []
    for index, (beat, size) in enumerate(sizes):
        speaker = "Moderator" if index % 2 == 0 else "Expertin"
        lines.append(_line(index, beat, speaker, ("Ein Satz. " * size)[:size]))
    return AudioScript(lines=lines)


CAST = VoiceCast(
    voices=[
        VoiceChoice(speaker="Moderator", voice_id="v-mod"),
        VoiceChoice(speaker="Expertin", voice_id="v-exp"),
    ],
    model_id="eleven_v4",
    seed=7,
    language_code="de",
)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


def _context(store: ArtifactStore, speech: SpeechClient | None) -> NodeContext:
    return NodeContext(
        run_id="take",
        llm=LLMClient(resolve=lambda _name: None, cost_of=lambda _m, _u: 0.0),  # type: ignore[arg-type,return-value]
        artifacts=store,
        config={},
        logger=logging.getLogger("test"),
        speech=speech,
    )


def _approval() -> AudioApproval:
    return AudioApproval(approved_at="2026-10-03T12:00:00Z", characters=0, estimate_usd=0.0)


# ----------------------------------------------------------------- provider


def _provider(handler: Any) -> ElevenLabsProvider:
    return ElevenLabsProvider("key-123", transport=httpx.MockTransport(handler))


def test_the_provider_sends_a_timestamped_dialogue_and_reads_the_answer() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"request-id": "r-9", "character-cost": "31"},
            json={
                "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
                "voice_segments": [
                    {
                        "voice_id": "v-mod",
                        "start_time_seconds": 0.0,
                        "end_time_seconds": 1.2,
                        "character_start_index": 0,
                        "character_end_index": 12,
                        "dialogue_input_index": 0,
                    },
                    {
                        "voice_id": "v-exp",
                        "start_time_seconds": 1.3,
                        "end_time_seconds": 2.9,
                        "character_start_index": 12,
                        "character_end_index": 31,
                        "dialogue_input_index": 1,
                    },
                ],
            },
        )

    result = _provider(handler).dialogue(
        DialogueRequest(
            inputs=[
                DialogueInput(text="[curious] Hallo?", voice_id="v-mod"),
                DialogueInput(text="Guten Tag.", voice_id="v-exp"),
            ],
            language_code="de",
            stability=0.4,
            seed=7,
            previous_request_ids=["a", "b", "c", "d"],
        )
    )

    assert seen["path"] == "/v1/text-to-dialogue/with-timestamps"
    assert seen["params"] == {"output_format": "mp3_44100_128"}
    assert seen["key"] == "key-123"
    assert seen["body"] == {
        "inputs": [
            {"text": "[curious] Hallo?", "voice_id": "v-mod"},
            {"text": "Guten Tag.", "voice_id": "v-exp"},
        ],
        "model_id": "eleven_v4",
        "language_code": "de",
        "settings": {"stability": 0.4},
        "seed": 7,
        "previous_request_ids": ["b", "c", "d"],
    }
    assert result.audio == b"mp3-bytes"
    assert result.request_id == "r-9" and result.character_cost == 31
    assert [(s.input_index, s.end_s) for s in result.segments] == [(0, 1.2), (1, 2.9)]


@pytest.mark.parametrize(
    ("status", "body", "retryable", "words"),
    [
        (401, {"detail": {"code": "invalid_api_key", "message": "bad key"}}, False, "refused"),
        (402, {"detail": {"code": "insufficient_credits", "message": "x"}}, False, "credits"),
        (429, {"detail": {"code": "concurrent_limit_exceeded", "message": "x"}}, True, "limit"),
        (503, {"detail": "busy"}, True, "503"),
        (422, {"detail": [{"msg": "text too long"}]}, False, "text too long"),
    ],
)
def test_provider_errors_are_readable_and_say_whether_to_retry(
    status: int, body: dict[str, Any], retryable: bool, words: str
) -> None:
    provider = _provider(lambda _request: httpx.Response(status, json=body))
    with pytest.raises(SpeechError) as raised:
        provider.dialogue(DialogueRequest(inputs=[DialogueInput(text="x", voice_id="v")]))
    assert raised.value.retryable is retryable
    assert raised.value.status == status
    assert words in str(raised.value)


def test_default_voices_are_left_out_of_the_voice_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/voices"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "p1", "name": "Rachel", "category": "premade"},
                    {
                        "voice_id": "c1",
                        "name": "Mara",
                        "category": "professional",
                        "labels": {"language": "de"},
                        "preview_url": "https://example.test/mara.mp3",
                    },
                ]
            },
        )

    voices = _provider(handler).voices()
    assert [(v.voice_id, v.language, v.preview_url) for v in voices] == [
        ("c1", "de", "https://example.test/mara.mp3")
    ]


# ------------------------------------------------------------------- client


def test_the_client_retries_what_is_retryable_and_adds_up_the_cost() -> None:
    stub = StubSpeech()
    pauses: list[float] = []
    calls = {"n": 0}
    original = stub.dialogue

    def flaky(request: DialogueRequest) -> SpeechResult:
        calls["n"] += 1
        if calls["n"] < 3:
            raise SpeechError("busy", status=429, retryable=True)
        return original(request)

    stub.dialogue = flaky  # type: ignore[method-assign]
    client = SpeechClient(stub, sleep=pauses.append)
    request = DialogueRequest(inputs=[DialogueInput(text="a" * 500, voice_id="v")])
    client.dialogue(request)

    assert pauses == [2.0, 4.0]
    assert client.total_characters == 500
    assert client.total_cost_usd == pytest.approx(0.04)
    assert client.traces[0]["attempts"] == 3


def test_the_client_gives_up_on_a_refusal_at_once() -> None:
    stub = StubSpeech()
    stub.fail_with = SpeechError("no credits", status=402)
    client = SpeechClient(stub, sleep=lambda _s: pytest.fail("must not wait"))
    with pytest.raises(SpeechError):
        client.dialogue(DialogueRequest(inputs=[DialogueInput(text="a", voice_id="v")]))
    assert client.total_cost_usd == 0


# ------------------------------------------------------------------- chunks


def test_a_sample_is_whole_lines_from_the_start_up_to_its_size() -> None:
    script = _script(("b0", 400), ("b0", 400), ("b1", 400), ("b1", 400))
    sample = select_lines(script, AudioRequest(scope="sample", sample_characters=1000))
    assert [line.segment_id for line in sample] == ["b0-s0000", "b0-s0001", "b1-s0002"]
    assert len(select_lines(script, AudioRequest(scope="full"))) == 4


def test_chunks_keep_lines_whole_stay_in_one_beat_and_fit_the_limit() -> None:
    script = _script(("b0", 900), ("b0", 800), ("b0", 300), ("b1", 200), ("b1", 4000))
    chunks = plan_chunks(script.lines)
    assert [[line.segment_id for line in chunk] for chunk in chunks] == [
        ["b0-s0000", "b0-s0001"],
        ["b0-s0002"],
        ["b1-s0003"],
        ["b1-s0004#1"],
        ["b1-s0004#2"],
        ["b1-s0004#3"],
    ]
    assert all(sum(len(line.tagged) for line in chunk) <= MAX_CHUNK_CHARACTERS for chunk in chunks)
    characters, price, requests = estimate(script.lines, "eleven_v4")
    assert characters == 6200 and requests == 6
    assert price == pytest.approx(0.496)


# -------------------------------------------------------------------- nodes


def test_the_approval_pauses_with_the_price_before_anything_is_spent(
    store: ArtifactStore,
) -> None:
    script = _script(("b0", 600), ("b0", 600), ("b1", 600))
    cast = CAST.model_copy(update={"voices": CAST.voices[:1]})
    with pytest.raises(NodePause) as paused:
        AudioApprovalNode().run(
            AudioApprovalInput(audio_script=script, voice_cast=cast), _context(store, None)
        )
    payload = paused.value.payload
    assert payload["kind"] == "audio" and payload["scope"] == "sample"
    assert payload["lines"] == 2 and payload["characters"] == 1200
    assert payload["estimate_usd"] == pytest.approx(0.096)
    assert payload["missing_voices"] == ["Expertin"]


def test_render_speaks_each_chunk_once_and_stores_the_audio(store: ArtifactStore) -> None:
    script = _script(("b0", 900), ("b0", 800), ("b0", 300), ("b1", 200))
    stub = StubSpeech()
    inp = AudioRenderInput(
        audio_script=script,
        audio_approval=_approval(),
        voice_cast=CAST,
        audio_request=AudioRequest(scope="full"),
    )

    audio = AudioRenderNode().run(inp, _context(store, SpeechClient(stub)))
    assert isinstance(audio, Audio)
    assert len(stub.requests) == 3
    assert [item.voice_id for item in stub.requests[0].inputs] == ["v-mod", "v-exp"]
    assert stub.requests[0].previous_request_ids == []
    assert stub.requests[2].previous_request_ids == ["req-1", "req-2"]
    assert stub.requests[0].seed == 7 and stub.requests[0].language_code == "de"
    first = audio.chunks[0]
    assert store.blob_path(first.blob, ".mp3").read_bytes().startswith(b"ID3")
    assert [t.segment_id for t in first.lines] == ["b0-s0000", "b0-s0001"]
    assert first.duration_s == pytest.approx(2.9)
    assert audio.characters == 2200 and audio.cost_usd == pytest.approx(0.176)

    again = AudioRenderNode().run(inp, _context(store, SpeechClient(stub)))
    assert len(stub.requests) == 3, "a second take of the same lines is not paid for again"
    assert [c.blob for c in again.chunks] == [c.blob for c in audio.chunks]

    store.blob_path(first.blob, ".mp3").unlink()
    AudioRenderNode().run(inp, _context(store, SpeechClient(stub)))
    assert len(stub.requests) == 4, "a chunk whose file is gone is spoken again"


def test_v3_is_not_stitched(store: ArtifactStore) -> None:
    stub = StubSpeech()
    inp = AudioRenderInput(
        audio_script=_script(("b0", 100), ("b1", 100)),
        audio_approval=_approval(),
        voice_cast=CAST.model_copy(update={"model_id": "eleven_v3"}),
        audio_request=AudioRequest(scope="full"),
    )
    AudioRenderNode().run(inp, _context(store, SpeechClient(stub)))
    assert [r.previous_request_ids for r in stub.requests] == [[], []]


def test_render_without_a_provider_or_a_voice_fails_with_a_reason(store: ArtifactStore) -> None:
    inp = AudioRenderInput(
        audio_script=_script(("b0", 100), ("b0", 100)),
        audio_approval=_approval(),
        voice_cast=CAST,
    )
    with pytest.raises(NodeError, match="ELEVENLABS_API_KEY"):
        AudioRenderNode().run(inp, _context(store, None))

    lacking = inp.model_copy(update={"voice_cast": VoiceCast()})
    with pytest.raises(NodeError, match="no voice is set for Expertin, Moderator"):
        AudioRenderNode().run(lacking, _context(store, SpeechClient(StubSpeech())))

    refusing = StubSpeech()
    refusing.fail_with = SpeechError("ElevenLabs reports too few credits", status=402)
    with pytest.raises(NodeError, match="too few credits"):
        AudioRenderNode().run(inp, _context(store, SpeechClient(refusing)))
