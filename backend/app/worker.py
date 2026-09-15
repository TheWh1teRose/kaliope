"""In-process background worker (§7.6).

A bounded ``ThreadPoolExecutor`` started in the FastAPI lifespan. Two kinds of
job run on it: parsing an uploaded document, and executing a flow over one.

A node failure fails the run, preserves the artifacts that were already
produced, and stores the traceback (AC-FW-5). Re-runs resume via the artifact
cache (AC-FW-1).
"""

from __future__ import annotations

import logging
import traceback as traceback_module
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.ingestion.pipeline import ParseOptions, parse
from app.ingestion.zones import ZoneCache, ZoneClassifier
from app.llm import registry as llm_registry
from app.llm.base import LLMClient, Usage
from app.models import (
    Artifact,
    BenchNode,
    BenchRun,
    Document,
    GateResult,
    LLMCall,
    Run,
    RunNode,
    Segment,
)
from app.pipeline import catalogue
from app.pipeline.bench import coerce_value, jsonable
from app.pipeline.feedback import resume_outputs, write_pause
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, FlowNode, flow_directory
from app.pipeline.framework.runner import FlowRunner, NodeRecord
from app.pipeline.gates import GateContext, run_gates
from app.pipeline.nodes.ingest import DocumentRef
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import AudienceSpec, FormatSpec, Script

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, max_workers: int | None = None) -> None:
        settings = get_settings()
        self.max_workers = max_workers or settings.max_concurrent_runs
        self._pool: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="kalliope"
            )
            logger.info("worker pool started with %s slot(s)", self.max_workers)

    def shutdown(self, wait: bool = True) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            self._pool = None

    def submit(self, fn: Any, *args: Any) -> Future[Any]:
        if self._pool is None:
            self.start()
        assert self._pool is not None
        return self._pool.submit(fn, *args)

    # -------------------------------------------------------------- parsing

    def submit_parse(self, document_id: str) -> Future[Any]:
        return self.submit(self.parse_document, document_id)

    def parse_document(self, document_id: str) -> None:
        settings = get_settings()
        store = ArtifactStore(settings.artifacts_dir)

        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:
                logger.warning("parse requested for unknown document %s", document_id)
                return
            document.parse_status = "parsing"
            document.parse_error = None
            path = settings.uploads_dir / f"{document.sha256}.pdf"
            parse_version = document.parse_version + 1
            language = document.language

        try:
            llm = self._build_llm(run_id=None)
            classifier = ZoneClassifier(
                client=llm if llm_available() else None,
                model=settings.zone_model or llm_registry.DEFAULT_SMALL_MODEL,
                cache=ZoneCache(settings.data_dir / "zone_cache"),
            )
            parsed = parse(
                path,
                ParseOptions(
                    document_id=document_id,
                    parse_version=parse_version,
                    language_override=language,
                ),
                classifier=classifier,
            )
            stored = store.put("parsed", parsed)

            with session_scope() as session:
                document = session.get(Document, document_id)
                if document is None:  # pragma: no cover - deleted mid-parse
                    return
                document.parse_status = "parsed"
                document.parse_version = parse_version
                document.page_count = parsed.page_count
                document.language = parsed.language
                document.title = parsed.title
                document.report_json = parsed.report.model_dump(mode="json")
                document.parsed_artifact_hash = stored.hash
                _record_artifact(session, stored.hash, "parsed", stored.size_bytes)
        except Exception as exc:  # noqa: BLE001 - recorded on the document
            logger.exception("parsing document %s failed", document_id)
            with session_scope() as session:
                document = session.get(Document, document_id)
                if document is not None:
                    document.parse_status = "failed"
                    document.parse_error = f"{type(exc).__name__}: {exc}\n\n" + (
                        traceback_module.format_exc()
                    )

    # ------------------------------------------------------------------ runs

    def submit_run(self, run_id: str) -> Future[Any]:
        return self.submit(self.execute_run, run_id)

    def submit_bench(self, bench_id: str) -> Future[Any]:
        return self.submit(self.execute_bench, bench_id)

    def execute_run(self, run_id: str) -> None:
        settings = get_settings()
        store = ArtifactStore(settings.artifacts_dir)
        bus.publish(run_id, "run.queued", {"run_id": run_id})

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                logger.warning("execution requested for unknown run %s", run_id)
                return
            document = session.get(Document, run.document_id)
            if document is None:
                run.status = "failed"
                run.error = "the document this run refers to no longer exists"
                return
            run.status = "running"
            if run.started_at is None:
                run.started_at = datetime.now(UTC)
            flow_id = run.flow_id
            config = dict(run.config_json or {})
            resume = resume_outputs(run.manifest_json)
            previous_manifest = dict(run.manifest_json or {})
            format_spec = FormatSpec.model_validate(run.format_spec_json)
            audience_spec = AudienceSpec.model_validate(run.audience_spec_json)
            document_ref = DocumentRef(
                document_id=document.id,
                parse_version=document.parse_version,
                sha256=document.sha256,
                parsed_artifact_hash=document.parsed_artifact_hash,
                language_override=config.get("language"),
            )
            pdf_path = settings.uploads_dir / f"{document.sha256}.pdf"

        bus.publish(run_id, "run.started", {"run_id": run_id, "flow": flow_id})

        flow = self._load_flow(flow_id)
        if flow is None:
            self._fail_run(run_id, f"flow '{flow_id}' is not available", None)
            return

        llm = self._build_llm(run_id=run_id)
        runner = FlowRunner(
            artifacts=store,
            llm=llm,
            cache_lookup=_cache_lookup,
            on_node=lambda record: _persist_node(run_id, record),
            progress=lambda event, data: bus.publish(run_id, event, data),
            document_path=pdf_path,
            force=bool(config.get("force")),
        )

        seeds: dict[str, Any] = {
            "document_ref": document_ref,
            "target_minutes": int(config.get("target_minutes", format_spec.target_minutes)),
            "format_spec": format_spec,
            "audience_spec": audience_spec,
        }

        result = runner.execute(
            flow,
            run_id,
            seeds,
            document_id=document_ref.document_id,
            config_overrides=config.get("node_config") or {},
            resume_outputs=resume,
        )

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:  # pragma: no cover - deleted mid-run
                return
            run.manifest_json = result.manifest.model_dump(mode="json")
            run.total_cost_usd = result.manifest.total_cost_usd
            for record in result.manifest.nodes:
                if record.artifact_hash:
                    _record_artifact(session, record.artifact_hash, record.name, 0)

        if result.status == "failed":
            self._fail_run(
                run_id, result.error or "the run failed", result.traceback, result.verdict
            )
            return

        if result.status == "paused":
            self._pause_run(run_id, result, previous=previous_manifest)
            return

        try:
            self._finish_run(run_id, result.bag, flow, store)
        except Exception as exc:  # noqa: BLE001
            logger.exception("post-processing run %s failed", run_id)
            self._fail_run(run_id, f"{type(exc).__name__}: {exc}", traceback_module.format_exc())

    def execute_bench(self, bench_id: str) -> None:
        """Run a bench chain. No gates, no segments, no review rows."""
        settings = get_settings()
        store = ArtifactStore(settings.artifacts_dir)
        bus.publish(bench_id, "run.queued", {"run_id": bench_id})

        with session_scope() as session:
            row = session.get(BenchRun, bench_id)
            if row is None:
                logger.warning("bench execution requested for unknown id %s", bench_id)
                return
            row.status = "running"
            if row.started_at is None:
                row.started_at = datetime.now(UTC)
            document = session.get(Document, row.document_id) if row.document_id else None
            pdf_path = (
                settings.uploads_dir / f"{document.sha256}.pdf" if document is not None else None
            )
            document_id = document.id if document else None
            force = bool(row.force)
            entries = [FlowNode.model_validate(entry) for entry in (row.nodes_json or [])]
            seed_meta = dict(row.seeds_json or {})
            resume = resume_outputs(row.manifest_json)
            previous_manifest = dict(row.manifest_json or {})

        bus.publish(bench_id, "run.started", {"run_id": bench_id, "flow": "bench"})

        seeds: dict[str, Any] = {}
        for key, meta in seed_meta.items():
            digest = (meta or {}).get("artifact_hash") if isinstance(meta, dict) else None
            if not digest or not store.exists(digest):
                self._fail_bench(bench_id, f"seed '{key}' is missing from the artifact store", None)
                return
            try:
                seeds[key] = coerce_value(key, store.get_raw(digest))
            except Exception as exc:  # noqa: BLE001
                self._fail_bench(
                    bench_id, f"seed '{key}' is invalid: {exc}", traceback_module.format_exc()
                )
                return

        flow = Flow(id="bench", version="0", description="bench", nodes=entries)
        llm = self._build_llm(run_id=None)
        runner = FlowRunner(
            artifacts=store,
            llm=llm,
            cache_lookup=_cache_lookup,
            on_node=lambda record: _persist_bench_node(bench_id, record),
            progress=lambda event, data: bus.publish(bench_id, event, data),
            document_path=pdf_path,
            force=force,
        )
        result = runner.execute(
            flow, bench_id, seeds, document_id=document_id, resume_outputs=resume
        )

        bag_hashes: dict[str, str] = {}
        for key, value in result.bag.items():
            digest = result.artifact_hashes.get(key)
            if isinstance(digest, str) and store.exists(digest):
                bag_hashes[key] = digest
                continue
            stored = store.put_raw(f"bench:{key}", jsonable(value))
            bag_hashes[key] = stored.hash

        with session_scope() as session:
            row = session.get(BenchRun, bench_id)
            if row is None:
                return
            manifest = result.manifest.model_dump(mode="json")
            manifest["llm_traces"] = list(llm.traces)
            row.manifest_json = manifest
            row.total_cost_usd = result.manifest.total_cost_usd
            row.bag_hashes_json = bag_hashes
            row.verdict = result.verdict
            for record in result.manifest.nodes:
                if record.artifact_hash:
                    _record_artifact(session, record.artifact_hash, record.name, 0)

        if result.status == "failed":
            self._fail_bench(
                bench_id, result.error or "the bench run failed", result.traceback, result.verdict
            )
            return

        if result.status == "paused":
            self._pause_bench(bench_id, result, previous=previous_manifest)
            return

        with session_scope() as session:
            row = session.get(BenchRun, bench_id)
            if row is None:
                return
            row.status = "completed"
            row.finished_at = datetime.now(UTC)
        bus.publish(bench_id, "run.completed", {"nodes": [n.name for n in result.manifest.nodes]})

    # -------------------------------------------------------------- helpers

    def _finish_run(
        self, run_id: str, bag: dict[str, Any], flow: Flow, store: ArtifactStore
    ) -> None:
        parsed: ParsedDocument = bag["parsed"]
        script: Script = bag["script"]
        gate_context = GateContext(
            parsed=parsed,
            script=script,
            outline=bag["outline"],
            selection=bag["selection"],
            budget=bag["budget"],
            format_spec=bag["format_spec"],
        )
        bus.publish(run_id, "gates.started", {"gates": flow.gates})
        suite = run_gates(gate_context, flow.gates or None)
        stored = store.put_raw("gate_reports", suite.model_dump(mode="json"))

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:  # pragma: no cover
                return
            for report in suite.reports:
                session.add(
                    GateResult(
                        run_id=run_id,
                        gate_id=report.id,
                        status=report.status,
                        violations_json=[v.model_dump(mode="json") for v in report.violations],
                        skip_reason=report.skip_reason,
                        measurements_json=report.measurements,
                    )
                )
            for ordinal, segment in enumerate(script.segments):
                session.add(
                    Segment(
                        id=f"{run_id}:{segment.id}",
                        run_id=run_id,
                        ordinal=ordinal,
                        speaker=segment.speaker,
                        text=segment.text,
                        kind=segment.kind,
                        anchors_json=[a.model_dump(mode="json") for a in segment.anchors],
                        beat_id=segment.beat_id,
                    )
                )
            _record_artifact(session, stored.hash, "gate_reports", stored.size_bytes)
            run.status = "completed"
            run.finished_at = datetime.now(UTC)

        bus.publish(
            run_id,
            "run.completed",
            {
                "failed_gates": suite.failed,
                "warned_gates": suite.warned,
                "segments": len(script.segments),
            },
        )

    def _pause_run(
        self, run_id: str, result: Any, *, previous: dict[str, Any] | None = None
    ) -> None:
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                return
            manifest = write_pause(
                result.manifest.model_dump(mode="json"),
                node=result.paused_at or "",
                payload=result.pause or {},
                bag_hashes=result.artifact_hashes,
                previous=previous or run.manifest_json,
            )
            run.manifest_json = manifest
            run.total_cost_usd = result.manifest.total_cost_usd
            run.status = "paused"
            run.finished_at = None
            for record in result.manifest.nodes:
                if record.artifact_hash:
                    _record_artifact(session, record.artifact_hash, record.name, 0)
        bus.publish(run_id, "run.paused", {"node": result.paused_at})

    def _pause_bench(
        self, bench_id: str, result: Any, *, previous: dict[str, Any] | None = None
    ) -> None:
        with session_scope() as session:
            row = session.get(BenchRun, bench_id)
            if row is None:
                return
            traces = list((row.manifest_json or {}).get("llm_traces") or [])
            hashes = dict(row.bag_hashes_json or {})
            manifest = write_pause(
                result.manifest.model_dump(mode="json"),
                node=result.paused_at or "",
                payload=result.pause or {},
                bag_hashes=hashes,
                previous=previous or row.manifest_json,
            )
            manifest["llm_traces"] = traces
            row.manifest_json = manifest
            row.total_cost_usd = result.manifest.total_cost_usd
            row.status = "paused"
            row.finished_at = None
            for record in result.manifest.nodes:
                if record.artifact_hash:
                    _record_artifact(session, record.artifact_hash, record.name, 0)
        bus.publish(bench_id, "run.paused", {"node": result.paused_at})

    def _fail_run(
        self, run_id: str, error: str, traceback: str | None, verdict: str | None = None
    ) -> None:
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:  # pragma: no cover
                return
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = error if not traceback else f"{error}\n\n{traceback}"
            if verdict:
                config = dict(run.config_json or {})
                config["verdict"] = verdict
                run.config_json = config
        bus.publish(run_id, "run.failed", {"error": error, "verdict": verdict})

    def _fail_bench(
        self, bench_id: str, error: str, traceback: str | None, verdict: str | None = None
    ) -> None:
        with session_scope() as session:
            row = session.get(BenchRun, bench_id)
            if row is None:
                return
            row.status = "failed"
            row.finished_at = datetime.now(UTC)
            row.error = error if not traceback else f"{error}\n\n{traceback}"
            if verdict:
                row.verdict = verdict
        bus.publish(bench_id, "run.failed", {"error": error, "verdict": verdict})

    def _build_llm(self, run_id: str | None) -> LLMClient:
        def record(
            *,
            model_id: str,
            usage: Usage,
            cost_usd: float,
            latency_ms: int,
            node_name: str | None,
        ) -> None:
            with session_scope() as session:
                session.add(
                    LLMCall(
                        run_id=run_id,
                        node_name=node_name,
                        model_id=model_id,
                        tokens_in=usage.input_tokens + usage.cache_read_tokens,
                        tokens_out=usage.output_tokens,
                        cost_usd=cost_usd,
                        latency_ms=latency_ms,
                    )
                )

        return LLMClient(
            resolve=llm_registry.resolve_provider,
            cost_of=llm_registry.cost_usd,
            recorder=record,
        )

    def _load_flow(self, flow_id: str) -> Flow | None:
        with session_scope() as session:
            return catalogue.flows(session).get(flow_id)


