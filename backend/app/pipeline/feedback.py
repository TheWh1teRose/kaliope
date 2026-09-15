"""Pause state and the notes a person submits to resume a run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion import anchors as anchor_tools
from app.models import BenchNode, BenchRun, Document, Run, RunNode
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, get_node
from app.pipeline.notes import human_notes, merge_notes, validate_note_targets
from app.schemas.api import AnchorOut, PauseOut
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import Note, Notes, NoteSubject, Outline, Script


class FeedbackNoteIn(BaseModel):
    id: str | None = None
    text: str
    target: dict[str, str]
    source: str | None = None
    criterion: str | None = None


class FeedbackSaveIn(BaseModel):
    notes: list[FeedbackNoteIn] = Field(default_factory=list)


class FeedbackCitation(BaseModel):
    """A script segment with resolved page rectangles, for the source pane."""

    id: str
    speaker: str
    text: str
    kind: str
    beat_id: str | None = None
    anchors: list[AnchorOut] = Field(default_factory=list)


class FeedbackOut(BaseModel):
    run_id: str
    kind: Literal["run", "bench"]
    node: str
    status: str
    subject: NoteSubject
    instructions: str
    document_id: str | None = None
    outline: dict[str, Any] | None = None
    script: dict[str, Any] | None = None
    citations: list[FeedbackCitation] = Field(default_factory=list)
    existing_notes: list[dict[str, Any]] = Field(default_factory=list)
    draft_notes: list[dict[str, Any]] = Field(default_factory=list)


def pause_from_manifest(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    pause = manifest.get("pause")
    return pause if isinstance(pause, dict) else None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def pause_out(manifest: dict[str, Any] | None) -> PauseOut | None:
    pause = pause_from_manifest(manifest)
    if not pause or not pause.get("node"):
        return None
    payload = _as_dict(pause.get("payload"))
    subject = payload.get("subject") or "script"
    if subject not in {"outline", "script"}:
        subject = "script"
    return PauseOut(
        node=str(pause["node"]),
        subject=subject,  # type: ignore[arg-type]
        instructions=str(payload.get("instructions") or ""),
    )


def write_pause(
    manifest: dict[str, Any] | None,
    *,
    node: str,
    payload: dict[str, Any],
    bag_hashes: dict[str, str],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = dict(manifest or {})
    old = pause_from_manifest(previous) or pause_from_manifest(data) or {}
    data["pause"] = {
        "node": node,
        "payload": payload,
        "bag_hashes": bag_hashes,
        "draft_notes": list(old.get("draft_notes") or []),
        "submitted_hash": None,
    }
    return data


def save_draft(manifest: dict[str, Any] | None, notes: list[Note]) -> dict[str, Any]:
    data = dict(manifest or {})
    pause = dict(pause_from_manifest(data) or {})
    pause["draft_notes"] = [note.model_dump(mode="json") for note in notes]
    data["pause"] = pause
    return data


def mark_submitted(manifest: dict[str, Any] | None, digest: str, notes: Notes) -> dict[str, Any]:
    data = dict(manifest or {})
    pause = dict(pause_from_manifest(data) or {})
    pause["submitted_hash"] = digest
    pause["draft_notes"] = [note.model_dump(mode="json") for note in notes.items]
    data["pause"] = pause
    return data


def resume_outputs(manifest: dict[str, Any] | None) -> dict[str, str]:
    pause = pause_from_manifest(manifest)
    if not pause:
        return {}
    node = pause.get("node")
    digest = pause.get("submitted_hash")
    if isinstance(node, str) and isinstance(digest, str) and digest:
        return {node: digest}
    return {}


def load_subject_artifacts(
    store: ArtifactStore, hashes: dict[str, Any]
) -> tuple[Outline | None, Script | None, Notes | None]:
    outline = _load(store, hashes, "outline", Outline)
    script = _load(store, hashes, "script", Script)
    notes = _load(store, hashes, "notes", Notes)
    return outline, script, notes


def feedback_out(
    *,
    run_id: str,
    kind: Literal["run", "bench"],
    status: str,
    store: ArtifactStore,
    manifest: dict[str, Any] | None,
    extra_hashes: dict[str, Any] | None = None,
    document_id: str | None = None,
    session: Session | None = None,
) -> FeedbackOut:
    pause = pause_from_manifest(manifest)
    if not pause or not pause.get("node"):
        raise KeyError("this run is not waiting for notes")
    payload = _as_dict(pause.get("payload"))
    hashes = dict(extra_hashes or {})
    stored = _as_dict(pause.get("bag_hashes"))
    hashes.update({key: value for key, value in stored.items() if isinstance(value, str)})
    outline, script, existing = load_subject_artifacts(store, hashes)
    parsed = _load_parsed(store, hashes, document_id, session)
    if parsed is not None and not document_id:
        document_id = parsed.document_id
    subject = payload.get("subject") or (existing.subject if existing else "script")
    if subject not in {"outline", "script"}:
        subject = "script"
    draft_raw = pause.get("draft_notes") or []
    draft = [Note.model_validate(item).model_dump(mode="json") for item in draft_raw if item]
    return FeedbackOut(
        run_id=run_id,
        kind=kind,
        node=str(pause["node"]),
        status=status,
        subject=subject,
        instructions=str(payload.get("instructions") or ""),
        document_id=document_id,
        outline=outline.model_dump(mode="json") if outline else None,
        script=script.model_dump(mode="json") if script else None,
        citations=_citation_segments(script, parsed),
        existing_notes=[
            note.model_dump(mode="json") for note in (existing.items if existing else [])
        ],
        draft_notes=draft,
    )


def _pause_hashes(
    manifest: dict[str, Any] | None, extra_hashes: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    pause = pause_from_manifest(manifest)
    if not pause:
        raise KeyError("this run is not waiting for notes")
    payload = _as_dict(pause.get("payload"))
    hashes = dict(extra_hashes or {})
    stored = _as_dict(pause.get("bag_hashes"))
    hashes.update({key: value for key, value in stored.items() if isinstance(value, str)})
    return pause, payload, hashes


def posted_human_notes(
    store: ArtifactStore,
    manifest: dict[str, Any] | None,
    posted: list[FeedbackNoteIn],
    extra_hashes: dict[str, Any] | None = None,
) -> list[Note]:
    _pause, payload, hashes = _pause_hashes(manifest, extra_hashes)
    outline, script, existing = load_subject_artifacts(store, hashes)
    subject: NoteSubject = payload.get("subject") or (existing.subject if existing else "script")
    if subject not in {"outline", "script"}:
        subject = "script"
    incoming = human_notes([item.model_dump() for item in posted], subject=subject)
    validate_note_targets(incoming, subject=subject, outline=outline, script=script)
    return incoming


def compile_submission(
    store: ArtifactStore,
    manifest: dict[str, Any] | None,
    posted: list[FeedbackNoteIn],
    extra_hashes: dict[str, Any] | None = None,
) -> Notes:
    _pause, payload, hashes = _pause_hashes(manifest, extra_hashes)
    outline, script, existing = load_subject_artifacts(store, hashes)
    subject: NoteSubject = payload.get("subject") or (existing.subject if existing else "script")
    if subject not in {"outline", "script"}:
        subject = "script"
    incoming = posted_human_notes(store, manifest, posted, extra_hashes)
    return merge_notes(existing, incoming, subject=subject)


def last_row_for_key(
    session: Session, *, run_id: str, flow: Flow, key: str
) -> RunNode | None:
    """The last node in the flow that published ``key`` and left an artifact."""
    names = [entry.node for entry in flow.nodes if get_node(entry.node).produces == key]
    rows = {
        row.node_name: row
        for row in session.scalars(select(RunNode).where(RunNode.run_id == run_id)).all()
    }
    for name in reversed(names):
        row = rows.get(name)
        if row is not None and row.artifact_hash:
            return row
    return None


def persist_human_output(
    session: Session,
    *,
    kind: Literal["run", "bench"],
    run_id: str,
    node_name: str,
    digest: str,
) -> None:
    if kind == "run":
        run_row = session.scalars(
            select(RunNode).where(RunNode.run_id == run_id, RunNode.node_name == node_name)
        ).first()
        if run_row is None:
            run_row = RunNode(run_id=run_id, node_name=node_name, node_version="1.0", cache_key="")
            session.add(run_row)
        run_row.artifact_hash = digest
        run_row.cache_hit = False
        run_row.finished_at = datetime.now(UTC)
        return
    bench_row = session.scalars(
        select(BenchNode).where(BenchNode.bench_run_id == run_id, BenchNode.node_name == node_name)
    ).first()
    if bench_row is None:
        bench_row = BenchNode(
            bench_run_id=run_id, node_name=node_name, node_version="1.0", cache_key=""
        )
        session.add(bench_row)
    bench_row.artifact_hash = digest
    bench_row.cache_hit = False
    bench_row.finished_at = datetime.now(UTC)


def require_paused_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise KeyError(f"run '{run_id}' does not exist")
    if run.status != "paused":
        raise ValueError(f"run status is '{run.status}', not paused")
    return run


def require_paused_bench(session: Session, bench_id: str) -> BenchRun:
    row = session.get(BenchRun, bench_id)
    if row is None:
        raise KeyError(f"bench run '{bench_id}' does not exist")
    if row.status != "paused":
        raise ValueError(f"bench run status is '{row.status}', not paused")
    return row


def _load_parsed(
    store: ArtifactStore,
    hashes: dict[str, Any],
    document_id: str | None,
    session: Session | None,
) -> ParsedDocument | None:
    parsed = _load(store, hashes, "parsed", ParsedDocument)
    if parsed is not None:
        return parsed if isinstance(parsed, ParsedDocument) else None
    if not document_id or session is None:
        return None
    document = session.get(Document, document_id)
    digest = getattr(document, "parsed_artifact_hash", None)
    if not isinstance(digest, str) or not store.exists(digest):
        return None
    return ParsedDocument.model_validate(store.get_raw(digest))


def _citation_segments(
    script: Script | None, parsed: ParsedDocument | None
) -> list[FeedbackCitation]:
    if script is None:
        return []
    items: list[FeedbackCitation] = []
    for segment in script.segments:
        resolved: list[AnchorOut] = []
        if parsed is not None:
            for anchor in segment.anchors:
                outcome = anchor_tools.resolve(parsed, anchor)
                resolved.append(
                    AnchorOut(
                        block_id=anchor.block_id,
                        char_start=anchor.char_start,
                        char_end=anchor.char_end,
                        text=outcome.text,
                        resolved=outcome.resolved,
                        rects=outcome.rects,
                    )
                )
        items.append(
            FeedbackCitation(
                id=segment.id,
                speaker=segment.speaker,
                text=segment.text,
                kind=segment.kind,
                beat_id=segment.beat_id,
                anchors=resolved,
            )
        )
    return items


def _load(store: ArtifactStore, hashes: dict[str, Any], key: str, model: type[Any]) -> Any | None:
    digest = hashes.get(key)
    if not isinstance(digest, str) or not store.exists(digest):
        return None
    return model.model_validate(store.get_raw(digest))
