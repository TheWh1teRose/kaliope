"""The node testing bench (§ debug / develop).

A bench run is a short chain executed against a value bag. The bag can be
loaded from a real document or a previous run and edited at any time. Results
never appear in the production run list.
"""

from __future__ import annotations

import queue
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.events import bus
from app.llm import registry as llm_registry
from app.models import BenchNode, BenchRun, Document, User
from app.pipeline import catalogue
from app.pipeline.bench import (
    load_values,
    resolve_config,
    resolve_seeds,
    validate_bench,
    value_catalogue,
    value_out,
)
from app.pipeline.catalogue import CatalogueError
from app.pipeline.feedback import pause_out
from app.pipeline.formats import DEFAULT_AUDIENCE, DEFAULT_FORMAT_ID, FORMATS
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import get_node
from app.schemas.authoring import FlowNodeIn
from app.schemas.bench import (
    BenchCatalogueOut,
    BenchLoadIn,
    BenchLoadOut,
    BenchNodeOut,
    BenchRunIn,
    BenchRunOut,
    BenchValidateIn,
    BenchValueOut,
    LLMTraceOut,
)
from app.security import current_user
from app.worker import worker

router = APIRouter(prefix="/api/bench", tags=["bench"])

_HEARTBEAT_SECONDS = 15.0
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.paused"})


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


