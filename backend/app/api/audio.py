"""Audio takes: an audio pipeline run over a finished script, plus voices per format.

A take tags the script, stops at the approval with the price, and speaks the
approved lines only after someone approves. Without ``ELEVENLABS_API_KEY`` the
take can still be tagged and priced; generating is refused with the setup
message, never with a server error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.review import edited_texts
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.events import bus
from app.models import AudioTake, Document, Run, User, VoiceCastRow
from app.pipeline import catalogue
from app.pipeline.catalogue import CatalogueError
from app.pipeline.feedback import last_row_for_key, pause_from_manifest
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import flow_purpose
from app.schemas.audio import Audio, AudioApproval, AudioScript, VoiceCast
from app.schemas.pipeline import FormatSpec, Script
from app.security import current_user
from app.speech.base import DIALOGUE_MODELS, USD_PER_1K_CHARACTERS, SpeechError, Voice
from app.speech.registry import SETUP_MESSAGE, speech_configured, speech_provider
from app.worker import worker

router = APIRouter(prefix="/api", tags=["audio"])

#: The audio pipeline a take uses when the request names none.
DEFAULT_AUDIO_FLOW = "elevenlabs_dialog_v0"
#: Run states that have a script to speak.
SPOKEN_STATES = frozenset({"completed", "in_review", "reviewed"})
ACTIVE_STATES = frozenset({"queued", "running"})


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


# ------------------------------------------------------------------- models


class AudioFlowOut(BaseModel):
    id: str
    version: str
    description: str | None = None


class AudioStatusOut(BaseModel):
    configured: bool
    #: The setup instruction when nothing can be generated yet.
    message: str | None = None
    flows: list[AudioFlowOut]
    models: list[str]
    usd_per_1k_characters: dict[str, float]


class VoiceCastOut(BaseModel):
    format_id: str
    speakers: list[str]
    cast: VoiceCast
    saved: bool


class TakeIn(BaseModel):
    flow_id: str = DEFAULT_AUDIO_FLOW
    #: Only the one-minute sample in this version; the whole episode follows.
    scope: Literal["sample"] = "sample"
    voice_cast: VoiceCast | None = None


class ApproveIn(BaseModel):
    #: Voices chosen at the approval, replacing the take's.
    voice_cast: VoiceCast | None = None


class ApprovalOut(BaseModel):
    lines: int
    characters: int
    requests: int
    estimate_usd: float
    model_id: str
    missing_voices: list[str] = Field(default_factory=list)


class ChunkOut(BaseModel):
    index: int
    url: str
    characters: int
    duration_s: float
    cost_usd: float
    segment_ids: list[str]


class TakeOut(BaseModel):
    id: str
    run_id: str
    flow_id: str
    flow_version: str
    status: str
    scope: str
    created_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    total_cost_usd: float
    voice_cast: VoiceCast
    speakers: list[str]
    approval: ApprovalOut | None = None
    audio_script: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    chunks: list[ChunkOut] = Field(default_factory=list)


class RunAudioOut(BaseModel):
    configured: bool
    message: str | None = None
    takes: list[TakeOut]


# ------------------------------------------------------------------- status


@router.get("/audio/status", response_model=AudioStatusOut)
def audio_status(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> AudioStatusOut:
    configured = speech_configured()
    return AudioStatusOut(
        configured=configured,
        message=None if configured else SETUP_MESSAGE,
        flows=[
            AudioFlowOut(id=flow.id, version=flow.version, description=flow.description)
            for flow in catalogue.flows(db).values()
            if flow_purpose(flow) == "audio"
        ],
        models=list(DIALOGUE_MODELS),
        usd_per_1k_characters=dict(USD_PER_1K_CHARACTERS),
    )


@router.get("/audio/voices", response_model=list[Voice])
def list_voices(_user: User = Depends(current_user)) -> list[Voice]:
    provider = speech_provider()
    if provider is None:
        raise problem(409, "ElevenLabs is not set up", SETUP_MESSAGE)
    try:
        return provider.voices()
    except SpeechError as exc:
        raise problem(502, "Voices unavailable", str(exc)) from exc


# ------------------------------------------------------------ format voices


@router.get("/formats/{format_id}/voices", response_model=VoiceCastOut)
def get_format_voices(
    format_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> VoiceCastOut:
    spec = _format(db, format_id)
    row = db.get(VoiceCastRow, format_id)
    cast = VoiceCast.model_validate(row.cast_json) if row is not None else VoiceCast()
    return VoiceCastOut(
        format_id=format_id, speakers=spec.speaker_names(), cast=cast, saved=row is not None
    )


@router.put("/formats/{format_id}/voices", response_model=VoiceCastOut)
def put_format_voices(
    format_id: str,
    payload: VoiceCast,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> VoiceCastOut:
    spec = _format(db, format_id)
    row = db.get(VoiceCastRow, format_id)
    if row is None:
        row = VoiceCastRow(format_id=format_id)
        db.add(row)
    row.cast_json = payload.model_dump(mode="json")
    row.updated_by = user.id
    row.updated_at = datetime.now(UTC)
    db.commit()
    return VoiceCastOut(
        format_id=format_id, speakers=spec.speaker_names(), cast=payload, saved=True
    )


# -------------------------------------------------------------------- takes


@router.get("/runs/{run_id}/audio", response_model=RunAudioOut)
def run_audio(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> RunAudioOut:
    run = _require_run(db, run_id)
    takes = db.scalars(
        select(AudioTake).where(AudioTake.run_id == run_id).order_by(AudioTake.created_at.desc())
    ).all()
    configured = speech_configured()
    return RunAudioOut(
        configured=configured,
        message=None if configured else SETUP_MESSAGE,
        takes=[_take_out(take, run) for take in takes],
    )


@router.post("/runs/{run_id}/audio", response_model=TakeOut, status_code=201)
def start_take(
    run_id: str,
    payload: TakeIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> TakeOut:
    run = _require_run(db, run_id)
    if run.status not in SPOKEN_STATES:
        raise problem(
            409, "No script yet", f"Run status is '{run.status}'; audio needs a finished script."
        )
    try:
        flow = catalogue.get_flow(db, payload.flow_id)
    except CatalogueError as exc:
        raise problem(404, "No such audio pipeline", str(exc)) from exc
    if flow_purpose(flow) != "audio":
        raise problem(422, "Not an audio pipeline", f"'{flow.id}' does not make audio.")
    active = db.scalars(
        select(AudioTake).where(
            AudioTake.run_id == run_id, AudioTake.status.in_(sorted(ACTIVE_STATES))
        )
    ).first()
    if active is not None:
        raise problem(409, "A take is running", "Wait until the current audio take has stopped.")

    store = _store()
    script = _spoken_script(db, run, store)
    cast = payload.voice_cast or _saved_cast(db, run)
    if cast.language_code is None:
        cast = cast.model_copy(update={"language_code": _language(db, run)})

    take = AudioTake(
        run_id=run_id,
        flow_id=flow.id,
        flow_version=flow.version,
        status="queued",
        request_json={"scope": payload.scope},
        voice_cast_json=cast.model_dump(mode="json"),
        script_hash=store.put("script_reviewed", script).hash,
        created_by=user.id,
    )
    db.add(take)
    db.commit()
    worker.submit_take(take.id)
    return _take_out(take, run)


@router.post("/audio/takes/{take_id}/approve", response_model=TakeOut)
def approve_take(
    take_id: str,
    payload: ApproveIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> TakeOut:
    take = db.get(AudioTake, take_id)
    if take is None:
        raise problem(404, "No such take", f"Audio take '{take_id}' does not exist.")
    if take.status != "paused":
        raise problem(409, "Not waiting", f"The take is '{take.status}', not waiting for approval.")
    if not speech_configured():
        raise problem(409, "ElevenLabs is not set up", SETUP_MESSAGE)
    run = _require_run(db, take.run_id)

    if payload.voice_cast is not None:
        cast = payload.voice_cast
        if cast.language_code is None:
            cast = cast.model_copy(
                update={
                    "language_code": VoiceCast.model_validate(take.voice_cast_json).language_code
                }
            )
        take.voice_cast_json = cast.model_dump(mode="json")
    approval = _approval_out(take)
    if approval is None:
        raise problem(409, "Not waiting", "The take has no approval to give.")
    if approval.missing_voices:
        raise problem(
            422, "Voices missing", "No voice is set for " + ", ".join(approval.missing_voices) + "."
        )

    store = _store()
    stored = store.put(
        "audio_approval",
        AudioApproval(
            approved_by=user.id,
            approved_at=datetime.now(UTC).isoformat(),
            characters=approval.characters,
            estimate_usd=approval.estimate_usd,
        ),
    )
    manifest = dict(take.manifest_json or {})
    pause = dict(pause_from_manifest(manifest) or {})
    pause["submitted_hash"] = stored.hash
    manifest["pause"] = pause
    take.manifest_json = manifest
    take.status = "queued"
    take.approved_by = user.id
    take.approved_at = datetime.now(UTC)
    db.commit()
    bus.clear(take_id)
    worker.submit_take(take_id)
    return _take_out(take, run)


@router.get("/audio/takes/{take_id}/chunks/{index}")
def take_chunk(
    take_id: str, index: int, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> FileResponse:
    take = db.get(AudioTake, take_id)
    if take is None:
        raise problem(404, "No such take", f"Audio take '{take_id}' does not exist.")
    audio = _audio(take, _store())
    if audio is None or not 0 <= index < len(audio.chunks):
        raise problem(404, "No such chunk", f"The take has no chunk {index}.")
    chunk = audio.chunks[index]
    path = _store().blob_path(chunk.blob, chunk.suffix)
    if not path.exists():
        raise problem(410, "Audio missing", "The chunk's file is no longer on disk.")
    return FileResponse(path, media_type="audio/mpeg", filename=f"kalliope-{take_id}-{index}.mp3")


# ------------------------------------------------------------------ helpers


def _require_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise problem(404, "No such run", f"Run '{run_id}' does not exist.")
    return run


def _format(db: Session, format_id: str) -> FormatSpec:
    try:
        return catalogue.get_format_spec(db, format_id)
    except CatalogueError as exc:
        raise problem(404, "No such format", str(exc)) from exc


def _saved_cast(db: Session, run: Run) -> VoiceCast:
    format_id = str((run.format_spec_json or {}).get("id") or "")
    row = db.get(VoiceCastRow, format_id) if format_id else None
    return VoiceCast.model_validate(row.cast_json) if row is not None else VoiceCast()


def _language(db: Session, run: Run) -> str | None:
    document = db.get(Document, run.document_id)
    language = (run.config_json or {}).get("language") or (document.language if document else None)
    return str(language)[:2] if language else None


def _spoken_script(db: Session, run: Run, store: ArtifactStore) -> Script:
    """The run's script with the review edits that survive, as review shows it."""
    flow = catalogue.flows(db).get(run.flow_id)
    row = last_row_for_key(db, run_id=run.id, flow=flow, key="script") if flow else None
    if row is None or not row.artifact_hash or not store.exists(row.artifact_hash):
        raise problem(409, "No script", "This run has no script artifact to speak.")
    script = Script.model_validate(store.get_raw(row.artifact_hash))
    edits = edited_texts(db, run.id)
    return script.model_copy(
        update={
            "segments": [
                segment.model_copy(update={"text": edits.get(segment.id, segment.text)})
                for segment in script.segments
            ]
        }
    )


