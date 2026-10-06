"""The Experimentieren section: hand-coded experiments, their runs and collected outputs.

A run is queued here and executed on the worker pool like a bench run, so a
paid answer is stored even when the browser goes away. The page polls the run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.experiments  # noqa: F401 - registers every experiment
from app.accounts import author_labels
from app.api.output_folders import tree as folder_tree
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.experiments.base import Experiment, SourceIn
from app.experiments.registry import experiments, get_experiment
from app.experiments.search import item_search_text
from app.folder_tree import ROOT
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
    OutputMoveIn,
    OutputOut,
    OutputPageOut,
    OutputPatchIn,
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


@router.get("/outputs", response_model=OutputPageOut)
def list_all_outputs(
    folder_id: str | None = None,
    include_sub: bool = True,
    experiment: str | None = None,
    q: str | None = None,
    status: Literal["kandidat", "gewaehlt", "verworfen", "none"] | None = None,
    ids: str | None = None,
    sort: Literal["new", "old"] = "new",
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> OutputPageOut:
    """The Sammlung: collected outputs of every experiment, newest first.

    ``folder_id`` absent means every output; the literal ``root`` means those in
    no folder; a folder id shows that folder, with its subfolders unless
    ``include_sub`` is false. ``q`` searches each output's own text, its label
    and its note. ``status=none`` means outputs without a status. ``ids`` (comma
    separated, as the compare view sends them) limits the list to those outputs.
    """
    statement = select(ExperimentOutput)
    if ids is not None:
        wanted = [part for part in ids.split(",") if part][:200]
        statement = statement.where(ExperimentOutput.id.in_(wanted))
    if status == "none":
        statement = statement.where(ExperimentOutput.status.is_(None))
    elif status:
        statement = statement.where(ExperimentOutput.status == status)
    if folder_id == ROOT:
        statement = statement.where(ExperimentOutput.folder_id.is_(None))
    elif folder_id:
        resolved = folder_tree.resolve(db, folder_id)
        assert resolved is not None
        scope = [resolved, *(folder_tree.descendants(db, resolved) if include_sub else [])]
        statement = statement.where(ExperimentOutput.folder_id.in_(scope))
    if experiment:
        statement = statement.where(ExperimentOutput.experiment_key == experiment)
    needle = (q or "").strip()
    if needle:
        pattern = _like(needle)
        statement = statement.where(
            or_(
                ExperimentOutput.search_text.ilike(pattern, escape="\\"),
                ExperimentOutput.label.ilike(pattern, escape="\\"),
                ExperimentOutput.note.ilike(pattern, escape="\\"),
            )
        )

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    order = (
        (ExperimentOutput.created_at.desc(), ExperimentOutput.id.desc())
        if sort == "new"
        else (ExperimentOutput.created_at, ExperimentOutput.id)
    )
    rows = db.scalars(
        statement.order_by(*order).offset(max(0, offset)).limit(max(1, min(limit, 200)))
    ).all()
    all_count = db.scalar(select(func.count(ExperimentOutput.id))) or 0
    root_count = (
        db.scalar(
            select(func.count(ExperimentOutput.id)).where(ExperimentOutput.folder_id.is_(None))
        )
        or 0
    )
    labels = _labels(db)
    return OutputPageOut(
        items=[_output_out(row, labels) for row in rows],
        total=int(total),
        all_count=int(all_count),
        root_count=int(root_count),
    )


@router.post("/outputs/move")
def move_outputs(
    payload: OutputMoveIn, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> dict[str, object]:
    """File one or many outputs into a folder, or back to "Ohne Ordner"."""
    target = folder_tree.resolve(db, payload.folder_id)
    ids = list(dict.fromkeys(payload.ids))
    rows = db.scalars(select(ExperimentOutput).where(ExperimentOutput.id.in_(ids))).all()
    missing = sorted(set(ids) - {row.id for row in rows})
    if missing:
        raise problem(404, "No such output", f"Output '{missing[0]}' does not exist.")
    for row in rows:
        row.folder_id = target
    db.commit()
    return {"moved": len(rows), "folder_id": target}


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
    folder_id = folder_tree.resolve(db, payload.folder_id)
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
        return _output_out(existing, _labels(db))

    text = (row.manifest_json or {}).get("text")
    output = ExperimentOutput(
        experiment_key=row.experiment_key,
        run_id=row.id,
        item=payload.item,
        label=(payload.label or "").strip() or None,
        output_json=row.output_json,
        text=text,
        meta_json={**_run_facts(row), **ref.meta},
        setup_json=row.setup_json,
        created_by=user.id,
        folder_id=folder_id,
        search_text=item_search_text(row.output_json, payload.item, text),
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
        return _output_out(existing, _labels(db))
    return _output_out(output, _labels(db))


@router.patch("/outputs/{output_id}", response_model=OutputOut)
def update_output(
    output_id: str,
    payload: OutputPatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> OutputOut:
    """Rename an output, or record a decision on it: a status and a short note."""
    row = db.get(ExperimentOutput, output_id)
    if row is None:
        raise problem(404, "No such output", f"Output '{output_id}' does not exist.")
    sent = payload.model_fields_set
    if "label" in sent:
        row.label = (payload.label or "").strip() or None
    if "status" in sent or "note" in sent:
        if "status" in sent:
            row.status = payload.status
        if "note" in sent:
            row.note = (payload.note or "").strip() or None
        row.decided_by = user.id
        row.decided_at = datetime.now(UTC)
    db.commit()
    return _output_out(row, _labels(db))


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
    if key == "audio_generation":
        from app.experiments.audio_generation import (
            SynthesisSetup,
            prepared_source,
            selected_script,
        )
        from app.schemas.audio import AudioScript

        assert isinstance(setup, SynthesisSetup)
        if payload.source is None:
            raise problem(422, "Source required", "Load a prepared audio take first.")
        try:
            loaded = prepared_source(db, _store(), payload.source)
        except LookupError as exc:
            raise problem(422, "Prepared source unavailable", str(exc)) from exc
        if loaded.source["artifact_hash"] != setup.artifact_hash:
            raise problem(422, "Source changed", "Reload the prepared source.")
        script = AudioScript.model_validate(_store().get_raw(setup.artifact_hash))
        try:
            selected_script(script, setup)
        except ValueError as exc:
            raise problem(422, "Invalid single-request selection", str(exc)) from exc
        payload.source_meta = loaded.source
    _check_model(getattr(setup, "settings", None))
    from app.speech.base import SpeechError

    try:
        problems = experiment.validate_setup(setup)
    except SpeechError as exc:
        raise problem(502, "Voices unavailable", str(exc)) from exc
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
    labels = _labels(db)
    return [_output_out(row, labels) for row in rows]


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


class _Labels:
    """Folder paths and author labels, looked up once per response."""

    def __init__(self, paths: dict[str, list[str]], authors: dict[str, str]) -> None:
        self.paths = paths
        self.authors = authors


def _labels(db: Session) -> _Labels:
    return _Labels(folder_tree.paths(db), author_labels(db))


def _output_out(row: ExperimentOutput, labels: _Labels) -> OutputOut:
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
        folder_id=row.folder_id,
        folder_path=labels.paths.get(row.folder_id, []) if row.folder_id else [],
        created_by=labels.authors.get(row.created_by) if row.created_by else None,
        status=row.status,  # type: ignore[arg-type]
        note=row.note,
        decided_by=labels.authors.get(row.decided_by) if row.decided_by else None,
        decided_at=_iso(row.decided_at),
    )


def _like(needle: str) -> str:
    """A LIKE pattern that matches ``needle`` literally, ``%`` and ``_`` included."""
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