def llm_available() -> bool:
    return get_settings().has_any_llm_key()


def _flow_directory() -> Path:
    """Where the shipped flow files live. Kept here for the CLI and the tests."""
    return flow_directory()


def _cache_lookup(cache_key: str) -> str | None:
    with session_scope() as session:
        row = session.scalars(
            sa_select(RunNode)
            .where(RunNode.cache_key == cache_key, RunNode.artifact_hash.is_not(None))
            .order_by(RunNode.finished_at.desc())
            .limit(1)
        ).first()
        if row:
            return row.artifact_hash
        bench = session.scalars(
            sa_select(BenchNode)
            .where(BenchNode.cache_key == cache_key, BenchNode.artifact_hash.is_not(None))
            .order_by(BenchNode.finished_at.desc())
            .limit(1)
        ).first()
        return bench.artifact_hash if bench else None


def _persist_node(run_id: str, record: NodeRecord) -> None:
    with session_scope() as session:
        existing = session.scalars(
            sa_select(RunNode).where(RunNode.run_id == run_id, RunNode.node_name == record.name)
        ).first()
        row = existing or RunNode(run_id=run_id, node_name=record.name)
        row.node_version = record.version
        row.cache_key = record.cache_key
        row.cache_hit = record.cache_hit
        row.artifact_hash = record.artifact_hash
        row.started_at = _parse_ts(record.started_at)
        row.finished_at = _parse_ts(record.finished_at)
        row.cost_usd = record.cost_usd
        row.tokens_in = record.tokens_in
        row.tokens_out = record.tokens_out
        row.model_id = record.model_id
        row.error = record.error
        if existing is None:
            session.add(row)


def _persist_bench_node(bench_id: str, record: NodeRecord) -> None:
    with session_scope() as session:
        existing = session.scalars(
            sa_select(BenchNode).where(
                BenchNode.bench_run_id == bench_id, BenchNode.node_name == record.name
            )
        ).first()
        row = existing or BenchNode(bench_run_id=bench_id, node_name=record.name)
        row.node_version = record.version
        row.cache_key = record.cache_key
        row.cache_hit = record.cache_hit
        row.artifact_hash = record.artifact_hash
        row.started_at = _parse_ts(record.started_at)
        row.finished_at = _parse_ts(record.finished_at)
        row.cost_usd = record.cost_usd
        row.tokens_in = record.tokens_in
        row.tokens_out = record.tokens_out
        row.model_id = record.model_id
        row.error = record.error
        if existing is None:
            session.add(row)


def _record_artifact(session: Session, digest: str, kind: str, size_bytes: int) -> None:
    if session.get(Artifact, digest) is not None:
        return
    session.add(Artifact(hash=digest, kind=kind, size_bytes=size_bytes))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover
        return None


worker = Worker()
