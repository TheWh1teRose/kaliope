"""The node testing bench: value catalogue, seed resolution, bag loading.

A bench execution is a flow assembled for one experiment. It uses the same
runner and the same artifact store as a production run; it does not have to
publish a script, and it never writes review rows.
"""

from __future__ import annotations

import json
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.introspection import is_model, type_name
from app.models import BenchNode, BenchRun, Document, Run, RunNode
from app.pipeline.formats import DEFAULT_AUDIENCE, DEFAULT_FORMAT_ID, FORMATS
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import input_keys
from app.pipeline.framework.registry import get_node, node_names
from app.pipeline.framework.spec import coerce, node_params, prune_defaults
from app.pipeline.nodes.ingest import DocumentRef
from app.pipeline.validation import RUN_SEED_KEYS, validate_flow
from app.schemas.api import NodeRunStatus
from app.schemas.authoring import FlowNodeIn
from app.schemas.bench import BenchValueOut, SeedIn, ValueKeyOut
from app.schemas.pipeline import FormatSpec

#: Longest JSON preview a bag value carries. The full payload stays behind
#: its artifact hash.
_PREVIEW_CHARS = 40_000
_SAMPLE_CHARS = 240


def validate_bench(nodes: list[dict[str, Any]] | list[Any], seed_keys: list[str]) -> Any:
    """Wiring check for a bench chain: seeds in the bag count, a script does not."""
    return validate_flow(nodes, gates=[], as_pipeline=False, extra_seeds=seed_keys)


def annotation_for_key(key: str) -> Any:
    """The type of a bag key, from the node that publishes it or first consumes it."""
    for name in node_names():
        node = get_node(name)
        if node.produces == key:
            return node.Output
    for name in node_names():
        info = get_node(name).Input.model_fields.get(key)
        if info is not None:
            return info.annotation
    return None


