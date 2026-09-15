"""Run endpoints, including SSE progress (§11).

Alongside the run itself this module exposes the flow as a graph: what each
node consumes, what it publishes, what it actually produced in a given run, and
which gates report on it. A failing run is otherwise a single error string at
the top of the page; the graph is what turns that into a place.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.documents import load_parsed_document
from app.api.introspection import fields_of, is_model, port_for_key, type_name
from app.api.review import edited_texts, segment_states
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.events import bus
from app.ingestion import anchors as anchor_tools
from app.models import Document, GateResult, Run, RunNode, User
from app.pipeline import catalogue
from app.pipeline.catalogue import CatalogueError
from app.pipeline.feedback import last_row_for_key, pause_out
from app.pipeline.formats import DEFAULT_AUDIENCE
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import Node, input_keys
from app.pipeline.framework.registry import Flow, get_node
from app.pipeline.gates import gate_catalogue
from app.pipeline.validation import RUN_SEED_KEYS
from app.schemas.api import (
    AnchorOut,
    CreateRunRequest,
    EdgeOut,
    FlowGraphOut,
    FlowNodeOut,
    FlowOut,
    NodeIOOut,
    NodeRunStatus,
    NodeValueOut,
    PortOut,
    RunGraphNodeOut,
    RunGraphOut,
    RunNodeOut,
    RunOut,
    ScriptOut,
    SegmentCommentOut,
    SegmentOut,
)
from app.schemas.document import ParsedDocument
from app.schemas.gates import GateDetail, GateReport, GateSpec
from app.schemas.pipeline import AudienceSpec, FormatSpec, Script
from app.security import current_user
from app.worker import worker

router = APIRouter(prefix="/api", tags=["runs"])

#: Longest JSON preview a node-inspection response carries. Enough to read the
#: shape of an artifact; the full artifact stays behind its own endpoint.
_PREVIEW_CHARS = 40_000
#: Longest single string kept inside a summary sample.
_SAMPLE_CHARS = 240

#: Seconds between SSE heartbeats when nothing is happening.
_HEARTBEAT_SECONDS = 15.0
#: Events after which the stream closes itself.
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.paused"})


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


# ----------------------------------------------------------------- catalogue


@router.get("/flows", response_model=list[FlowOut])
def list_flows(db: Session = Depends(get_db), _user: User = Depends(current_user)) -> list[FlowOut]:
    """The runnable pipelines. Editing them lives under ``/api/pipelines``."""
    return [
        FlowOut(
            id=flow.id,
            version=flow.version,
            description=flow.description,
            nodes=[n.node for n in flow.nodes],
            gates=flow.gates,
        )
        for flow in catalogue.flows(db).values()
    ]


@router.get("/gates", response_model=list[GateSpec])
def list_gates(_user: User = Depends(current_user)) -> list[GateSpec]:
    """What every gate checks and how, as the gates themselves describe it."""
    return gate_catalogue()


@router.get("/flows/{flow_id}/graph", response_model=FlowGraphOut)
def flow_graph(
    flow_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> FlowGraphOut:
    flow = _require_flow(db, flow_id)
    nodes, edges, seeds = _topology(flow)
    return FlowGraphOut(
        flow_id=flow.id,
        flow_version=flow.version,
        description=flow.description,
        nodes=nodes,
        edges=edges,
        seeds=seeds,
        gates=gate_catalogue(flow.gates or None),
    )


# ---------------------------------------------------------------------- runs


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(
    payload: CreateRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> RunOut:
    document = db.get(Document, payload.document_id)
    if document is None:
        raise problem(404, "No such document", f"Document '{payload.document_id}' does not exist.")
    if document.parse_status != "parsed":
        raise problem(
            409,
            "Document is not parsed",
            f"Parse status is '{document.parse_status}'. Wait for parsing to finish, or "
            "re-parse the document first.",
        )

    try:
        flow = catalogue.get_flow(db, payload.flow_id)
    except CatalogueError as exc:
        raise problem(404, "No such flow", str(exc)) from exc

    try:
        format_spec = catalogue.get_format_spec(db, payload.format_id)
    except CatalogueError as exc:
        raise problem(404, "No such format", str(exc)) from exc

    target_minutes = payload.target_minutes or format_spec.target_minutes
    if target_minutes <= 0:
        raise problem(422, "Invalid target", "target_minutes must be greater than zero.")

    flow_row = catalogue.flow_row(db, flow.id)
    run = Run(
        document_id=document.id,
        flow_id=flow.id,
        flow_version=flow.version,
        flow_revision=flow_row.revision if flow_row else 0,
        config_json={
            "target_minutes": target_minutes,
            "language": payload.language,
            "force": payload.force,
        },
        format_spec_json=format_spec.model_dump(mode="json"),
        audience_spec_json=(payload.audience_spec or DEFAULT_AUDIENCE).model_dump(mode="json"),
        status="queued",
        created_by=user.id,
    )
    db.add(run)
    db.commit()
    worker.submit_run(run.id)
    return _run_out(db, run)


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    document_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[RunOut]:
    statement = select(Run).order_by(Run.created_at.desc()).limit(max(1, min(limit, 500)))
    if document_id:
        statement = statement.where(Run.document_id == document_id)
    return [_run_out(db, run, detailed=False) for run in db.scalars(statement).all()]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> RunOut:
    return _run_out(db, _require_run(db, run_id))


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> StreamingResponse:
    run = _require_run(db, run_id)
    terminal = run.status in {"completed", "failed", "reviewed", "paused"}
    subscriber, replay = bus.subscribe(run_id)

    def stream() -> Iterator[str]:
        try:
            yield ": connected\n\n"
            for event in replay:
                yield event.to_sse()
                # A terminal event in the replay means the run is over; holding
                # the connection open after it would leave the client waiting
                # on heartbeats forever.
                if event.type in _TERMINAL_EVENTS:
                    return
            if terminal:
                yield f'event: run.{run.status}\ndata: {{"run_id": "{run_id}"}}\n\n'
                return
            while True:
                try:
                    event = subscriber.get(timeout=_HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield event.to_sse()
                if event.type in _TERMINAL_EVENTS:
                    return
        finally:
            bus.unsubscribe(run_id, subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/artifacts/{node_name}")
def get_artifact(
    run_id: str,
    node_name: str,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> Any:
    _require_run(db, run_id)
    row = db.scalars(
        select(RunNode).where(RunNode.run_id == run_id, RunNode.node_name == node_name)
    ).first()
    if row is None or not row.artifact_hash:
        raise problem(404, "No artifact", f"Node '{node_name}' produced no artifact in this run.")
    store = _store()
    if not store.exists(row.artifact_hash):
        raise problem(500, "Artifact missing", "The artifact is recorded but absent from disk.")
    return store.get_raw(row.artifact_hash)


@router.get("/runs/{run_id}/gates", response_model=list[GateReport])
def get_gates(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[GateReport]:
    _require_run(db, run_id)
    return _gate_reports(db, run_id)


@router.get("/runs/{run_id}/gates/detail", response_model=list[GateDetail])
def get_gate_detail(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[GateDetail]:
    """Each gate's specification joined with what it did in this run.

    A gate that reports only ``pass`` asks to be trusted. Pairing the rule and
    the method with the numbers the gate computed is what makes the check
    inspectable instead.
    """
    run = _require_run(db, run_id)
    flow = catalogue.flows(db).get(run.flow_id)
    reports = {report.id: report for report in _gate_reports(db, run_id)}
    specs = gate_catalogue((flow.gates or None) if flow else None)

    detail = [GateDetail(spec=spec, report=reports.get(spec.id)) for spec in specs]
    known = {spec.id for spec in specs}
    # A run whose flow has since dropped a gate still has that gate's result.
    for gate_id, report in sorted(reports.items()):
        if gate_id not in known:
            detail.append(
                GateDetail(
                    spec=GateSpec(
                        id=gate_id,
                        name=report.name,
                        severity="warn",
                        rule="This gate is no longer registered.",
                        method=(
                            "The result was recorded by a build in which this gate existed. "
                            "Its rule and thresholds are not recoverable from the code."
                        ),
                    ),
                    report=report,
                )
            )
    return detail


@router.get("/runs/{run_id}/graph", response_model=RunGraphOut)
def run_graph(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> RunGraphOut:
    """The flow as it actually ran: per node, what happened and what it cost."""
    run = _require_run(db, run_id)
    flow = _require_flow(db, run.flow_id)
    nodes, edges, seeds = _topology(flow)

    rows = {
        row.node_name: row
        for row in db.scalars(select(RunNode).where(RunNode.run_id == run.id)).all()
    }
    records = {
        record.get("name"): record
        for record in ((run.manifest_json or {}).get("nodes") or [])
        if isinstance(record, dict)
    }
    findings = _findings_by_node(db, run_id, nodes)
    store = _store()

    failed_node = next((name for name, row in rows.items() if row.error), None)
    reached_failure = False
    out: list[RunGraphNodeOut] = []

    for node in nodes:
        row = rows.get(node.name)
        record = records.get(node.name) or {}
        status = _node_status(row, reached=reached_failure, run_status=run.status)
        if row is not None and row.error:
            reached_failure = True

        summary: dict[str, Any] = {}
        if row is not None and row.artifact_hash and store.exists(row.artifact_hash):
            summary = _summarise(store.get_raw(row.artifact_hash))

        out.append(
            RunGraphNodeOut(
                **node.model_dump(),
                status=status,
                cache_hit=bool(row.cache_hit) if row else False,
                artifact_hash=row.artifact_hash if row else None,
                model_id=(row.model_id if row else None) or record.get("model_id"),
                tokens_in=row.tokens_in if row else 0,
                tokens_out=row.tokens_out if row else 0,
                cost_usd=row.cost_usd if row else 0.0,
                wall_ms=record.get("wall_ms"),
                started_at=_iso(row.started_at) if row else None,
                finished_at=_iso(row.finished_at) if row else None,
                error=row.error if row else None,
                output_summary=summary,
                findings=findings.get(node.name, []),
            )
        )

    return RunGraphOut(
        run_id=run.id,
        flow_id=run.flow_id,
        flow_version=run.flow_version,
        status=run.status,
        nodes=out,
        edges=edges,
        seeds=seeds,
        failed_node=failed_node,
        error=run.error,
        total_cost_usd=run.total_cost_usd,
    )


@router.get("/runs/{run_id}/nodes/{node_name}/io", response_model=NodeIOOut)
def node_io(
    run_id: str,
    node_name: str,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> NodeIOOut:
    """One node's actual inputs and output in this run.

    Inputs are resolved the same way the runner wires them — by field name
    against what earlier nodes published — so what is shown here is what the
    node was given, not a reconstruction that could disagree with it.
    """
    run = _require_run(db, run_id)
    flow = _require_flow(db, run.flow_id)
    entry = next((e for e in flow.nodes if e.node == node_name), None)
    if entry is None:
        raise problem(
            404,
            "No such node",
            f"Flow '{flow.id}' has no node '{node_name}'. It has: "
            f"{', '.join(e.node for e in flow.nodes)}.",
        )

    node = get_node(node_name)
    producers = _producers(flow)
    rows = {
        row.node_name: row
        for row in db.scalars(select(RunNode).where(RunNode.run_id == run.id)).all()
    }
    row = rows.get(node_name)
    record = next(
        (
            r
            for r in ((run.manifest_json or {}).get("nodes") or [])
            if isinstance(r, dict) and r.get("name") == node_name
        ),
        {},
    )
    seed_values = _seed_values(db, run)
    store = _store()

    inputs: list[NodeValueOut] = []
    for key in input_keys(node):
        source = producers.get(key)
        # A key produced by a node that comes later in the flow is not this
        # node's input; only what precedes it can be.
        if source is not None and _position(flow, source) >= _position(flow, node_name):
            source = None

        if source is None:
            value = seed_values.get(key)
            inputs.append(
                _value_out(
                    key=key,
                    model=_model_name_for_key(flow, key) or type(value).__name__,
                    produced_by=None,
                    artifact_hash=None,
                    payload=_jsonable(value),
                    available=key in seed_values,
                )
            )
            continue

        upstream = rows.get(source)
        digest = upstream.artifact_hash if upstream else None
        payload = store.get_raw(digest) if digest and store.exists(digest) else None
        inputs.append(
            _value_out(
                key=key,
                model=get_node(source).Output.__name__,
                produced_by=source,
                artifact_hash=digest,
                payload=payload,
                available=payload is not None,
            )
        )

    output: NodeValueOut | None = None
    digest = row.artifact_hash if row else None
    payload = store.get_raw(digest) if digest and store.exists(digest) else None
    if digest or payload is not None:
        output = _value_out(
            key=node.produces,
            model=node.Output.__name__,
            produced_by=node_name,
            artifact_hash=digest,
            payload=payload,
            available=payload is not None,
        )

    return NodeIOOut(
        run_id=run.id,
        node_name=node_name,
        node_version=(row.node_version if row else None) or node.version,
        status=_node_status(
            row,
            # A node with no row is "blocked" rather than "pending" when an
            # earlier node already failed, which is the same judgement the
            # graph makes.
            reached=any(
                other.error and _position(flow, name) < _position(flow, node_name)
                for name, other in rows.items()
            ),
            run_status=run.status,
        ),
        config={
            **entry.config,
            **((run.config_json or {}).get("node_config") or {}).get(node_name, {}),
        },
        cache_key=row.cache_key if row else None,
        cache_hit=bool(row.cache_hit) if row else False,
        model_id=row.model_id if row else None,
        tokens_in=row.tokens_in if row else 0,
        tokens_out=row.tokens_out if row else 0,
        cost_usd=row.cost_usd if row else 0.0,
        wall_ms=record.get("wall_ms"),
        error=row.error if row else None,
        inputs=inputs,
        output=output,
    )


@router.get("/runs/{run_id}/script", response_model=ScriptOut)
def get_script(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> ScriptOut:
    run = _require_run(db, run_id)
    parsed, script = _load_run_output(db, run)
    reports = _gate_reports(db, run_id)
    violations: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        for violation in report.violations:
            if violation.target_id:
                violations.setdefault(violation.target_id, []).append(
                    {"gate": report.id, "status": report.status, "message": violation.message}
                )

    states = segment_states(db, run_id)
    emails = {u.id: u.email for u in db.scalars(select(User)).all()}

    segments: list[SegmentOut] = []
    for ordinal, segment in enumerate(script.segments):
        state = states.get(segment.id)
        resolved: list[AnchorOut] = []
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
        segments.append(
            SegmentOut(
                id=segment.id,
                ordinal=ordinal,
                speaker=segment.speaker,
                text=(state.text if state else None) or segment.text,
                kind=segment.kind,
                beat_id=segment.beat_id,
                anchors=resolved,
                violations=violations.get(segment.id, []),
                accepted=bool(state and state.accepted),
                flagged=bool(state and state.flagged),
                edited=bool(state and state.edited),
                original_text=segment.text,
                undoable=bool(state and state.undoable),
                tags=list(state.tags) if state else [],
                comments=[
                    SegmentCommentOut(
                        id=comment.id,
                        user_id=comment.user_id,
                        user_email=emails.get(comment.user_id),
                        note=comment.note or "",
                        created_at=comment.created_at.isoformat() if comment.created_at else "",
                    )
                    for comment in (state.comments if state else [])
                ],
            )
        )

    return ScriptOut(
        run_id=run_id,
        document_id=parsed.document_id,
        parse_version=parsed.parse_version,
        language=parsed.language,
        format_spec=FormatSpec.model_validate(run.format_spec_json),
        segments=segments,
        word_count=sum(len(s.text.split()) for s in segments),
    )


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: str,
    format: str = "md",
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> Response:
    if format != "md":
        raise problem(422, "Unsupported format", "Only 'md' is supported in this build.")
    run = _require_run(db, run_id)
    parsed, script = _load_run_output(db, run)
    document = db.get(Document, run.document_id)
    body = _to_markdown(run, document, parsed, script, edited_texts(db, run_id))
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="kalliope-{run_id}.md"'},
    )


# ------------------------------------------------------------------ topology


def _require_flow(db: Session, flow_id: str) -> Flow:
    try:
        return catalogue.get_flow(db, flow_id)
    except CatalogueError as exc:
        raise problem(404, "No such flow", str(exc)) from exc


def _position(flow: Flow, node_name: str) -> int:
    return next((i for i, e in enumerate(flow.nodes) if e.node == node_name), len(flow.nodes))


def _producers(flow: Flow) -> dict[str, str]:
    """Published key → the node that publishes it, later entries winning."""
    return {get_node(entry.node).produces: entry.node for entry in flow.nodes}


def _port(key: str, model: type[BaseModel] | None, produced_by: str | None) -> PortOut:
    return PortOut(
        key=key,
        model=model.__name__ if model else "unknown",
        fields=fields_of(model) if model else [],
        produced_by=produced_by,
    )


def _seed_port(flow: Flow, key: str) -> PortOut:
    """A run seed, typed from whichever node declares it as an input field."""
    for entry in flow.nodes:
        info = get_node(entry.node).Input.model_fields.get(key)
        if info is not None:
            annotation = info.annotation
            return PortOut(
                key=key,
                model=type_name(annotation),
                fields=fields_of(annotation) if is_model(annotation) else [],
                produced_by=None,
            )
    return port_for_key(key)


def _model_name_for_key(flow: Flow, key: str) -> str | None:
    for entry in flow.nodes:
        info = get_node(entry.node).Input.model_fields.get(key)
        if info is not None:
            return type_name(info.annotation)
    return None


def _description(node: Node) -> str | None:
    doc = (type(node).__doc__ or "").strip()
    return " ".join(doc.split()) or None


def _topology(flow: Flow) -> tuple[list[FlowNodeOut], list[EdgeOut], list[PortOut]]:
    """Nodes, wiring and run seeds, derived from the node classes themselves.

    Wiring is by field name — exactly the rule the runner applies — so adding a
    node that consumes an existing value shows up here with no change of its own.
    """
    producers = _producers(flow)
    gates_by_node: dict[str, list[str]] = {}
    for spec in gate_catalogue(flow.gates or None):
        for node_name in spec.inspects:
            gates_by_node.setdefault(node_name, []).append(spec.id)

    nodes: list[FlowNodeOut] = []
    edges: list[EdgeOut] = []
    seed_keys: list[str] = []

    for index, entry in enumerate(flow.nodes):
        node = get_node(entry.node)
        consumes: list[PortOut] = []
        for key in input_keys(node):
            info = node.Input.model_fields[key]
            source = producers.get(key)
            if source is not None and _position(flow, source) >= index:
                source = None
            if (
                source is None
                and key not in RUN_SEED_KEYS
                and not info.is_required()
            ):
                continue
            if source is None and key not in seed_keys:
                seed_keys.append(key)
            consumes.append(
                _port(key, get_node(source).Output if source else None, source)
                if source
                else _seed_port(flow, key)
            )
            edges.append(EdgeOut(from_node=source, to_node=entry.node, key=key))

        nodes.append(
            FlowNodeOut(
                name=node.name,
                version=node.version,
                description=_description(node),
                consumes=consumes,
                produces=_port(node.produces, node.Output, node.name),
                config=dict(entry.config),
                checked_by=sorted(gates_by_node.get(node.name, [])),
            )
        )

    return nodes, edges, [_seed_port(flow, key) for key in seed_keys]


def _node_status(row: RunNode | None, *, reached: bool, run_status: str) -> NodeRunStatus:
    """What happened to one node, from its row and the run around it."""
    if row is None:
        if reached or run_status == "failed":
            return "blocked"
        return "running" if run_status == "running" else "pending"
    if row.error:
        return "failed"
    if row.finished_at is None:
        return "paused" if run_status == "paused" else "running"
    return "cached" if row.cache_hit else "ok"


def _findings_by_node(
    db: Session, run_id: str, nodes: list[FlowNodeOut]
) -> dict[str, list[dict[str, Any]]]:
    """Gate results grouped by the node whose output the gate inspects."""
    reports = {report.id: report for report in _gate_reports(db, run_id)}
    out: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        for gate_id in node.checked_by:
            report = reports.get(gate_id)
            if report is None:
                continue
            out.setdefault(node.name, []).append(
                {
                    "gate": report.id,
                    "name": report.name,
                    "status": report.status,
                    "violations": len(report.violations),
                }
            )
    return out


def _seed_values(db: Session, run: Run) -> dict[str, Any]:
    """The values the worker seeds a run with, read back off the run row."""
    document = db.get(Document, run.document_id)
    config = run.config_json or {}
    seeds: dict[str, Any] = {
        "target_minutes": config.get("target_minutes"),
        "format_spec": run.format_spec_json,
        "audience_spec": run.audience_spec_json,
    }
    if document is not None:
        seeds["document_ref"] = {
            "document_id": document.id,
            "parse_version": document.parse_version,
            "sha256": document.sha256,
            "parsed_artifact_hash": document.parsed_artifact_hash,
            "language_override": config.get("language"),
        }
    return {key: value for key, value in seeds.items() if value is not None}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _value_out(
    *,
    key: str,
    model: str,
    produced_by: str | None,
    artifact_hash: str | None,
    payload: Any,
    available: bool,
) -> NodeValueOut:
    if not available or payload is None:
        return NodeValueOut(
            key=key,
            model=model,
            produced_by=produced_by,
            artifact_hash=artifact_hash,
            available=False,
        )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    truncated = len(text) > _PREVIEW_CHARS
    return NodeValueOut(
        key=key,
        model=model,
        produced_by=produced_by,
        artifact_hash=artifact_hash,
        summary=_summarise(payload),
        preview=text[:_PREVIEW_CHARS],
        truncated=truncated,
        available=True,
    )


def _summarise(payload: Any) -> dict[str, Any]:
    """Shape of a value, without shipping it.

    Deliberately structural rather than node-aware: it reports counts, scalars
    and one sample per collection for whatever it is handed, so a node added
    later is summarised without anyone teaching this function about it.
    """
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__, "value": _sample(payload)}

    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            out[key] = {"count": len(value), "sample": _sample(value[0]) if value else None}
        elif isinstance(value, dict):
            out[key] = {"keys": sorted(value)[:20], "size": len(value)}
        else:
            out[key] = _sample(value)
    return out


def _sample(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_SAMPLE_CHARS] + ("…" if len(value) > _SAMPLE_CHARS else "")
    if isinstance(value, dict):
        return {k: _sample(v) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return {"count": len(value), "first": _sample(value[0]) if value else None}
    return value


# -------------------------------------------------------------- serialisation


def _to_markdown(
    run: Run,
    document: Document | None,
    parsed: ParsedDocument,
    script: Script,
    edits: dict[str, str],
) -> str:
    title = (document.title if document else None) or (document.filename if document else "Skript")
    lines = [
        f"# {title}",
        "",
        f"- Run: `{run.id}`",
        f"- Flow: `{run.flow_id}` v{run.flow_version}",
        f"- Dokument: `{parsed.document_id}` (Parse-Version {parsed.parse_version})",
        f"- Sprache: `{parsed.language}`",
        f"- Kosten: ${run.total_cost_usd:.4f}",
        "",
        "---",
        "",
    ]

    footnotes: list[str] = []
    seen: dict[tuple[str, int, int], int] = {}
    for segment in script.segments:
        markers = []
        for anchor in segment.anchors:
            key = (anchor.block_id, anchor.char_start, anchor.char_end)
            if key not in seen:
                seen[key] = len(seen) + 1
                block = parsed.block_by_id(anchor.block_id)
                quote = (
                    block.text[anchor.char_start : anchor.char_end]
                    if block
                    else "(nicht auflösbar)"
                )
                page = (block.page + 1) if block else "?"
                footnotes.append(
                    f"[^{seen[key]}]: S. {page}, Block `{anchor.block_id}` "
                    f"[{anchor.char_start}–{anchor.char_end}]: „{quote}“"
                )
            markers.append(f"[^{seen[key]}]")
        text = edits.get(segment.id, segment.text)
        lines.append(f"**{segment.speaker}:** {text}{''.join(markers)}")
        lines.append("")

    if footnotes:
        lines.extend(["---", "", *footnotes, ""])
    return "\n".join(lines)


def _load_run_output(db: Session, run: Run) -> tuple[ParsedDocument, Script]:
    document = db.get(Document, run.document_id)
    if document is None:
        raise problem(404, "No such document", "The run's document no longer exists.")

    store = _store()
    parsed_row = db.scalars(
        select(RunNode).where(RunNode.run_id == run.id, RunNode.node_name == "ingest")
    ).first()
    if parsed_row and parsed_row.artifact_hash and store.exists(parsed_row.artifact_hash):
        parsed = ParsedDocument.model_validate(store.get_raw(parsed_row.artifact_hash))
    else:
        parsed = load_parsed_document(document)

    flow = catalogue.flows(db).get(run.flow_id)
    script_row = (
        last_row_for_key(db, run_id=run.id, flow=flow, key="script")
        if flow is not None
        else db.scalars(
            select(RunNode).where(RunNode.run_id == run.id, RunNode.node_name == "script")
        ).first()
    )
    if script_row is None or not script_row.artifact_hash:
        raise problem(
            409,
            "No script yet",
            f"Run '{run.id}' has status '{run.status}' and produced no script artifact.",
        )
    script = Script.model_validate(store.get_raw(script_row.artifact_hash))
    return parsed, script


def _gate_reports(db: Session, run_id: str) -> list[GateReport]:
    rows = db.scalars(
        select(GateResult).where(GateResult.run_id == run_id).order_by(GateResult.gate_id)
    ).all()
    # Only the id and the result are stored; the name comes from the gate, so a
    # gate that was renamed reads under its current name rather than its id.
    names = {spec.id: spec.name for spec in gate_catalogue()}
    return [
        GateReport.model_validate(
            {
                "id": row.gate_id,
                "name": names.get(row.gate_id, row.gate_id),
                "status": row.status,
                "violations": row.violations_json or [],
                "skip_reason": row.skip_reason,
                "measurements": row.measurements_json or {},
            }
        )
        for row in rows
    ]


def _run_out(db: Session, run: Run, detailed: bool = True) -> RunOut:
    document = db.get(Document, run.document_id)
    config = run.config_json or {}
    nodes: list[RunNodeOut] = []
    gates: list[GateReport] = []
    if detailed:
        rows = db.scalars(
            select(RunNode).where(RunNode.run_id == run.id).order_by(RunNode.started_at)
        ).all()
        nodes = [
            RunNodeOut(
                node_name=row.node_name,
                node_version=row.node_version,
                cache_hit=row.cache_hit,
                artifact_hash=row.artifact_hash,
                cost_usd=row.cost_usd,
                tokens_in=row.tokens_in,
                tokens_out=row.tokens_out,
                model_id=row.model_id,
                started_at=_iso(row.started_at),
                finished_at=_iso(row.finished_at),
                error=row.error,
            )
            for row in rows
        ]
        gates = _gate_reports(db, run.id)

    return RunOut(
        id=run.id,
        document_id=run.document_id,
        document_title=(document.title or document.filename) if document else None,
        flow_id=run.flow_id,
        flow_version=run.flow_version,
        status=run.status,
        created_at=_iso(run.created_at) or "",
        started_at=_iso(run.started_at),
        finished_at=_iso(run.finished_at),
        error=run.error,
        total_cost_usd=run.total_cost_usd,
        target_minutes=config.get("target_minutes"),
        verdict=config.get("verdict"),
        format_spec=FormatSpec.model_validate(run.format_spec_json) if detailed else None,
        audience_spec=AudienceSpec.model_validate(run.audience_spec_json) if detailed else None,
        nodes=nodes,
        gates=gates,
        manifest=run.manifest_json if detailed else None,
        pause=pause_out(run.manifest_json),
    )


def _require_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise problem(404, "No such run", f"Run '{run_id}' does not exist.")
    return run


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
