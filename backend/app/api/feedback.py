"""Human-feedback pause: read the subject, save a draft, submit and resume."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.models import BenchRun, Run, User
from app.pipeline.feedback import (
    FeedbackOut,
    FeedbackSaveIn,
    compile_submission,
    feedback_out,
    mark_submitted,
    persist_human_output,
    posted_human_notes,
    require_paused_bench,
    require_paused_run,
    save_draft,
)
from app.pipeline.framework.artifacts import ArtifactStore
from app.security import current_user
from app.worker import worker

router = APIRouter(tags=["feedback"])


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


@router.get("/api/runs/{run_id}/feedback", response_model=FeedbackOut)
def get_run_feedback(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> FeedbackOut:
    run = db.get(Run, run_id)
    if run is None:
        raise problem(404, "No such run", f"Run '{run_id}' does not exist.")
    try:
        return feedback_out(
            run_id=run_id,
            kind="run",
            status=run.status,
            store=_store(),
            manifest=run.manifest_json,
            document_id=run.document_id,
            session=db,
        )
    except KeyError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc


@router.put("/api/runs/{run_id}/feedback", response_model=FeedbackOut)
def save_run_feedback(
    run_id: str,
    payload: FeedbackSaveIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FeedbackOut:
    try:
        run = require_paused_run(db, run_id)
    except KeyError as exc:
        raise problem(404, "No such run", str(exc)) from exc
    except ValueError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc
    try:
        notes = posted_human_notes(_store(), run.manifest_json, payload.notes)
    except (KeyError, ValueError) as exc:
        raise problem(422, "Invalid notes", str(exc)) from exc
    run.manifest_json = save_draft(run.manifest_json, notes)
    db.commit()
    return feedback_out(
        run_id=run_id,
        kind="run",
        status=run.status,
        store=_store(),
        manifest=run.manifest_json,
        document_id=run.document_id,
        session=db,
    )


@router.post("/api/runs/{run_id}/feedback/submit", response_model=FeedbackOut)
def submit_run_feedback(
    run_id: str,
    payload: FeedbackSaveIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FeedbackOut:
    try:
        run = require_paused_run(db, run_id)
    except KeyError as exc:
        raise problem(404, "No such run", str(exc)) from exc
    except ValueError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc
    store = _store()
    try:
        notes = compile_submission(store, run.manifest_json, payload.notes)
    except (KeyError, ValueError) as exc:
        raise problem(422, "Invalid notes", str(exc)) from exc
    stored = store.put("notes", notes)
    pause = (run.manifest_json or {}).get("pause") or {}
    node_name = str(pause.get("node") or "human_feedback")
    persist_human_output(db, kind="run", run_id=run_id, node_name=node_name, digest=stored.hash)
    run.manifest_json = mark_submitted(run.manifest_json, stored.hash, notes)
    run.status = "queued"
    run.error = None
    db.commit()
    worker.submit_run(run_id)
    return feedback_out(
        run_id=run_id,
        kind="run",
        status="queued",
        store=store,
        manifest=run.manifest_json,
        document_id=run.document_id,
        session=db,
    )


@router.get("/api/bench/runs/{bench_id}/feedback", response_model=FeedbackOut)
def get_bench_feedback(
    bench_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> FeedbackOut:
    row = db.get(BenchRun, bench_id)
    if row is None:
        raise problem(404, "No such bench run", f"Bench run '{bench_id}' does not exist.")
    try:
        return feedback_out(
            run_id=bench_id,
            kind="bench",
            status=row.status,
            store=_store(),
            manifest=row.manifest_json,
            extra_hashes=dict(row.bag_hashes_json or {}),
            document_id=row.document_id,
            session=db,
        )
    except KeyError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc


@router.put("/api/bench/runs/{bench_id}/feedback", response_model=FeedbackOut)
def save_bench_feedback(
    bench_id: str,
    payload: FeedbackSaveIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FeedbackOut:
    try:
        row = require_paused_bench(db, bench_id)
    except KeyError as exc:
        raise problem(404, "No such bench run", str(exc)) from exc
    except ValueError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc
    try:
        notes = posted_human_notes(
            _store(),
            row.manifest_json,
            payload.notes,
            extra_hashes=dict(row.bag_hashes_json or {}),
        )
    except (KeyError, ValueError) as exc:
        raise problem(422, "Invalid notes", str(exc)) from exc
    row.manifest_json = save_draft(row.manifest_json, notes)
    db.commit()
    return feedback_out(
        run_id=bench_id,
        kind="bench",
        status=row.status,
        store=_store(),
        manifest=row.manifest_json,
        extra_hashes=dict(row.bag_hashes_json or {}),
        document_id=row.document_id,
        session=db,
    )


@router.post("/api/bench/runs/{bench_id}/feedback/submit", response_model=FeedbackOut)
def submit_bench_feedback(
    bench_id: str,
    payload: FeedbackSaveIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FeedbackOut:
    try:
        row = require_paused_bench(db, bench_id)
    except KeyError as exc:
        raise problem(404, "No such bench run", str(exc)) from exc
    except ValueError as exc:
        raise problem(409, "Not waiting", str(exc)) from exc
    store = _store()
    try:
        notes = compile_submission(
            store,
            row.manifest_json,
            payload.notes,
            extra_hashes=dict(row.bag_hashes_json or {}),
        )
    except (KeyError, ValueError) as exc:
        raise problem(422, "Invalid notes", str(exc)) from exc
    stored = store.put("notes", notes)
    pause = (row.manifest_json or {}).get("pause") or {}
    node_name = str(pause.get("node") or "human_feedback")
    persist_human_output(
        db, kind="bench", run_id=bench_id, node_name=node_name, digest=stored.hash
    )
    row.manifest_json = mark_submitted(row.manifest_json, stored.hash, notes)
    row.status = "queued"
    row.error = None
    db.commit()
    worker.submit_bench(bench_id)
    return feedback_out(
        run_id=bench_id,
        kind="bench",
        status="queued",
        store=store,
        manifest=row.manifest_json,
        extra_hashes=dict(row.bag_hashes_json or {}),
        document_id=row.document_id,
        session=db,
    )
