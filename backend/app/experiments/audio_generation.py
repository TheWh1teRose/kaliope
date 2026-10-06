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
from app.schemas.audio import AudioScript, VoiceCast
from app.speech.base import DIALOGUE_MODELS
from app.speech.registry import SETUP_MESSAGE, speech_provider


class SynthesisSetup(BaseModel):
    artifact_hash: str = ""
    voice_cast: VoiceCast = Field(default_factory=VoiceCast)


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
        "Vorbereitetes Skript mit unveränderten Tags laden und mit anderen Stimmen "
        "und Einstellungen sprechen. Keine Skriptgenerierung."
    )
    target: ClassVar[str] = "ElevenLabs-Sprachausgabe"
    version: ClassVar[str] = "1"
    Setup: ClassVar[type[BaseModel]] = SynthesisSetup

    def defaults(self) -> SynthesisSetup:
        return SynthesisSetup()

    def field_specs(self) -> list[FieldSpec]:
        return []

    def extras(self) -> dict[str, Any]:
        return {"models": list(DIALOGUE_MODELS), "stability": [0.0, 0.5, 1.0]}

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
        from app.worker import worker

        if ctx.source is None:
            raise ValueError("Load a prepared source first")
        with ctx.session_scope() as db:
            loaded = prepared_source(db, ctx.store, ctx.source)
            if loaded.source["artifact_hash"] != setup.artifact_hash:
                raise ValueError("Prepared source changed; load it again")
            script = AudioScript.model_validate(ctx.store.get_raw(setup.artifact_hash))
            missing = {
                line.speaker
                for line in script.lines
                if not setup.voice_cast.voice_for(line.speaker)
            }
            if missing:
                raise ValueError("Missing voices: " + ", ".join(sorted(missing)))
            original = db.get(AudioTake, loaded.source["source_take_id"])
            assert original is not None
            take = AudioTake(
                run_id=ctx.source.run_id,
                flow_id=original.flow_id,
                flow_version=original.flow_version,
                status="queued",
                created_by=ctx.created_by,
                approved_by=ctx.created_by,
                approved_at=datetime.now(UTC),
                script_hash=original.script_hash,
                request_json={"scope": "full", "prepared_audio_hash": setup.artifact_hash},
                voice_cast_json=setup.voice_cast.model_dump(mode="json"),
                manifest_json={"bag_hashes": {"audio_script": setup.artifact_hash}},
            )
            db.add(take)
            db.flush()
            take_id = take.id
        worker.submit_take(take_id)
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
