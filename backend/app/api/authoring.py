"""Editing pipelines and format specifications (§6.5, §7.5, §11).

Every write here appends a revision and never overwrites one, including a
restore — restoring revision 3 writes revision 7 carrying revision 3's content.
The history is therefore the complete record of what the pipeline has ever been,
which is what makes a run from last month explainable at all.

The editor's shape follows the runner's, not a general graph tool's: a flow is an
ordered list of nodes, and wiring is by value name — a node consumes whatever an
earlier node published under the name it asks for. So "wiring up nodes" here is
choosing which nodes run and in what order; this module reports the resulting
connections and refuses an order that cannot supply a node's inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.introspection import fields_of, port_for_key
from app.db import get_db
from app.errors import problem
from app.llm import registry as llm_registry
from app.models import Flow as FlowRow
from app.models import FlowVersion, FormatSpecRow, FormatVersion, Run, User
from app.pipeline import catalogue
from app.pipeline.catalogue import CatalogueError
from app.pipeline.framework.registry import Flow, get_node, node_names
from app.pipeline.framework.spec import node_doc, node_params, node_title, prune_defaults
from app.pipeline.gates import gate_catalogue
from app.pipeline.validation import REQUIRED_OUTPUT_KEYS, RUN_SEED_KEYS, validate_flow
from app.schemas.authoring import (
    ArchiveRequest,
    FlowNodeIn,
    FormatDetailOut,
    FormatSave,
    FormatSummaryOut,
    NodeCatalogueOut,
    NodeSpecOut,
    PipelineCreate,
    PipelineDetailOut,
    PipelineDraft,
    PipelineSave,
    PipelineSummaryOut,
    RevisionOut,
)
from app.schemas.pipeline import FormatSpec
from app.security import current_user

router = APIRouter(prefix="/api", tags=["authoring"])


def _refuse(exc: CatalogueError) -> NoReturn:
    raise problem(409, "Nicht möglich", str(exc)) from exc


# ------------------------------------------------------------------- nodes


@router.get("/nodes", response_model=NodeCatalogueOut)
def list_nodes(_user: User = Depends(current_user)) -> NodeCatalogueOut:
    """Every registered node, with its documentation and editable parameters."""
    gates_by_node: dict[str, list[str]] = {}
    for spec in gate_catalogue():
        for name in spec.inspects:
            gates_by_node.setdefault(name, []).append(spec.id)

    nodes: list[NodeSpecOut] = []
    for name in node_names():
        node = get_node(name)
        nodes.append(
            NodeSpecOut(
                name=node.name,
                title=node_title(node),
                version=node.version,
                doc=node_doc(node),
                params=node_params(node),
                consumes=list(node.Input.model_fields),
                produces=node.produces,
                input_model=node.Input.__name__,
                output_model=node.Output.__name__,
                output_fields=fields_of(node.Output),
                checked_by=sorted(gates_by_node.get(node.name, [])),
            )
        )

    return NodeCatalogueOut(
        nodes=nodes,
        seeds=[port_for_key(key) for key in RUN_SEED_KEYS],
        required_outputs=list(REQUIRED_OUTPUT_KEYS),
        models=sorted(llm_registry.MODELS),
    )


# --------------------------------------------------------------- pipelines


@router.get("/pipelines", response_model=list[PipelineSummaryOut])
def list_pipelines(
    include_archived: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[PipelineSummaryOut]:
    rows = {row.id: row for row in db.scalars(select(FlowRow)).all()}
    available = catalogue.flows(db)
    counts = _run_counts(db)
    emails = _emails(db)

    ids = sorted(
        set(available) | {i for i, r in rows.items() if include_archived or not r.archived}
    )
    out: list[PipelineSummaryOut] = []
    for flow_id in ids:
        row = rows.get(flow_id)
        flow = available.get(flow_id) or (catalogue.flow_from_row(row) if row else None)
        if flow is None:
            continue
        if row is not None and row.archived and not include_archived:
            continue
        out.append(_summary(flow, row, counts.get(flow_id, 0), emails))
    return out


@router.post("/pipelines", response_model=PipelineDetailOut, status_code=201)
def create_pipeline(
    payload: PipelineCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PipelineDetailOut:
    spec = _spec_from_draft(payload)
    try:
        row = catalogue.create_flow(
            db,
            flow_id=payload.id,
            name=(payload.name or payload.id).strip(),
            version=payload.version.strip() or "1.0",
            spec=spec,
            user_id=user.id,
            note=payload.note,
        )
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _detail(db, row.id)


@router.post("/pipelines/validate")
def validate_pipeline(payload: PipelineDraft, _user: User = Depends(current_user)) -> Any:
    """Check a draft without saving it. What the editor calls on every change."""
    return validate_flow([n.model_dump() for n in payload.nodes], payload.gates)


@router.get("/pipelines/{flow_id}", response_model=PipelineDetailOut)
def get_pipeline(
    flow_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> PipelineDetailOut:
    return _detail(db, flow_id)


@router.put("/pipelines/{flow_id}", response_model=PipelineDetailOut)
def save_pipeline(
    flow_id: str,
    payload: PipelineSave,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PipelineDetailOut:
    spec = _spec_from_draft(payload)
    try:
        catalogue.save_flow(
            db,
            flow_id=flow_id,
            name=payload.name,
            version=payload.version.strip() or "1.0",
            spec=spec,
            user_id=user.id,
            note=payload.note,
        )
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _detail(db, flow_id)


@router.post("/pipelines/{flow_id}/archive", response_model=PipelineSummaryOut)
def archive_pipeline(
    flow_id: str,
    payload: ArchiveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PipelineSummaryOut:
    """Take a pipeline out of circulation, or bring it back.

    Never a delete: runs point at their flow by id, and removing the row would
    leave those runs referring to nothing.
    """
    try:
        row = catalogue.archive_flow(
            db, flow_id=flow_id, archived=payload.archived, user_id=user.id
        )
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    flow = catalogue.flow_from_row(row)
    return _summary(flow, row, _run_counts(db).get(flow_id, 0), _emails(db))


@router.get("/pipelines/{flow_id}/versions", response_model=list[RevisionOut])
def pipeline_versions(
    flow_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[RevisionOut]:
    emails = _emails(db)
    return [_revision(row, emails) for row in catalogue.flow_history(db, flow_id)]


@router.get("/pipelines/{flow_id}/versions/{revision}", response_model=RevisionOut)
def pipeline_version(
    flow_id: str,
    revision: int,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> RevisionOut:
    try:
        row = catalogue.flow_revision(db, flow_id, revision)
    except CatalogueError as exc:
        raise problem(404, "Keine solche Revision", str(exc)) from exc
    return _revision(row, _emails(db), with_spec=True)


@router.post("/pipelines/{flow_id}/versions/{revision}/restore", response_model=PipelineDetailOut)
def restore_pipeline(
    flow_id: str,
    revision: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PipelineDetailOut:
    try:
        catalogue.restore_flow(db, flow_id=flow_id, revision=revision, user_id=user.id)
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _detail(db, flow_id)


# ----------------------------------------------------------------- formats


@router.get("/formats", response_model=list[FormatSpec])
def list_formats(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[FormatSpec]:
    """The formats a run can be started with. Used by the new-run form."""
    return [spec for _, spec in sorted(catalogue.formats(db).items())]


@router.get("/format-specs", response_model=list[FormatSummaryOut])
def list_format_specs(
    include_archived: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[FormatSummaryOut]:
    rows = {row.id: row for row in db.scalars(select(FormatSpecRow)).all()}
    specs = catalogue.formats(db)
    emails = _emails(db)
    counts = _format_run_counts(db)

    out: list[FormatSummaryOut] = []
    for format_id in sorted(set(specs) | (set(rows) if include_archived else set())):
        row = rows.get(format_id)
        spec = specs.get(format_id)
        if spec is None and row is not None:
            spec = FormatSpec.model_validate(row.spec_json)
        if spec is None:
            continue
        out.append(_format_summary(spec, row, counts.get(format_id, 0), emails))
    return out


@router.post("/format-specs", response_model=FormatDetailOut, status_code=201)
def create_format(
    payload: FormatSave,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FormatDetailOut:
    try:
        row = catalogue.create_format(db, spec=payload.spec, user_id=user.id, note=payload.note)
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _format_detail(db, row.id)


@router.get("/format-specs/{format_id}", response_model=FormatDetailOut)
def get_format_spec(
    format_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> FormatDetailOut:
    return _format_detail(db, format_id)


@router.put("/format-specs/{format_id}", response_model=FormatDetailOut)
def save_format_spec(
    format_id: str,
    payload: FormatSave,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FormatDetailOut:
    _check_format(payload.spec)
    try:
        catalogue.save_format(
            db, format_id=format_id, spec=payload.spec, user_id=user.id, note=payload.note
        )
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _format_detail(db, format_id)


@router.post("/format-specs/{format_id}/archive", response_model=FormatSummaryOut)
def archive_format(
    format_id: str,
    payload: ArchiveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FormatSummaryOut:
    try:
        row = catalogue.archive_format(
            db, format_id=format_id, archived=payload.archived, user_id=user.id
        )
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    spec = FormatSpec.model_validate(row.spec_json)
    return _format_summary(spec, row, _format_run_counts(db).get(format_id, 0), _emails(db))


@router.get("/format-specs/{format_id}/versions", response_model=list[RevisionOut])
def format_versions(
    format_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[RevisionOut]:
    emails = _emails(db)
    return [_format_revision(row, emails) for row in catalogue.format_history(db, format_id)]


@router.get("/format-specs/{format_id}/versions/{revision}", response_model=RevisionOut)
def format_version(
    format_id: str,
    revision: int,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> RevisionOut:
    try:
        row = catalogue.format_revision(db, format_id, revision)
    except CatalogueError as exc:
        raise problem(404, "Keine solche Revision", str(exc)) from exc
    return _format_revision(row, _emails(db), with_spec=True)


@router.post(
    "/format-specs/{format_id}/versions/{revision}/restore", response_model=FormatDetailOut
)
def restore_format(
    format_id: str,
    revision: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FormatDetailOut:
    try:
        catalogue.restore_format(db, format_id=format_id, revision=revision, user_id=user.id)
    except CatalogueError as exc:
        _refuse(exc)
    db.commit()
    return _format_detail(db, format_id)


# ----------------------------------------------------------------- helpers


def _spec_from_draft(draft: PipelineDraft) -> dict[str, Any]:
    """Normalise a posted draft into a stored flow definition.

    Config entries that only restate a node's default are dropped: the node
    cache key is a hash of the config, so persisting resolved defaults would
    invalidate every cached artifact of that node without changing behaviour
    (§7.2).
    """
    nodes: list[dict[str, Any]] = []
    for entry in draft.nodes:
        config = _clean_config(entry)
        nodes.append({"node": entry.node, "config": config})
    return {
        "description": (draft.description or "").strip() or None,
        "nodes": nodes,
        "gates": list(draft.gates),
    }


def _clean_config(entry: FlowNodeIn) -> dict[str, Any]:
    config = {k: v for k, v in entry.config.items() if v is not None and v != ""}
    try:
        node = get_node(entry.node)
    except KeyError:
        return config
    return prune_defaults(node, config)


def _draft_from_flow(flow: Flow, row: FlowRow | None) -> PipelineDraft:
    return PipelineDraft(
        name=(row.name if row else None) or flow.id,
        version=flow.version,
        description=flow.description,
        nodes=[FlowNodeIn(node=n.node, config=dict(n.config)) for n in flow.nodes],
        gates=list(flow.gates),
    )


def _summary(
    flow: Flow, row: FlowRow | None, run_count: int, emails: dict[str, str]
) -> PipelineSummaryOut:
    validation = validate_flow(
        [{"node": n.node, "config": n.config} for n in flow.nodes], flow.gates
    )
    return PipelineSummaryOut(
        id=flow.id,
        name=(row.name if row else None) or flow.id,
        version=flow.version,
        revision=row.revision if row else 0,
        description=flow.description,
        origin=row.origin if row else "file",
        archived=bool(row.archived) if row else False,
        nodes=[n.node for n in flow.nodes],
        gates=list(flow.gates),
        updated_at=_iso(row.updated_at) if row else None,
        updated_by_email=emails.get(row.updated_by or "") if row else None,
        run_count=run_count,
        valid=validation.valid,
    )


def _detail(db: Session, flow_id: str) -> PipelineDetailOut:
    row = catalogue.flow_row(db, flow_id)
    flow = catalogue.flows(db).get(flow_id)
    if flow is None and row is not None:
        flow = catalogue.flow_from_row(row)
    if flow is None:
        raise problem(
            404,
            "Keine solche Pipeline",
            f"'{flow_id}' existiert nicht. Bekannt: "
            f"{', '.join(sorted(catalogue.flows(db))) or 'keine'}.",
        )

    summary = _summary(flow, row, _run_counts(db).get(flow_id, 0), _emails(db))
    return PipelineDetailOut(
        **summary.model_dump(),
        definition=_draft_from_flow(flow, row),
        validation=validate_flow(
            [{"node": n.node, "config": n.config} for n in flow.nodes], flow.gates
        ),
    )


def _revision(row: FlowVersion, emails: dict[str, str], with_spec: bool = False) -> RevisionOut:
    return RevisionOut(
        revision=row.revision,
        version=row.version,
        note=row.note,
        restored_from=row.restored_from,
        created_at=_iso(row.created_at) or "",
        created_by=row.created_by,
        created_by_email=emails.get(row.created_by or ""),
        spec=dict(row.spec_json or {}) if with_spec else None,
    )


def _format_summary(
    spec: FormatSpec, row: FormatSpecRow | None, run_count: int, emails: dict[str, str]
) -> FormatSummaryOut:
    return FormatSummaryOut(
        id=spec.id,
        name=spec.name,
        revision=row.revision if row else 0,
        origin=row.origin if row else "file",
        archived=bool(row.archived) if row else False,
        speakers=len(spec.speakers),
        target_minutes=spec.target_minutes,
        register=spec.register,
        updated_at=_iso(row.updated_at) if row else None,
        updated_by_email=emails.get(row.updated_by or "") if row else None,
        run_count=run_count,
    )


def _format_detail(db: Session, format_id: str) -> FormatDetailOut:
    row = catalogue.format_row(db, format_id)
    spec = catalogue.formats(db).get(format_id)
    if spec is None and row is not None:
        spec = FormatSpec.model_validate(row.spec_json)
    if spec is None:
        raise problem(404, "Kein solches Format", f"'{format_id}' existiert nicht.")
    summary = _format_summary(spec, row, _format_run_counts(db).get(format_id, 0), _emails(db))
    return FormatDetailOut(**summary.model_dump(), spec=spec)


def _format_revision(
    row: FormatVersion, emails: dict[str, str], with_spec: bool = False
) -> RevisionOut:
    return RevisionOut(
        revision=row.revision,
        version=str(row.revision),
        note=row.note,
        restored_from=row.restored_from,
        created_at=_iso(row.created_at) or "",
        created_by=row.created_by,
        created_by_email=emails.get(row.created_by or ""),
        spec=dict(row.spec_json or {}) if with_spec else None,
    )


def _check_format(spec: FormatSpec) -> None:
    if not spec.speakers:
        raise problem(
            422,
            "Format ohne Sprecher",
            "Ein Format braucht mindestens einen Sprecher: der Skript-Knoten schreibt jede "
            "Zeile einem benannten Sprecher zu und bricht sonst ab.",
        )
    ids = [s.id for s in spec.speakers]
    if len(set(ids)) != len(ids):
        raise problem(422, "Doppelte Sprecher-Id", "Jede Sprecher-Id darf nur einmal vorkommen.")
    names = [s.name.strip().casefold() for s in spec.speakers]
    if len(set(names)) != len(names):
        raise problem(
            422,
            "Doppelter Sprechername",
            "Zwei Sprecher mit demselben Namen lassen sich im Skript nicht auseinanderhalten.",
        )
    if spec.target_minutes <= 0:
        raise problem(422, "Ungültige Ziellänge", "Die Ziellänge muss größer als null sein.")


def _run_counts(db: Session) -> dict[str, int]:
    return {
        flow_id: count
        for flow_id, count in db.execute(
            select(Run.flow_id, func.count(Run.id)).group_by(Run.flow_id)
        ).all()
    }


def _format_run_counts(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for (payload,) in db.execute(select(Run.format_spec_json)).all():
        if isinstance(payload, dict) and payload.get("id"):
            key = str(payload["id"])
            counts[key] = counts.get(key, 0) + 1
    return counts


def _emails(db: Session) -> dict[str, str]:
    return {row.id: row.email for row in db.scalars(select(User)).all()}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