def unwrap(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is type(None):
        return annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if args:
        return args[0]
    return annotation


def json_schema_for(annotation: Any) -> dict[str, Any]:
    inner = unwrap(annotation)
    if is_model(inner):
        return inner.model_json_schema()
    name = type_name(inner)
    mapping = {"int": "integer", "float": "number", "str": "string", "bool": "boolean"}
    return {"type": mapping.get(name, name)}


def empty_template(annotation: Any) -> Any:
    inner = unwrap(annotation)
    if inner is int:
        return 15
    if inner is float:
        return 0.0
    if inner is bool:
        return False
    if inner is str:
        return ""
    if is_model(inner):
        data: dict[str, Any] = {}
        for name, info in inner.model_fields.items():
            if not info.is_required():
                data[name] = info.get_default(call_default_factory=True)
            else:
                data[name] = None
        return data
    return None


def value_catalogue() -> list[ValueKeyOut]:
    produced: dict[str, list[str]] = {}
    consumed: dict[str, list[str]] = {}
    for name in node_names():
        node = get_node(name)
        produced.setdefault(node.produces, []).append(name)
        for key in input_keys(node):
            consumed.setdefault(key, []).append(name)

    keys = sorted(set(produced) | set(consumed) | set(RUN_SEED_KEYS))
    out: list[ValueKeyOut] = []
    for key in keys:
        annotation = annotation_for_key(key)
        template: Any = None
        if key in RUN_SEED_KEYS:
            if key == "audience_spec":
                template = DEFAULT_AUDIENCE.model_dump(mode="json")
            elif key == "format_spec":
                template = FORMATS[DEFAULT_FORMAT_ID].model_dump(mode="json")
            elif key == "target_minutes":
                template = FORMATS[DEFAULT_FORMAT_ID].target_minutes
            else:
                template = empty_template(annotation) if annotation is not None else None
        out.append(
            ValueKeyOut(
                key=key,
                model=type_name(unwrap(annotation)) if annotation is not None else "unknown",
                produced_by=produced.get(key, []),
                consumed_by=consumed.get(key, []),
                json_schema=json_schema_for(annotation) if annotation is not None else {},
                template=template,
                seed=key in RUN_SEED_KEYS,
            )
        )
    return out


def resolve_config(node_name: str, config: dict[str, Any]) -> dict[str, Any]:
    node = get_node(node_name)
    params = {param.key: param for param in node_params(node)}
    resolved: dict[str, Any] = {}
    for key, value in config.items():
        if key in params:
            coerced = coerce(params[key], value)
            if coerced is not None:
                resolved[key] = coerced
        else:
            resolved[key] = value
    return prune_defaults(node, resolved)


def coerce_value(key: str, payload: Any) -> Any:
    annotation = unwrap(annotation_for_key(key))
    if annotation is None:
        return payload
    if is_model(annotation):
        return annotation.model_validate(payload)
    if annotation is int:
        return int(payload)
    if annotation is float:
        return float(payload)
    if annotation is bool:
        return bool(payload)
    if annotation is str:
        return str(payload)
    return payload


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def resolve_seeds(
    store: ArtifactStore, seeds: dict[str, SeedIn]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Turn posted seeds into typed values and store each as an artifact.

    Returns ``(bag, hashes)``. A seed that names a missing artifact or fails
    its model raises ``ValueError``.
    """
    bag: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for key, seed in seeds.items():
        if seed.payload is not None:
            payload = seed.payload
        else:
            assert seed.artifact_hash is not None
            if not store.exists(seed.artifact_hash):
                raise ValueError(
                    f"artifact '{seed.artifact_hash[:12]}…' for '{key}' is not in the store"
                )
            payload = store.get_raw(seed.artifact_hash)
        try:
            value = coerce_value(key, payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ValueError(
                f"seed '{key}' is not a valid {type_name(annotation_for_key(key))}: {exc}"
            ) from exc
        stored = store.put_raw(f"bench:{key}", jsonable(value))
        bag[key] = value
        hashes[key] = stored.hash
    return bag, hashes


def document_ref_for(document: Document, language: str | None = None) -> DocumentRef:
    return DocumentRef(
        document_id=document.id,
        parse_version=document.parse_version,
        sha256=document.sha256,
        parsed_artifact_hash=document.parsed_artifact_hash,
        language_override=language,
    )


def load_values(
    session: Session,
    store: ArtifactStore,
    *,
    document_id: str | None = None,
    run_id: str | None = None,
    bench_run_id: str | None = None,
    format_id: str | None = None,
    keys: list[str] | None = None,
    include_payload: bool = False,
) -> tuple[list[BenchValueOut], list[FlowNodeIn] | None, str | None]:
    """Collect bag values from a document, a production run, a bench run, and/or a format."""
    wanted = set(keys) if keys else None
    collected: dict[str, BenchValueOut] = {}
    nodes: list[FlowNodeIn] | None = None
    resolved_document: str | None = document_id

    if format_id or (document_id and not run_id and not bench_run_id):
        spec = _format_spec(session, format_id or DEFAULT_FORMAT_ID)
        _put(
            collected,
            store,
            key="format_spec",
            payload=spec.model_dump(mode="json"),
            model="FormatSpec",
            source="format",
            source_id=spec.id,
            include_payload=include_payload,
        )
        _put(
            collected,
            store,
            key="audience_spec",
            payload=DEFAULT_AUDIENCE.model_dump(mode="json"),
            model="AudienceSpec",
            source="format",
            source_id=spec.id,
            include_payload=include_payload,
        )
        _put(
            collected,
            store,
            key="target_minutes",
            payload=spec.target_minutes,
            model="int",
            source="format",
            source_id=spec.id,
            include_payload=True,
        )

    if document_id:
        document = session.get(Document, document_id)
        if document is None:
            raise KeyError(f"document '{document_id}' does not exist")
        _put(
            collected,
            store,
            key="document_ref",
            payload=document_ref_for(document).model_dump(mode="json"),
            model="DocumentRef",
            source="document",
            source_id=document.id,
            include_payload=True,
        )
        if document.parsed_artifact_hash and store.exists(document.parsed_artifact_hash):
            payload = store.get_raw(document.parsed_artifact_hash)
            _put(
                collected,
                store,
                key="parsed",
                payload=payload,
                model="ParsedDocument",
                source="document",
                source_id=document.id,
                include_payload=include_payload,
                artifact_hash=document.parsed_artifact_hash,
            )

    if run_id:
        run = session.get(Run, run_id)
        if run is None:
            raise KeyError(f"run '{run_id}' does not exist")
        resolved_document = resolved_document or run.document_id
        document = session.get(Document, run.document_id)
        config = run.config_json or {}
        if document is not None:
            _put(
                collected,
                store,
                key="document_ref",
                payload=document_ref_for(document, config.get("language")).model_dump(mode="json"),
                model="DocumentRef",
                source="run",
                source_id=run.id,
                include_payload=True,
            )
        _put(
            collected,
            store,
            key="target_minutes",
            payload=config.get("target_minutes"),
            model="int",
            source="run",
            source_id=run.id,
            include_payload=True,
        )
        _put(
            collected,
            store,
            key="format_spec",
            payload=run.format_spec_json,
            model="FormatSpec",
            source="run",
            source_id=run.id,
            include_payload=include_payload,
        )
        _put(
            collected,
            store,
            key="audience_spec",
            payload=run.audience_spec_json,
            model="AudienceSpec",
            source="run",
            source_id=run.id,
            include_payload=include_payload,
        )
        rows = session.scalars(select(RunNode).where(RunNode.run_id == run.id)).all()
        for row in rows:
            if not row.artifact_hash or not store.exists(row.artifact_hash):
                continue
            node = get_node(row.node_name)
            _put(
                collected,
                store,
                key=node.produces,
                payload=store.get_raw(row.artifact_hash),
                model=node.Output.__name__,
                source="run",
                source_id=run.id,
                include_payload=include_payload,
                artifact_hash=row.artifact_hash,
                produced_by=row.node_name,
            )

    if bench_run_id:
        bench = session.get(BenchRun, bench_run_id)
        if bench is None:
            raise KeyError(f"bench run '{bench_run_id}' does not exist")
        resolved_document = resolved_document or bench.document_id
        nodes = [FlowNodeIn.model_validate(entry) for entry in (bench.nodes_json or [])]
        for key, digest in (bench.bag_hashes_json or {}).items():
            if not isinstance(digest, str) or not store.exists(digest):
                continue
            annotation = annotation_for_key(key)
            produced_by = next(
                (
                    entry.get("node")
                    for entry in (bench.nodes_json or [])
                    if _produces(entry.get("node"), key)
                ),
                None,
            )
            _put(
                collected,
                store,
                key=key,
                payload=store.get_raw(digest),
                model=type_name(unwrap(annotation)) if annotation is not None else "unknown",
                source="bench",
                source_id=bench.id,
                include_payload=include_payload,
                artifact_hash=digest,
                produced_by=produced_by,
            )
        for key, meta in (bench.seeds_json or {}).items():
            if key in collected:
                continue
            digest = (meta or {}).get("artifact_hash") if isinstance(meta, dict) else None
            if not digest or not store.exists(digest):
                continue
            annotation = annotation_for_key(key)
            _put(
                collected,
                store,
                key=key,
                payload=store.get_raw(digest),
                model=type_name(unwrap(annotation)) if annotation is not None else "unknown",
                source="bench",
                source_id=bench.id,
                include_payload=include_payload,
                artifact_hash=digest,
            )

    values = list(collected.values())
    if wanted is not None:
        values = [value for value in values if value.key in wanted]
    values.sort(key=lambda value: value.key)
    return values, nodes, resolved_document


def value_out(
    *,
    key: str,
    model: str,
    payload: Any,
    artifact_hash: str | None,
    source: str,
    source_id: str | None = None,
    produced_by: str | None = None,
    include_payload: bool = False,
) -> BenchValueOut:
    if payload is None:
        return BenchValueOut(
            key=key,
            model=model,
            produced_by=produced_by,
            artifact_hash=artifact_hash,
            available=False,
            source=source,
            source_id=source_id,
        )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    truncated = len(text) > _PREVIEW_CHARS
    return BenchValueOut(
        key=key,
        model=model,
        produced_by=produced_by,
        artifact_hash=artifact_hash,
        summary=_summarise(payload),
        preview=text[:_PREVIEW_CHARS],
        truncated=truncated,
        available=True,
        source=source,
        source_id=source_id,
        payload=payload if include_payload or not truncated else None,
    )


def node_status(row: BenchNode | None, *, reached: bool, run_status: str) -> NodeRunStatus:
    if row is None:
        if reached or run_status == "failed":
            return "blocked"
        return "running" if run_status == "running" else "pending"
    if row.error:
        return "failed"
    if row.finished_at is None:
        return "running"
    return "cached" if row.cache_hit else "ok"


def _put(
    collected: dict[str, BenchValueOut],
    store: ArtifactStore,
    *,
    key: str,
    payload: Any,
    model: str,
    source: str,
    source_id: str | None,
    include_payload: bool,
    artifact_hash: str | None = None,
    produced_by: str | None = None,
) -> None:
    if payload is None:
        return
    digest = artifact_hash
    if digest is None:
        stored = store.put_raw(f"bench:{key}", jsonable(payload))
        digest = stored.hash
    collected[key] = value_out(
        key=key,
        model=model,
        payload=payload,
        artifact_hash=digest,
        source=source,
        source_id=source_id,
        produced_by=produced_by,
        include_payload=include_payload,
    )


def _format_spec(session: Session, format_id: str) -> FormatSpec:
    from app.pipeline import catalogue

    return catalogue.get_format_spec(session, format_id)


def _produces(node_name: str | None, key: str) -> bool:
    if not node_name:
        return False
    try:
        return get_node(node_name).produces == key
    except KeyError:
        return False


def _summarise(payload: Any) -> dict[str, Any]:
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
