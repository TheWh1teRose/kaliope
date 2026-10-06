"""Synthesis experiments import retained prepared artifacts, never raw scripts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experiments.base import (
    BeatOut,
    ExperimentContext,
    ExperimentResult,
    FieldSpec,
    ItemRef,
    SourceIn,
    SourceOut,
)
from app.experiments.registry import register_experiment
from app.llm.base import LLMClient
from app.models import AudioTake, Run
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.audio import Audio, AudioChunk, AudioScript, LineTiming, VoiceCast
from app.speech.base import DIALOGUE_MODELS, DialogueInput, DialogueRequest, SpeechClient
from app.speech.registry import SETUP_MESSAGE, speech_provider

# Provider's documented reliable request ceiling, not the production chunk size.
# https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert-with-timestamps
# Read 2026-10-06: <=2,000 total inputs[].text characters and <=10 unique voices.
REQUEST_LIMITS = dict.fromkeys(DIALOGUE_MODELS, 2000)
LIMIT_SOURCE = "https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert-with-timestamps"


class SynthesisSetup(BaseModel):
    artifact_hash: str = ""
    voice_cast: VoiceCast = Field(default_factory=VoiceCast)
    # Only the tagged text is editable. Identity, order and speakers stay in the source.
    edited_text: list[str] | None = None
    line_count: int | None = Field(default=None, ge=1)


def selected_script(script: AudioScript, setup: SynthesisSetup) -> AudioScript:
    lines = list(script.lines)
    if setup.edited_text is not None:
        if len(setup.edited_text) != len(lines):
            raise ValueError("Edited script must retain every source utterance and speaker")
        lines = [
            line.model_copy(update={"tagged": text})
            for line, text in zip(lines, setup.edited_text, strict=True)
        ]
    limit = REQUEST_LIMITS.get(setup.voice_cast.model_id)
    if limit is None:
        raise ValueError("No documented single-request limit for this model")
    count = setup.line_count
    if count is None and setup.edited_text is not None:
        raise ValueError(
            "Select the number of edited utterances explicitly; edits are never truncated"
        )
    if count is None:
        count, size = 0, 0
        for line in lines:
            if size + len(line.tagged) > limit:
                break
            count += 1
            size += len(line.tagged)
    if not count or count > len(lines):
        raise ValueError(
            "Select at least one whole utterance; shorten the first if it exceeds the limit"
        )
    chosen = lines[:count]
    if any(not line.tagged.strip() for line in chosen):
        raise ValueError("Selected utterances must not be empty")
    characters = sum(len(line.tagged) for line in chosen)
    if characters > limit:
        raise ValueError(
            f"Selected text has {characters} characters; single-request limit is {limit}. "
            "Shorten it or select fewer utterances. Edits are never truncated."
        )
    voices = {setup.voice_cast.voice_for(line.speaker) for line in chosen}
    if None in voices:
        raise ValueError("Assign a voice to every selected speaker")
    if len(voices) > 10:
        raise ValueError("ElevenLabs allows at most 10 unique voices per request")
    return script.model_copy(update={"lines": chosen})


def prepared_source(db: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    run = db.get(Run, source.run_id)
    if run is None:
        raise LookupError("Run does not exist")
    takes = db.scalars(
        select(AudioTake).where(AudioTake.run_id == run.id).order_by(AudioTake.created_at.desc())
    ).all()
    # The existing chooser's second-stage selection identifies a retained take.
    from app.api.audio import _bag

    available: list[tuple[AudioTake, str]] = []
    for take in takes:
        if (take.request_json or {}).get("experiment_single_request"):
            continue
        digest = _bag(take).get("audio_script")
        if digest and store.exists(digest):
            available.append((take, digest))
    if not available:
        raise LookupError(
            "No prepared ElevenLabs script retained. Prepare an audio take first; "
            "raw scripts are not regenerated here."
        )
    take, digest = next(((t, h) for t, h in available if t.id == source.beat_id), available[0])
    if source.beat_id and source.beat_id != take.id:
        raise LookupError("Prepared take does not exist")
    script = AudioScript.model_validate(store.get_raw(digest))
    return SourceOut(
        fields={
            "artifact_hash": digest,
            "audio_script": json.dumps(script.model_dump(mode="json"), ensure_ascii=False),
        },
        beats=[
            BeatOut(
                id=t.id,
                title=f"{t.created_at} · {t.status}",
                word_budget=0,
                passage_count=0,
            )
            for t, _ in available
        ],
        source={
            "run_id": run.id,
            "beat_id": take.id,
            "artifact_hash": digest,
            "source_take_id": take.id,
            "flow_version": take.flow_version,
            "prepared": True,
        },
    )


class AudioGenerationExperiment:
    key: ClassVar[str] = "audio_generation"
    title: ClassVar[str] = "ElevenLabs ausprobieren"
    summary: ClassVar[str] = (
        "Vorbereitetes Skript laden, bearbeiten und mit anderen Stimmen "
        "und Einstellungen sprechen. Keine Skriptgenerierung."
    )
    target: ClassVar[str] = "ElevenLabs-Sprachausgabe"
    version: ClassVar[str] = "2"
    Setup: ClassVar[type[BaseModel]] = SynthesisSetup

    def defaults(self) -> SynthesisSetup:
        return SynthesisSetup()

    def field_specs(self) -> list[FieldSpec]:
        return []

    def extras(self) -> dict[str, Any]:
        return {
            "models": list(DIALOGUE_MODELS),
            "stability": [0.0, 0.5, 1.0],
            "request_limits": REQUEST_LIMITS,
            "limit_source": LIMIT_SOURCE,
        }

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return prepared_source(session, store, source)

    def validate_setup(self, setup: BaseModel) -> list[str]:
        assert isinstance(setup, SynthesisSetup)
        provider = speech_provider()
        if provider is None:
            return [SETUP_MESSAGE]
        cast = setup.voice_cast
        if cast.model_id not in DIALOGUE_MODELS:
            return ["Unsupported dialogue model"]
        if cast.stability not in (0.0, 0.5, 1.0):
            return ["Dialogue stability must be 0, 0.5 or 1"]
        if cast.language_code is not None:
            return ["Language override is not offered by this experiment"]
        known = {voice.voice_id for voice in provider.voices()}
        if not cast.voices or any(v.voice_id not in known for v in cast.voices):
            return ["Select available ElevenLabs voices"]
        if len({v.speaker.strip().lower() for v in cast.voices}) != len(cast.voices):
            return ["Duplicate speaker assignments"]
        return []

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult:
        assert isinstance(setup, SynthesisSetup)
        if ctx.source is None:
            raise ValueError("Load a prepared source first")
        with ctx.session_scope() as db:
            loaded = prepared_source(db, ctx.store, ctx.source)
            if loaded.source["artifact_hash"] != setup.artifact_hash:
                raise ValueError("Prepared source changed; load it again")
            script = selected_script(
                AudioScript.model_validate(ctx.store.get_raw(setup.artifact_hash)), setup
            )
            missing = {
                line.speaker
                for line in script.lines
                if not setup.voice_cast.voice_for(line.speaker)
            }
            if missing:
                raise ValueError("Missing voices: " + ", ".join(sorted(missing)))
            original = db.get(AudioTake, loaded.source["source_take_id"])
            assert original is not None
            provider = speech_provider()
            if provider is None:
                raise ValueError(SETUP_MESSAGE)
            # No production planner, cache, retries or joiner: exactly one provider call.
            client = SpeechClient(provider, retries=0)
            result = client.dialogue(
                DialogueRequest(
                    inputs=[
                        DialogueInput(
                            text=line.tagged,
                            voice_id=setup.voice_cast.voice_for(line.speaker) or "",
                        )
                        for line in script.lines
                    ],
                    model_id=setup.voice_cast.model_id,
                    stability=setup.voice_cast.stability,
                    seed=setup.voice_cast.seed,
                )
            )
            if not result.audio:
                raise ValueError("ElevenLabs returned no audio")
            timings = [
                LineTiming(
                    segment_id=script.lines[s.input_index].segment_id,
                    start_s=s.start_s,
                    end_s=s.end_s,
                    input_index=s.input_index,
                )
                for s in result.segments
                if 0 <= s.input_index < len(script.lines)
            ]
            chunk = AudioChunk(
                index=0,
                segment_ids=[line.segment_id for line in script.lines],
                characters=client.total_characters,
                blob=ctx.store.put_blob(result.audio, ".mp3"),
                request_id=result.request_id,
                character_cost=result.character_cost,
                cost_usd=client.total_cost_usd,
                duration_s=max((t.end_s for t in timings), default=0),
                lines=timings,
            )
            script_hash = ctx.store.put("audio_script", script).hash
            audio_hash = ctx.store.put(
                "audio",
                Audio(
                    chunks=[chunk],
                    model_id=setup.voice_cast.model_id,
                    scope="full",
                    characters=client.total_characters,
                    cost_usd=client.total_cost_usd,
                    duration_s=chunk.duration_s,
                ),
            ).hash
            take = AudioTake(
                run_id=ctx.source.run_id,
                flow_id=original.flow_id,
                flow_version=original.flow_version,
                status="completed",
                finished_at=datetime.now(UTC),
                total_cost_usd=client.total_cost_usd,
                created_by=ctx.created_by,
                approved_by=ctx.created_by,
                approved_at=datetime.now(UTC),
                script_hash=original.script_hash,
                request_json={
                    "scope": "full",
                    "experiment_single_request": True,
                    "source_artifact_hash": setup.artifact_hash,
                },
                voice_cast_json=setup.voice_cast.model_dump(mode="json"),
                manifest_json={"bag_hashes": {"audio_script": script_hash, "audio": audio_hash}},
            )
            db.add(take)
            db.flush()
            take_id = take.id
        return ExperimentResult(
            output_json={
                "take_id": take_id,
                "run_id": ctx.source.run_id,
                "artifact_hash": setup.artifact_hash,
                "voice_cast": setup.voice_cast.model_dump(mode="json"),
            }
        )

    def items(self, output_json: dict[str, Any]) -> list[ItemRef]:
        return [ItemRef(title="ElevenLabs Audio")]


register_experiment(AudioGenerationExperiment())
