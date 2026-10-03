"""The ``audio_approval`` node: nothing is paid for before a person says so.

It works out what the take will send, which lines, how many requests, how many
characters and what that costs, and pauses with that as its payload. The console
shows it next to the tagged script and the voices; approving stores an
``AudioApproval`` and resumes the take. The node never calls a model or a speech
provider and is never reused from another take.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.pipeline.audio_chunks import estimate, select_lines
from app.pipeline.framework.node import NodeContext, NodePause
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc
from app.schemas.audio import AudioApproval, AudioRequest, AudioScript, VoiceCast


class AudioApprovalInput(BaseModel):
    audio_script: AudioScript
    voice_cast: VoiceCast
    audio_request: AudioRequest = AudioRequest()


class AudioApprovalNode:
    name = "audio_approval"
    title = "Audio freigeben"
    version = "1.0"
    Input: type[BaseModel] = AudioApprovalInput
    Output: type[BaseModel] = AudioApproval
    produces = "audio_approval"
    #: An approval belongs to one take; reusing one would spend without asking.
    cacheable = False

    doc = NodeDoc(
        summary="Pauses the audio take and shows the price before any credits are spent.",
        detail=[
            "Selects the lines the take will speak (the whole script, or for a sample the "
            "first lines up to about one minute), cuts them into requests the way the "
            "render step will, and prices the characters at the model's rate.",
            "The pause payload carries that estimate, the voices per speaker and any "
            "speaker without a voice. Approving in the console stores the approval with the "
            "price that was shown and resumes the take.",
        ],
        inputs={
            "audio_script": "The tagged lines; their characters are what is billed.",
            "voice_cast": "Voices, model and settings for this take.",
            "audio_request": "Sample or whole script.",
        },
        output="An AudioApproval: who approved, when, and the price they saw.",
        failure_modes=["None. It always pauses until someone approves."],
        cost="None.",
    )

    def run(self, inp: AudioApprovalInput, ctx: NodeContext) -> AudioApproval:
        lines = select_lines(inp.audio_script, inp.audio_request)
        characters, price, requests = estimate(lines, inp.voice_cast.model_id)
        speakers = sorted({line.speaker for line in lines})
        raise NodePause(
            f"waiting for approval to spend about ${price:.2f} on audio",
            payload={
                "kind": "audio",
                "scope": inp.audio_request.scope,
                "lines": len(lines),
                "segment_ids": [line.segment_id for line in lines],
                "characters": characters,
                "requests": requests,
                "estimate_usd": price,
                "model_id": inp.voice_cast.model_id,
                "voices": {speaker: inp.voice_cast.voice_for(speaker) for speaker in speakers},
                "missing_voices": [s for s in speakers if not inp.voice_cast.voice_for(s)],
            },
        )


register_node(AudioApprovalNode())
