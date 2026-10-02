"""The Experimentieren section: hand-coded experiments, their runs and collected outputs.

A run is queued here and executed on the worker pool like a bench run, so a
paid answer is stored even when the browser goes away. The page polls the run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.experiments  # noqa: F401 - registers every experiment
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.experiments.base import Experiment, SourceIn
from app.experiments.registry import experiments, get_experiment
from app.llm import registry as llm_registry
from app.models import ExperimentOutput, ExperimentRun, User
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.experiments import (
    ExperimentDetailOut,
    ExperimentRunIn,
    ExperimentRunOut,
    ExperimentSourceOut,
    ExperimentStats,
    ExperimentSummaryOut,
    ItemOut,
    LLMCallOut,
    OutputOut,
    SaveIn,
)
from app.security import current_user
from app.worker import worker

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.get("", response_model=list[ExperimentSummaryOut])
def list_experiments(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[ExperimentSummaryOut]:
    return [_summary(db, experiment) for experiment in experiments()]


@router.get("/runs/{run_id}", response_model=ExperimentRunOut)
def get_run(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> ExperimentRunOut:
    return _run_out(db, _require_run(db, run_id))


@router.post("/runs/{run_id}/save", response_model=OutputOut, status_code=201)
def save_item(
    run_id: str,
    payload: SaveIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> OutputOut:
    """Collect one item of a finished run. Saving the same item again returns it."""
    row = _require_run(db, run_id)
    if row.status != "completed" or row.output_json is None:
        raise problem(409, "Run not finished", "Only a completed run can be collected.")
    experiment = _experiment(row.experiment_key)
    items = {ref.item: ref for ref in experiment.items(row.output_json)}
    ref = items.get(payload.item)
    if ref is None:
        raise problem(422, "Unknown item", f"'{payload.item}' is not an item of this run.")

    existing = db.scalars(
        select(ExperimentOutput).where(
            ExperimentOutput.run_id == row.id, ExperimentOutput.item == payload.item
        )
    ).first()
    if existing is not None:
        if payload.label is not None and payload.label != existing.label:
            existing.label = payload.label or None
            db.commit()
        return _output_out(existing)

    output = ExperimentOutput(
        experiment_key=row.experiment_key,
        run_id=row.id,
        item=payload.item,
        label=(payload.label or "").strip() or None,
        output_json=row.output_json,
        text=(row.manifest_json or {}).get("text"),
        meta_json={**_run_facts(row), **ref.meta},
        setup_json=row.setup_json,
        created_by=user.id,
    )
    db.add(output)
    try:
        db.commit()
    except IntegrityError:  # a concurrent save of the same item won
        db.rollback()
        existing = db.scalars(
            select(ExperimentOutput).where(
                ExperimentOutput.run_id == row.id, ExperimentOutput.item == payload.item
            )
        ).one()
        return _output_out(existing)
    return _output_out(output)


@router.delete("/outputs/{output_id}", status_code=204)
def delete_output(
    output_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> Response:
    row = db.get(ExperimentOutput, output_id)
    if row is None:
        raise problem(404, "No such output", f"Output '{output_id}' does not exist.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/{key}", response_model=ExperimentDetailOut)
def get_experiment_detail(
    key: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> ExperimentDetailOut:
    experiment = _experiment(key)
    summary = _summary(db, experiment)
    return ExperimentDetailOut(
        **summary.model_dump(),
        defaults=experiment.defaults().model_dump(mode="json"),
        fields=experiment.field_specs(),
        extras=experiment.extras(),
    )


@router.post("/{key}/source", response_model=ExperimentSourceOut)
def load_source(
    key: str,
    payload: SourceIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> ExperimentSourceOut:
    experiment = _experiment(key)
    try:
        loaded = experiment.load_source(db, _store(), payload)
    except LookupError as exc:
        raise problem(422, "Cannot load from this run", str(exc)) from exc
    return ExperimentSourceOut(**loaded.model_dump())


@router.post("/{key}/runs", response_model=ExperimentRunOut, status_code=201)
def create_run(
    key: str,
    payload: ExperimentRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ExperimentRunOut:
    experiment = _experiment(key)
    try:
        setup = experiment.Setup.model_validate(payload.setup)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise problem(
            422,
            "Invalid setup",
            f"{location}: {first.get('msg')}",
            errors=exc.errors(include_context=False, include_url=False),
        ) from exc
    _check_model(getattr(setup, "settings", None))
    problems = experiment.validate_setup(setup)
    if problems:
        raise problem(422, "This setup cannot run", problems[0], errors=problems)

    source: dict[str, Any] | None = None
    if payload.source is not None:
        source = {**(payload.source_meta or {}), **payload.source.model_dump()}
    row = ExperimentRun(
        experiment_key=experiment.key,
        experiment_version=experiment.version,
        status="queued",
        setup_json=setup.model_dump(mode="json"),
        source_json=source,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    worker.submit_experiment(row.id)
    return _run_out(db, row)


@router.get("/{key}/runs", response_model=list[ExperimentRunOut])
def list_runs(
    key: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[ExperimentRunOut]:
    experiment = _experiment(key)
    rows = db.scalars(
        select(ExperimentRun)
        .where(ExperimentRun.experiment_key == experiment.key)
        .order_by(ExperimentRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [_run_out(db, row) for row in rows]


@router.get("/{key}/outputs", response_model=list[OutputOut])
def list_outputs(
    key: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[OutputOut]:
    experiment = _experiment(key)
    rows = db.scalars(
        select(ExperimentOutput)
        .where(ExperimentOutput.experiment_key == experiment.key)
        .order_by(ExperimentOutput.created_at.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [_output_out(row) for row in rows]


# ------------------------------------------------------------------ helpers


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


def _experiment(key: str) -> Experiment:
    try:
        return get_experiment(key)
    except KeyError as exc:
        raise problem(404, "No such experiment", str(exc)) from exc


def _require_run(db: Session, run_id: str) -> ExperimentRun:
    row = db.get(ExperimentRun, run_id)
    if row is None:
        raise problem(404, "No such run", f"Experiment run '{run_id}' does not exist.")
    return row


def _check_model(settings: Any) -> None:
    """Experiments use registered models only, so the price is always known."""
    model = getattr(settings, "model", None)
    if model is None:
        return
    spec = llm_registry.spec_for(model)
    if spec is None:
        raise problem(422, "Unknown model", f"'{model}' is not in the model registry.")
    try:
        llm_registry.resolve_provider(model)
    except KeyError as exc:
        raise problem(
            422,
            "Provider not configured",
            f"No API key is set for provider '{spec.provider}', so '{model}' cannot run.",
        ) from exc


def _summary(db: Session, experiment: Experiment) -> ExperimentSummaryOut:
    runs = db.execute(
        select(
            func.count(ExperimentRun.id),
            func.coalesce(func.sum(ExperimentRun.total_cost_usd), 0.0),
            func.max(ExperimentRun.created_at),
        ).where(ExperimentRun.experiment_key == experiment.key)
    ).one()
    saved = db.scalar(
        select(func.count(ExperimentOutput.id)).where(
            ExperimentOutput.experiment_key == experiment.key
        )
    )
    return ExperimentSummaryOut(
        key=experiment.key,
        title=experiment.title,
        summary=experiment.summary,
        target=experiment.target,
        version=experiment.version,
        stats=ExperimentStats(
            run_count=int(runs[0] or 0),
            saved_count=int(saved or 0),
            spent_usd=round(float(runs[1] or 0.0), 6),
            last_run_at=_iso(runs[2]),
        ),
    )


def _calls(row: ExperimentRun) -> list[LLMCallOut]:
    out: list[LLMCallOut] = []
    for item in (row.manifest_json or {}).get("llm_traces") or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(LLMCallOut.model_validate(item))
        except ValidationError:  # a broken frame must not hide the run
            continue
    return out


def _run_facts(row: ExperimentRun) -> dict[str, Any]:
    """What a collected output shows about the run that produced it."""
    calls = _calls(row)
    first = calls[0] if calls else None
    return {
        "model": first.model if first else (row.setup_json or {}).get("settings", {}).get("model"),
        "temperature": first.temperature if first else None,
        "thinking": first.thinking if first else None,
        "effort": first.effort if first else None,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "cost_usd": row.total_cost_usd,
        "latency_ms": sum(call.latency_ms for call in calls),
        "warnings": list(row.warnings_json or []),
        "source": row.source_json,
        "experiment_version": row.experiment_version,
    }


def _run_out(db: Session, row: ExperimentRun) -> ExperimentRunOut:
    items: list[ItemOut] = []
    if row.status == "completed" and row.output_json is not None:
        saved = {
            output.item: output.id
            for output in db.scalars(
                select(ExperimentOutput).where(ExperimentOutput.run_id == row.id)
            ).all()
        }
        try:
            refs = _experiment(row.experiment_key).items(row.output_json)
        except Exception:  # noqa: BLE001 - an unknown or changed experiment hides no data
            refs = []
        items = [
            ItemOut(item=ref.item, title=ref.title, meta=ref.meta, output_id=saved.get(ref.item))
            for ref in refs
        ]
    wall_ms = None
    if row.started_at and row.finished_at:
        wall_ms = int((row.finished_at - row.started_at).total_seconds() * 1000)
    return ExperimentRunOut(
        id=row.id,
        experiment_key=row.experiment_key,
        experiment_version=row.experiment_version,
        status=row.status,
        setup=row.setup_json or {},
        source=row.source_json,
        output=row.output_json,
        warnings=list(row.warnings_json or []),
        error=row.error,
        total_cost_usd=row.total_cost_usd,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        wall_ms=wall_ms,
        created_at=_iso(row.created_at) or "",
        finished_at=_iso(row.finished_at),
        items=items,
        calls=_calls(row),
    )


def _output_out(row: ExperimentOutput) -> OutputOut:
    return OutputOut(
        id=row.id,
        experiment_key=row.experiment_key,
        run_id=row.run_id,
        item=row.item,
        label=row.label,
        output=row.output_json,
        text=row.text,
        meta=row.meta_json or {},
        setup=row.setup_json or {},
        created_at=_iso(row.created_at) or "",
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
