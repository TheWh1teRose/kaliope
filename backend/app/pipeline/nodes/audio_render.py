"""The ``audio_render`` node: the approved lines spoken, one request per chunk.

Each chunk (whole lines of one beat, at most 1,800 characters with tags) is one
Text to Dialogue request with one input per line and that speaker's voice. The
audio is stored in the artifact store's media folder under its own hash, and
the chunk is cached under a key of everything that shapes the sound: model,
voices, settings and the chunk's tagged text. A take that failed half-way
resumes at the first chunk without a cached result, so no chunk is paid for
twice. Neighbouring chunks are not part of the key; on Eleven v4 a chunk is
conditioned on the ones just before it through their request ids.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.pipeline.audio_chunks import plan_chunks, select_lines
from app.pipeline.framework.artifacts import hash_payload
from app.pipeline.framework.cancel import RunStopped
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.audio import (
    Audio,
    AudioApproval,
    AudioChunk,
    AudioLine,
    AudioRequest,
    AudioScript,
    LineTiming,
    VoiceCast,
)
from app.speech.base import DialogueInput, DialogueRequest, SpeechError, cost_usd

#: Models that continue a generation from earlier request ids (request stitching).
STITCHING_MODELS = frozenset({"eleven_v4"})


class AudioRenderInput(BaseModel):
    audio_script: AudioScript
    audio_approval: AudioApproval
    voice_cast: VoiceCast
    audio_request: AudioRequest = AudioRequest()


class AudioRenderNode:
    name = "audio_render"
    title = "Audio erzeugen"
    version = "1.0"
    Input: type[BaseModel] = AudioRenderInput
    Output: type[BaseModel] = Audio
    produces = "audio"

    doc = NodeDoc(
        summary="Speaks the approved lines with ElevenLabs Text to Dialogue, chunk by chunk.",
        detail=[
            "Cuts the lines into chunks of whole lines within one beat, at most 1,800 "
            "characters with their tags, and sends each chunk as one dialogue request with "
            "one input per line and that speaker's voice.",
            "Stores each chunk's audio under its own hash and caches the chunk under a key "
            "of model, voices, settings and its tagged text. A failed take resumes at the "
            "first chunk that has no cached audio, so nothing is paid for twice.",
            "On Eleven v4 each chunk continues from up to three chunks before it through "
            "their request ids; every chunk uses the same voices, stability, seed and "
            "language.",
            "Rate limits and server errors are retried with back-off by the speech client.",
        ],
        inputs={
            "audio_script": "The tagged lines.",
            "audio_approval": "The go-ahead; without it the node never runs.",
            "voice_cast": "Voices, model, stability, seed and language.",
            "audio_request": "Sample or whole script; must match what was approved.",
        },
        output="Audio: the chunks with their files, request ids, characters, cost and the "
        "time each line is heard.",
        failure_modes=[
            "ElevenLabs is not set up (ELEVENLABS_API_KEY).",
            "A speaker in the lines has no voice.",
            "ElevenLabs refuses the key or reports too few credits. Chunks already "
            "generated stay cached.",
        ],
        cost="The characters of the spoken lines at the model's rate: about $0.08 per "
        "1,000 characters, one minute of speech is about 1,000 characters.",
    )

    params = [
        NodeParam(
            key="output_format",
            label="Output format",
            type="select",
            default="mp3_44100_128",
            options=["mp3_44100_128", "mp3_44100_192"],
            description="192 kbps needs the Creator plan or higher.",
            advanced=True,
        ),
    ]

    def run(self, inp: AudioRenderInput, ctx: NodeContext) -> Audio:
        if ctx.speech is None:
            raise NodeError(
                "ElevenLabs is not set up: ELEVENLABS_API_KEY is missing, so no audio can be "
                "generated."
            )
        cast = inp.voice_cast
        lines = select_lines(inp.audio_script, inp.audio_request)
        missing = sorted({line.speaker for line in lines if not cast.voice_for(line.speaker)})
        if missing:
            raise NodeError(f"no voice is set for {', '.join(missing)}")
        output_format = str(ctx.get("output_format") or "mp3_44100_128")

        chunks = plan_chunks(lines)
        rendered: list[AudioChunk] = []
        request_ids: list[str] = []
        for index, chunk_lines in enumerate(chunks):
            inputs = [
                DialogueInput(text=line.tagged, voice_id=cast.voice_for(line.speaker) or "")
                for line in chunk_lines
            ]
            key = hash_payload(
                {
                    "node": self.name,
                    "version": self.version,
                    "model": cast.model_id,
                    "stability": cast.stability,
                    "seed": cast.seed,
                    "language": cast.language_code,
                    "format": output_format,
                    "inputs": [item.model_dump() for item in inputs],
                }
            )
            chunk = self._cached(ctx, key)
            if chunk is None:
                if ctx.stopped():
                    raise RunStopped()
                ctx.progress(f"speaking chunk {index + 1} of {len(chunks)}")
                chunk = self._speak(
                    ctx, cast, inputs, chunk_lines, index, output_format, request_ids
                )
                ctx.artifacts.put_step(key, ctx.artifacts.put("audio_chunk", chunk).hash)
                if ctx.stopped():
                    # This chunk is already paid for and cached. Do not speak another.
                    rendered.append(chunk)
                    raise RunStopped()
            else:
                ctx.progress(f"chunk {index + 1} of {len(chunks)} reused")
                chunk = chunk.model_copy(update={"index": index})
            rendered.append(chunk)
            if chunk.request_id and chunk.request_id not in request_ids[-1:]:
                request_ids.append(chunk.request_id)

        return Audio(
            chunks=rendered,
            model_id=cast.model_id,
            scope=inp.audio_request.scope,
            characters=sum(chunk.characters for chunk in rendered),
            cost_usd=round(sum(chunk.cost_usd for chunk in rendered), 6),
            duration_s=round(sum(chunk.duration_s for chunk in rendered), 3),
        )

    def _cached(self, ctx: NodeContext, key: str) -> AudioChunk | None:
        digest = ctx.artifacts.get_step(key)
        if digest is None:
            return None
        chunk = AudioChunk.model_validate(ctx.artifacts.get_raw(digest))
        return chunk if ctx.artifacts.has_blob(chunk.blob, chunk.suffix) else None

    def _speak(
        self,
        ctx: NodeContext,
        cast: VoiceCast,
        inputs: list[DialogueInput],
        lines: list[AudioLine],
        index: int,
        output_format: str,
        request_ids: list[str],
    ) -> AudioChunk:
        assert ctx.speech is not None
        request = DialogueRequest(
            inputs=inputs,
            model_id=cast.model_id,
            language_code=cast.language_code,
            stability=cast.stability,
            seed=cast.seed,
            previous_request_ids=request_ids[-3:] if cast.model_id in STITCHING_MODELS else [],
            output_format=output_format,
        )
        try:
            result = ctx.speech.dialogue(request)
        except SpeechError as exc:
            raise NodeError(str(exc)) from exc
        if not result.audio:
            raise NodeError(f"chunk {index + 1} came back without audio")

        characters = result.character_cost or request.characters()
        timings = [
            LineTiming(
                segment_id=lines[segment.input_index].segment_id,
                start_s=segment.start_s,
                end_s=segment.end_s,
            )
            for segment in result.segments
            if 0 <= segment.input_index < len(lines)
        ]
        return AudioChunk(
            index=index,
            segment_ids=[line.segment_id for line in lines],
            characters=characters,
            blob=ctx.artifacts.put_blob(result.audio, ".mp3"),
            request_id=result.request_id,
            character_cost=result.character_cost,
            cost_usd=cost_usd(cast.model_id, characters),
            duration_s=max((timing.end_s for timing in timings), default=0.0),
            lines=timings,
        )


register_node(AudioRenderNode())
