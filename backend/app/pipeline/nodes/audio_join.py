"""The ``audio_join`` node: one MP3 for the take, and a clock for every line.

ffmpeg (bundled through ``imageio-ffmpeg``) joins the chunks in order with a
short silence between them and normalises the loudness. Each chunk's real
length is measured, so the per-line times ElevenLabs returned for a chunk move
onto the joined file's clock exactly. That is what lets the player jump to a
line and highlight the line being heard. Joining costs nothing, and the result
is cached under the chunk files it was made from.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import BaseModel

from app.pipeline.framework.artifacts import hash_payload
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.audio import Audio, AudioMix, LineTiming
from app.speech import mix


class AudioJoinInput(BaseModel):
    audio: Audio


class AudioJoinNode:
    name = "audio_join"
    title = "Audio zusammenfügen"
    version = "1.0"
    Input: type[BaseModel] = AudioJoinInput
    Output: type[BaseModel] = AudioMix
    produces = "audio_mix"

    doc = NodeDoc(
        summary="Joins the spoken chunks into one loudness-normalised MP3 with a clock "
        "for every line.",
        detail=[
            "Joins the chunks in order with ffmpeg, a short silence between them and no "
            "crossfade (chunks end at speaker turns), and normalises to -16 LUFS, the usual "
            "podcast loudness.",
            "Measures each chunk's real length and moves the per-line times from ElevenLabs "
            "onto the joined file's clock, so the player can seek to a line and highlight it.",
            "Cached under the chunk files and settings: joining the same chunks again reuses "
            "the file.",
        ],
        inputs={"audio": "The spoken chunks with their files and per-line times."},
        output="AudioMix: the joined file, its length, the chunk offsets and every line's "
        "start and end.",
        failure_modes=["ffmpeg is not available or cannot read a chunk file."],
        cost="None; runs locally in seconds.",
    )

    params = [
        NodeParam(
            key="gap_s",
            label="Pause between chunks (s)",
            type="float",
            default=mix.GAP_S,
            minimum=0.0,
            maximum=2.0,
        ),
        NodeParam(
            key="bitrate",
            label="MP3 bitrate",
            type="select",
            default="128k",
            options=["128k", "192k"],
            advanced=True,
        ),
    ]

    def run(self, inp: AudioJoinInput, ctx: NodeContext) -> AudioMix:
        if not inp.audio.chunks:
            raise NodeError("there are no chunks to join")
        gap = float(ctx.get("gap_s", mix.GAP_S))
        bitrate = str(ctx.get("bitrate") or "128k")
        paths = [ctx.artifacts.blob_path(c.blob, c.suffix) for c in inp.audio.chunks]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            raise NodeError(f"chunk files are missing: {', '.join(missing)}")

        key = hash_payload(
            {
                "node": self.name,
                "version": self.version,
                "chunks": [c.blob for c in inp.audio.chunks],
                "gap": gap,
                "bitrate": bitrate,
                "loudness": mix.LOUDNESS_LUFS,
            }
        )
        cached = ctx.artifacts.get_step(key)
        if cached is not None and not ctx.force:
            found = AudioMix.model_validate(ctx.artifacts.get_raw(cached))
            if ctx.artifacts.has_blob(found.blob, found.suffix):
                return found

        ctx.progress(f"joining {len(paths)} chunk(s)")
        try:
            lengths = [mix.duration_s(path) for path in paths]
            with tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / "joined.mp3"
                mix.join(paths, out, gap_s=gap, bitrate=bitrate)
                blob = ctx.artifacts.put_blob(out.read_bytes(), ".mp3")
                total = mix.duration_s(out)
        except mix.MixError as exc:
            raise NodeError(str(exc)) from exc

        offsets: list[float] = []
        clock = 0.0
        for length in lengths:
            offsets.append(round(clock, 3))
            clock += length + gap
        # A line split across requests ("s12#1", "s12#2") is one script segment.
        spans: dict[str, LineTiming] = {}
        for chunk, offset in zip(inp.audio.chunks, offsets, strict=True):
            for timing in chunk.lines:
                segment = timing.segment_id.split("#", 1)[0]
                start, end = round(offset + timing.start_s, 3), round(offset + timing.end_s, 3)
                known = spans.get(segment)
                spans[segment] = LineTiming(
                    segment_id=segment,
                    start_s=min(start, known.start_s) if known else start,
                    end_s=max(end, known.end_s) if known else end,
                )
        lines = sorted(spans.values(), key=lambda timing: timing.start_s)
        result = AudioMix(
            blob=blob,
            duration_s=round(total, 3),
            gap_s=gap,
            loudness_lufs=mix.LOUDNESS_LUFS,
            chunk_offsets_s=offsets,
            lines=lines,
        )
        ctx.artifacts.put_step(key, ctx.artifacts.put("audio_mix", result).hash)
        return result


register_node(AudioJoinNode())
