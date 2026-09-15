"""Review endpoints and the evaluation export (§9).

Producing the edit data is a first-class goal of this build, not a side effect,
so the write path is deliberately strict: an edit, a flag or a relabel without a
reason code is rejected at the schema level, and the JSONL export is a direct
projection of the event table with nothing dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, session_scope
from app.errors import problem
from app.models import EditEvent, ReviewSession, Run, RunNode, User
from app.pipeline import catalogue
from app.pipeline.feedback import last_row_for_key
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.api import ReviewCompleteRequest
from app.schemas.pipeline import Script
from app.schemas.review import (
    UNDOABLE_ACTIONS,
    EditEventIn,
    EditEventOut,
    ReviewSummary,
    clean_tag,
    reason_code_catalogue,
)
from app.schemas.zones import zone_catalogue
from app.security import current_user

router = APIRouter(prefix="/api", tags=["review"])


@dataclass
class SegmentState:
    """What the event stream currently says about one segment.

    Derived, never stored. The events are the record — folding them on read is
    what lets ``undo`` take an action back without deleting the evidence that it
    happened, which the evaluation export depends on.
    """

    #: Accept / edit / flag events still in force, oldest first.
    history: list[EditEvent] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    comments: list[EditEvent] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return any(e.action == "accept" for e in self.history)

    @property
    def flagged(self) -> bool:
        return any(e.action == "flag" for e in self.history)

    @property
    def edited(self) -> bool:
        return any(e.action == "edit" and e.text_after for e in self.history)

    @property
    def text(self) -> str | None:
        """Latest surviving edit, or ``None`` to keep the generated text."""
        for event in reversed(self.history):
            if event.action == "edit" and event.text_after:
                return event.text_after
        return None

    @property
    def undoable(self) -> bool:
        return bool(self.history)


def segment_states(db: Session, run_id: str) -> dict[str, SegmentState]:
    """Fold this run's segment events into per-segment state.

    Ordered by ``created_at``; two events on the same segment in the same
    microsecond would be needed to make the order ambiguous, and review actions
    are keystrokes apart.
    """
    rows = db.scalars(
        select(EditEvent)
        .where(EditEvent.run_id == run_id, EditEvent.target_type == "segment")
        .order_by(EditEvent.created_at.asc())
    ).all()

    states: dict[str, SegmentState] = {}
    for event in rows:
        state = states.setdefault(event.target_id, SegmentState())
        if event.action in UNDOABLE_ACTIONS:
            state.history.append(event)
        elif event.action == "undo":
            if state.history:
                state.history.pop()
        elif event.action == "tag":
            tag = clean_tag(event.text_after or "")
            if tag and tag not in state.tags:
                state.tags.append(tag)
        elif event.action == "untag":
            tag = clean_tag(event.text_after or "")
            if tag in state.tags:
                state.tags.remove(tag)
        elif event.action == "comment":
            state.comments.append(event)
    return states


def edited_texts(db: Session, run_id: str) -> dict[str, str]:
    """Segment id → surviving edited text. Undone edits are not in here."""
    return {
        segment_id: state.text
        for segment_id, state in segment_states(db, run_id).items()
        if state.text
    }


@router.get("/review/reason-codes")
def reason_codes(_user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return reason_code_catalogue()


@router.get("/review/zones")
def zones(_user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return zone_catalogue()


@router.post("/runs/{run_id}/review/start", status_code=201)
def start_review(
    run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    run = _require_run(db, run_id)
    if run.status not in {"completed", "in_review", "reviewed", "failed"}:
        raise problem(
            409,
            "Run is not reviewable",
            f"Run status is '{run.status}'. Wait for it to finish.",
        )

    open_session = db.scalars(
        select(ReviewSession)
        .where(
            ReviewSession.run_id == run_id,
            ReviewSession.user_id == user.id,
            ReviewSession.finished_at.is_(None),
        )
        .order_by(ReviewSession.started_at.desc())
    ).first()

    if open_session is None:
        open_session = ReviewSession(run_id=run_id, user_id=user.id)
        db.add(open_session)
    if run.status == "completed":
        run.status = "in_review"
    db.commit()
    return {"review_session_id": open_session.id, "run_id": run_id, "status": run.status}


@router.post("/runs/{run_id}/review/events", response_model=EditEventOut, status_code=201)
def record_event(
    run_id: str,
    payload: EditEventIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> EditEventOut:
    run = _require_run(db, run_id)
    text_before = payload.text_before
    text_after = payload.text_after
    note = payload.note

    if payload.action == "undo":
        # What an undo takes back depends on what came before it, so the server
        # resolves that rather than trusting a client's idea of the stack, and
        # writes both sides into the event so the export stays self-describing.
        state = segment_states(db, run_id).get(payload.target_id)
        if state is None or not state.history:
            raise problem(
                409,
                "Nothing to undo",
                f"Segment '{payload.target_id}' has no review action to take back.",
            )
        undone = state.history[-1]
        remaining = SegmentState(history=state.history[:-1])
        text_before = undone.text_after if undone.action == "edit" else undone.action
        text_after = remaining.text
        note = note or f"undo of {undone.action}"

    if payload.action in {"tag", "untag"}:
        text_after = clean_tag(text_after or "")

    event = EditEvent(
        run_id=run_id,
        document_id=run.document_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        user_id=user.id,
        action=payload.action,
        reason_code=payload.reason_code.value if payload.reason_code else None,
        note=note,
        text_before=text_before,
        text_after=text_after,
    )
    db.add(event)
    db.commit()
    return _event_out(event, user.email)


@router.get("/runs/{run_id}/review/tags", response_model=list[str])
def run_tags(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[str]:
    """Tags already in use on this run, so the editor can offer them again."""
    _require_run(db, run_id)
    seen: list[str] = []
    for state in segment_states(db, run_id).values():
        for tag in state.tags:
            if tag not in seen:
                seen.append(tag)
    return sorted(seen, key=str.casefold)


@router.post("/runs/{run_id}/review/complete", response_model=ReviewSummary)
def complete_review(
    run_id: str,
    payload: ReviewCompleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ReviewSummary:
    run = _require_run(db, run_id)
    store = ArtifactStore(get_settings().artifacts_dir)

    flow = catalogue.flows(db).get(run.flow_id)
    script_row = (
        last_row_for_key(db, run_id=run_id, flow=flow, key="script")
        if flow is not None
        else db.scalars(
            select(RunNode).where(RunNode.run_id == run_id, RunNode.node_name == "script")
        ).first()
    )
    if script_row is None or not script_row.artifact_hash:
        raise problem(409, "No script", "This run produced no script artifact to review.")

    original = Script.model_validate(store.get_raw(script_row.artifact_hash))
    # Undone edits are absent by construction: the fold drops them, so an edit
    # the reviewer took back cannot reappear in the stored script.
    edits = edited_texts(db, run_id)
    edits.update({k: v for k, v in payload.segments.items() if v})

    # The original is never overwritten — the edited script is a new artifact.
    edited = original.model_copy(
        update={
            "segments": [
                segment.model_copy(update={"text": edits.get(segment.id, segment.text)})
                for segment in original.segments
            ]
        }
    )
    stored = store.put("script_reviewed", edited)

    session_row = db.scalars(
        select(ReviewSession)
        .where(ReviewSession.run_id == run_id, ReviewSession.user_id == user.id)
        .order_by(ReviewSession.started_at.desc())
    ).first()
    if session_row is None:
        session_row = ReviewSession(run_id=run_id, user_id=user.id)
        db.add(session_row)
        db.flush()

    events = db.scalars(select(EditEvent).where(EditEvent.run_id == run_id)).all()
    reason_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for event in events:
        if event.reason_code:
            reason_counts[event.reason_code] = reason_counts.get(event.reason_code, 0) + 1
        action_counts[event.action] = action_counts.get(event.action, 0) + 1

    finished = datetime.now(UTC)
    started = session_row.started_at or finished
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    duration = max((finished - started).total_seconds(), 0.0)

    summary = ReviewSummary(
        run_id=run_id,
        reviewer_id=user.id,
        duration_seconds=round(duration, 1),
        event_count=len(events),
        reason_code_counts=reason_counts,
        action_counts=action_counts,
        edited_script_artifact=stored.hash,
    )
    session_row.finished_at = finished
    session_row.summary_json = summary.model_dump(mode="json")
    if payload.note:
        db.add(
            EditEvent(
                run_id=run_id,
                document_id=run.document_id,
                target_type="segment",
                target_id="__review__",
                user_id=user.id,
                action="comment",
                note=payload.note,
            )
        )
    run.status = "reviewed"
    db.commit()
    return summary


@router.get("/runs/{run_id}/review/events", response_model=list[EditEventOut])
def list_events(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[EditEventOut]:
    _require_run(db, run_id)
    rows = db.scalars(
        select(EditEvent).where(EditEvent.run_id == run_id).order_by(EditEvent.created_at)
    ).all()
    emails = _emails(db)
    return [_event_out(row, emails.get(row.user_id)) for row in rows]


@router.get("/exports/edit-events.jsonl")
def export_edit_events(_user: User = Depends(current_user)) -> StreamingResponse:
    """Every edit event across all runs, one JSON object per line (§9.5)."""

    def lines() -> Iterator[str]:
        with session_scope() as session:
            emails = _emails(session)
            for row in session.scalars(select(EditEvent).order_by(EditEvent.created_at)).yield_per(
                500
            ):
                yield (
                    json.dumps(
                        _event_out(row, emails.get(row.user_id)).model_dump(mode="json"),
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="edit-events.jsonl"'},
    )


def _emails(db: Session) -> dict[str, str]:
    return {u.id: u.email for u in db.scalars(select(User)).all()}


def _event_out(event: EditEvent, email: str | None) -> EditEventOut:
    created = event.created_at or datetime.now(UTC)
    return EditEventOut(
        id=event.id,
        run_id=event.run_id,
        document_id=event.document_id,
        target_type=event.target_type,
        target_id=event.target_id,
        user_id=event.user_id,
        user_email=email,
        action=event.action,
        reason_code=event.reason_code,
        note=event.note,
        text_before=event.text_before,
        text_after=event.text_after,
        created_at=created.isoformat(),
    )


def _require_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise problem(404, "No such run", f"Run '{run_id}' does not exist.")
    return run