def _bag(take: AudioTake) -> dict[str, str]:
    manifest = take.manifest_json or {}
    hashes = {k: v for k, v in (manifest.get("bag_hashes") or {}).items() if isinstance(v, str)}
    pause = pause_from_manifest(manifest) or {}
    for key, value in (pause.get("bag_hashes") or {}).items():
        if isinstance(value, str):
            hashes.setdefault(key, value)
    return hashes


def _audio(take: AudioTake, store: ArtifactStore) -> Audio | None:
    digest = _bag(take).get("audio")
    if not digest or not store.exists(digest):
        return None
    return Audio.model_validate(store.get_raw(digest))


def _approval_out(take: AudioTake) -> ApprovalOut | None:
    pause = pause_from_manifest(take.manifest_json)
    payload = (pause or {}).get("payload") or {}
    if payload.get("kind") != "audio":
        return None
    cast = VoiceCast.model_validate(take.voice_cast_json)
    speakers = sorted((payload.get("voices") or {}).keys())
    return ApprovalOut(
        lines=int(payload.get("lines") or 0),
        characters=int(payload.get("characters") or 0),
        requests=int(payload.get("requests") or 0),
        estimate_usd=float(payload.get("estimate_usd") or 0.0),
        model_id=cast.model_id,
        missing_voices=[speaker for speaker in speakers if not cast.voice_for(speaker)],
    )