@router.get("/catalogue", response_model=BenchCatalogueOut)
def get_catalogue(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> BenchCatalogueOut:
    try:
        default_format = catalogue.get_format_spec(db, DEFAULT_FORMAT_ID)
    except CatalogueError:
        default_format = FORMATS[DEFAULT_FORMAT_ID]
    return BenchCatalogueOut(
        values=value_catalogue(),
        models=sorted(llm_registry.MODELS),
        default_format=default_format,
        default_audience=DEFAULT_AUDIENCE,
    )


@router.post("/validate")
def validate(payload: BenchValidateIn, _user: User = Depends(current_user)) -> Any:
    return validate_bench([n.model_dump() for n in payload.nodes], payload.seed_keys)


@router.post("/load", response_model=BenchLoadOut)
def load(
    payload: BenchLoadIn,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> BenchLoadOut:
    if not any((payload.document_id, payload.run_id, payload.bench_run_id, payload.format_id)):
        raise problem(
            422,
            "Nothing to load",
            "Name a document, a run, a bench run or a format.",
        )
    try:
        values, nodes, document_id = load_values(
            db,
            _store(),
            document_id=payload.document_id,
            run_id=payload.run_id,
            bench_run_id=payload.bench_run_id,
            format_id=payload.format_id,
            keys=payload.keys,
            include_payload=payload.include_payload,
        )
    except KeyError as exc:
        raise problem(404, "Not found", str(exc)) from exc
    except CatalogueError as exc:
        raise problem(404, "No such format", str(exc)) from exc
    return BenchLoadOut(values=values, nodes=nodes, document_id=document_id)


@router.get("/artifacts/{digest}")
def get_artifact(digest: str, _user: User = Depends(current_user)) -> Any:
    store = _store()
    if not store.exists(digest):
        raise problem(404, "No artifact", f"Artifact '{digest}' is not in the store.")
    return store.get_raw(digest)


@router.post("/runs", response_model=BenchRunOut, status_code=201)
def create_run(
    payload: BenchRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> BenchRunOut:
    entries = _slice(payload)
    seed_keys = list(payload.seeds)
    if payload.document_id and "document_ref" not in seed_keys:
        seed_keys.append("document_ref")

    check = validate_bench([n.model_dump() for n in entries], seed_keys)
    if not check.valid:
        raise problem(422, "This chain cannot run", check.errors[0], errors=check.errors)

    document: Document | None = None
    if payload.document_id:
        document = db.get(Document, payload.document_id)
        if document is None:
            raise problem(
                404, "No such document", f"Document '{payload.document_id}' does not exist."
            )

    store = _store()
    try:
        _, hashes = resolve_seeds(store, payload.seeds)
    except ValueError as exc:
        raise problem(422, "Invalid seed", str(exc)) from exc

    if document is not None and "document_ref" not in hashes:
        from app.pipeline.bench import document_ref_for

        stored = store.put_raw(
            "bench:document_ref", document_ref_for(document).model_dump(mode="json")
        )
        hashes["document_ref"] = stored.hash

    resolved = [
        FlowNodeIn(node=entry.node, config=resolve_config(entry.node, entry.config))
        for entry in entries
    ]
    row = BenchRun(
        document_id=payload.document_id,
        nodes_json=[n.model_dump() for n in resolved],
        seeds_json={
            key: {"artifact_hash": digest, "model": _model_name(key)}
            for key, digest in hashes.items()
        },
        status="queued",
        created_by=user.id,
        force=payload.force,
    )
    db.add(row)
    db.commit()
    worker.submit_bench(row.id)
    return _run_out(db, row, detailed=True)


@router.get("/runs", response_model=list[BenchRunOut])
def list_runs(
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[BenchRunOut]:
    rows = db.scalars(
        select(BenchRun).order_by(BenchRun.created_at.desc()).limit(max(1, min(limit, 200)))
    ).all()
    return [_run_out(db, row, detailed=False) for row in rows]


@router.get("/runs/{bench_id}", response_model=BenchRunOut)
def get_run(
    bench_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> BenchRunOut:
    return _run_out(db, _require(db, bench_id), detailed=True)


@router.get("/runs/{bench_id}/events")
def run_events(
    bench_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> StreamingResponse:
    row = _require(db, bench_id)
    terminal = row.status in {"completed", "failed", "paused"}
    subscriber, replay = bus.subscribe(bench_id)

    def stream() -> Iterator[str]:
        try:
            yield ": connected\n\n"
            for event in replay:
                yield event.to_sse()
                if event.type in _TERMINAL_EVENTS:
                    return
            if terminal:
                yield f'event: run.{row.status}\ndata: {{"run_id": "{bench_id}"}}\n\n'
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
            bus.unsubscribe(bench_id, subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{bench_id}/values/{key}", response_model=BenchValueOut)
def get_value(
    bench_id: str,
    key: str,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> BenchValueOut:
    row = _require(db, bench_id)
    digest = (row.bag_hashes_json or {}).get(key)
    if not digest:
        meta = (row.seeds_json or {}).get(key) or {}
        digest = meta.get("artifact_hash")
    if not digest:
        raise problem(404, "No such value", f"'{key}' is not in this bench run.")
    store = _store()
    if not store.exists(digest):
        raise problem(404, "Artifact missing", f"'{key}' is recorded but absent from disk.")
    produced_by = next(
        (
            entry.get("node")
            for entry in (row.nodes_json or [])
            if _produces(entry.get("node"), key)
        ),
        None,
    )
    return value_out(
        key=key,
        model=_model_name(key),
        payload=store.get_raw(digest),
        artifact_hash=digest,
        source="produced" if produced_by else "seed",
        source_id=row.id,
        produced_by=produced_by,
        include_payload=True,
    )


def _slice(payload: BenchRunIn) -> list[FlowNodeIn]:
    entries = list(payload.nodes)
    names = [entry.node for entry in entries]
    if payload.only:
        if payload.only not in names:
            raise problem(422, "Unknown node", f"'{payload.only}' is not in this chain.")
        return [entry for entry in entries if entry.node == payload.only]
    if payload.from_node:
        if payload.from_node not in names:
            raise problem(422, "Unknown node", f"'{payload.from_node}' is not in this chain.")
        return entries[names.index(payload.from_node) :]
    return entries


def _require(db: Session, bench_id: str) -> BenchRun:
    row = db.get(BenchRun, bench_id)
    if row is None:
        raise problem(404, "No such bench run", f"Bench run '{bench_id}' does not exist.")
    return row


def _model_name(key: str) -> str:
    from app.api.introspection import type_name
    from app.pipeline.bench import annotation_for_key, unwrap

    annotation = annotation_for_key(key)
    return type_name(unwrap(annotation)) if annotation is not None else "unknown"


def _produces(node_name: str | None, key: str) -> bool:
    if not node_name:
        return False
    try:
        return get_node(node_name).produces == key
    except KeyError:
        return False


def _run_out(db: Session, row: BenchRun, *, detailed: bool) -> BenchRunOut:
    document = db.get(Document, row.document_id) if row.document_id else None
    node_rows = {
        item.node_name: item
        for item in db.scalars(select(BenchNode).where(BenchNode.bench_run_id == row.id)).all()
    }
    manifest_nodes = {
        record.get("name"): record
        for record in ((row.manifest_json or {}).get("nodes") or [])
        if isinstance(record, dict)
    }
    records: list[BenchNodeOut] = []
    failed_before = False
    for entry in row.nodes_json or []:
        name = str(entry.get("node"))
        item = node_rows.get(name)
        record = manifest_nodes.get(name) or {}
        if item is None:
            status = (
                "blocked"
                if failed_before or row.status == "failed"
                else ("running" if row.status == "running" else "pending")
            )
        elif item.error:
            status = "failed"
            failed_before = True
        elif item.finished_at is None:
            status = "paused" if row.status == "paused" else "running"
        else:
            status = "cached" if item.cache_hit else "ok"
        records.append(
            BenchNodeOut(
                node_name=name,
                node_version=(item.node_version if item else None) or _node_version(name),
                cache_hit=bool(item.cache_hit) if item else False,
                artifact_hash=item.artifact_hash if item else None,
                cost_usd=item.cost_usd if item else 0.0,
                tokens_in=item.tokens_in if item else 0,
                tokens_out=item.tokens_out if item else 0,
                model_id=item.model_id if item else None,
                started_at=_iso(item.started_at) if item else None,
                finished_at=_iso(item.finished_at) if item else None,
                wall_ms=record.get("wall_ms"),
                error=item.error if item else None,
                status=status,
            )
        )

    values: list[BenchValueOut] = []
    if detailed:
        store = _store()
        for key, digest in (row.bag_hashes_json or {}).items():
            if not isinstance(digest, str) or not store.exists(digest):
                continue
            produced_by = next(
                (
                    entry.get("node")
                    for entry in (row.nodes_json or [])
                    if _produces(entry.get("node"), key)
                ),
                None,
            )
            values.append(
                value_out(
                    key=key,
                    model=_model_name(key),
                    payload=store.get_raw(digest),
                    artifact_hash=digest,
                    source="produced" if produced_by else "seed",
                    source_id=row.id,
                    produced_by=produced_by,
                )
            )

    return BenchRunOut(
        id=row.id,
        document_id=row.document_id,
        document_title=(document.title or document.filename) if document else None,
        nodes=[str(entry.get("node")) for entry in (row.nodes_json or [])],
        status=row.status,
        created_at=_iso(row.created_at) or "",
        started_at=_iso(row.started_at),
        finished_at=_iso(row.finished_at),
        error=row.error,
        total_cost_usd=row.total_cost_usd,
        verdict=row.verdict,
        force=bool(row.force),
        records=records,
        values=values,
        llm_traces=_traces(row) if detailed else [],
        pause=pause_out(row.manifest_json),
    )


def _traces(row: BenchRun) -> list[LLMTraceOut]:
    raw = (row.manifest_json or {}).get("llm_traces") or []
    out: list[LLMTraceOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(LLMTraceOut.model_validate(item))
        except Exception:  # noqa: BLE001 - a broken frame must not hide the run
            continue
    return out


def _node_version(name: str) -> str:
    try:
        return get_node(name).version
    except KeyError:
        return ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