def _take_out(take: AudioTake, run: Run) -> TakeOut:
    store = _store()
    bag = _bag(take)
    script_digest = bag.get("audio_script")
    audio_script = (
        AudioScript.model_validate(store.get_raw(script_digest)).model_dump(mode="json")
        if script_digest and store.exists(script_digest)
        else None
    )
    audio = _audio(take, store)
    speakers = FormatSpec.model_validate(run.format_spec_json).speaker_names()
    return TakeOut(
        id=take.id,
        run_id=take.run_id,
        flow_id=take.flow_id,
        flow_version=take.flow_version,
        status=take.status,
        scope=str((take.request_json or {}).get("scope") or "sample"),
        created_at=take.created_at.isoformat() if take.created_at else None,
        finished_at=take.finished_at.isoformat() if take.finished_at else None,
        error=take.error,
        total_cost_usd=take.total_cost_usd or 0.0,
        voice_cast=VoiceCast.model_validate(take.voice_cast_json),
        speakers=speakers,
        approval=_approval_out(take) if take.status == "paused" else None,
        audio_script=audio_script,
        audio=audio.model_dump(mode="json") if audio else None,
        chunks=[
            ChunkOut(
                index=chunk.index,
                url=f"/api/audio/takes/{take.id}/chunks/{chunk.index}",
                characters=chunk.characters,
                duration_s=chunk.duration_s,
                cost_usd=chunk.cost_usd,
                segment_ids=chunk.segment_ids,
            )
            for chunk in (audio.chunks if audio else [])
        ],
    )
