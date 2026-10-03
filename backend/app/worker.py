"""In-process background worker (§7.6).

A bounded ``ThreadPoolExecutor`` started in the FastAPI lifespan. Two kinds of
job run on it: parsing an uploaded document, and executing a flow over one.

A node failure fails the run, preserves the artifacts that were already
produced, and stores the traceback (AC-FW-5). Re-runs resume via the artifact
cache (AC-FW-1).
"""

from __future__ import annotations

import logging
import threading
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
    ExperimentRun,
    GateResult,
    LLMCall,
    Run,
    RunNode,
    Segment,
    Series,
)
from app.pipeline import catalogue
from app.pipeline.bench import coerce_value, jsonable
from app.pipeline.feedback import last_row_for_key, resume_outputs, write_pause
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, FlowNode, flow_directory
from app.pipeline.framework.runner import FlowRunner, NodeRecord
from app.pipeline.gates import GateContext, run_gates
from app.pipeline.nodes.ingest import DocumentRef
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    EpisodeBrief,
    FormatSpec,
    Script,
    SeriesContext,
    SeriesRequest,
)
from app.series import (
    SCRIPT_DONE,
    brief_for,
    context_hash,
    coverage_check,
    episode_runs,
    final_script,
    load_plan,
    plan_run,
    series_channel,
    series_context_for,
    truncate_flow,
)

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, max_workers: int | None = None) -> None:
        settings = get_settings()
        self.max_workers = max_workers or settings.max_concurrent_runs
        self._pool: ThreadPoolExecutor | None = None
        #: ``run_id`` → ``(series_id, episode_index)`` for runs of a series, so
        #: their events also reach the series' channel.
        self._series_tags: dict[str, tuple[str, int]] = {}
        #: Series with a job running in this process.
        self._active_series: set[str] = set()
        self._lock = threading.Lock()

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
                if not document.title_edited:
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

    def submit_experiment(self, run_id: str) -> Future[Any]:
        return self.submit(self.execute_experiment, run_id)

    def execute_experiment(self, run_id: str) -> None:
        """Run one experiment click. No gates, no artifacts, no review rows."""
        from app.experiments.base import ExperimentContext, SourceIn
        from app.experiments.registry import get_experiment

        with session_scope() as session:
            row = session.get(ExperimentRun, run_id)
            if row is None:
                logger.warning("experiment run requested for unknown id %s", run_id)
                return
            row.status = "running"
            row.started_at = datetime.now(UTC)
            key = row.experiment_key
            setup_raw = dict(row.setup_json or {})
            source_raw = row.source_json

        llm = self._build_llm(run_id=None)
        try:
            experiment = get_experiment(key)
            llm.node_name = f"experiment:{key}"
            setup = experiment.Setup.model_validate(setup_raw)
            source = None
            if isinstance(source_raw, dict) and source_raw.get("run_id"):
                source = SourceIn.model_validate(source_raw)
            ctx = ExperimentContext(
                source=source,
                store=ArtifactStore(get_settings().artifacts_dir),
                session_scope=session_scope,
            )
            result = experiment.run(setup, llm, ctx)
        except Exception as exc:  # noqa: BLE001 - recorded on the run, shown on the page
            logger.warning("experiment run %s failed: %s", run_id, exc)
            with session_scope() as session:
                row = session.get(ExperimentRun, run_id)
                if row is not None:
                    row.status = "failed"
                    row.error = str(exc)
                    row.finished_at = datetime.now(UTC)
                    row.manifest_json = {"llm_traces": list(llm.traces)}
                    row.total_cost_usd = llm.total_cost_usd
            return

        with session_scope() as session:
            row = session.get(ExperimentRun, run_id)
            if row is None:
                return
            row.status = "completed"
            row.output_json = result.output_json
            row.warnings_json = list(result.warnings)
            row.manifest_json = {"llm_traces": list(llm.traces), "text": result.text}
            row.total_cost_usd = llm.total_cost_usd
            row.tokens_in = llm.total_usage.input_tokens + llm.total_usage.cache_read_tokens
            row.tokens_out = llm.total_usage.output_tokens
            row.finished_at = datetime.now(UTC)

    def execute_run(self, run_id: str) -> None:
        settings = get_settings()
        store = ArtifactStore(settings.artifacts_dir)
        with session_scope() as session:
            tagged = session.get(Run, run_id)
            if tagged is not None and tagged.series_id and tagged.episode_index is not None:
                with self._lock:
                    self._series_tags[run_id] = (tagged.series_id, tagged.episode_index)
        # A run executes again after a pause; the previous execution's events,
        # its terminal one above all, must not replay into this one's stream.
        bus.clear(run_id)
        self._emit(run_id, "run.queued", {"run_id": run_id})

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

        self._emit(run_id, "run.started", {"run_id": run_id, "flow": flow_id})

        flow = self._load_flow(flow_id)
        if flow is None:
            self._fail_run(run_id, f"flow '{flow_id}' is not available", None)
            return
        stop_after = config.get("stop_after")
        if stop_after:
            # The first stage of a series episode: up to its outline, no script yet.
            flow = truncate_flow(flow, str(stop_after))

        llm = self._build_llm(run_id=run_id)
        runner = FlowRunner(
            artifacts=store,
            llm=llm,
            cache_lookup=_cache_lookup,
            on_node=lambda record: _persist_node(run_id, record),
            progress=lambda event, data: self._emit(run_id, event, data),
            document_path=pdf_path,
            force=bool(config.get("force")),
        )

        seeds: dict[str, Any] = {
            "document_ref": document_ref,
            "target_minutes": int(config.get("target_minutes", format_spec.target_minutes)),
            "format_spec": format_spec,
            "audience_spec": audience_spec,
            **_series_seeds(config, store),
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
            run.total_cost_usd = _run_cost(session, run_id, result.manifest.total_cost_usd)
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

        if stop_after:
            self._stop_run(run_id, "outlined", "run.outlined")
            return
        if "script" not in result.bag:
            # A planner run: its output is the plan, there is no script to check.
            self._stop_run(run_id, "completed", "run.completed")
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
            episode_brief=bag.get("episode_brief"),
        )
        self._emit(run_id, "gates.started", {"gates": flow.gates})
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

        self._emit(
            run_id,
            "run.completed",
            {
                "failed_gates": suite.failed,
                "warned_gates": suite.warned,
                "segments": len(script.segments),
            },
        )

    def _stop_run(self, run_id: str, status: str, event: str) -> None:
        """End an execution that has no script to check: a planner run or an outline stage."""
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:  # pragma: no cover
                return
            run.status = status
            run.error = None
            run.finished_at = datetime.now(UTC) if status == "completed" else None
        self._emit(run_id, event, {"run_id": run_id})

    def _emit(self, run_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        """Publish a run's event; a series run's also goes to its series' channel."""
        payload = dict(data or {})
        bus.publish(run_id, event, payload)
        with self._lock:
            tag = self._series_tags.get(run_id)
        if tag is not None:
            series_id, episode = tag
            bus.publish(
                series_channel(series_id), event, {**payload, "run_id": run_id, "episode": episode}
            )

    # ---------------------------------------------------------------- series

    def submit_series(self, series_id: str) -> Future[Any]:
        return self.submit(self.execute_series, series_id)

    def series_active(self, series_id: str) -> bool:
        with self._lock:
            return series_id in self._active_series

    def execute_series(self, series_id: str) -> None:
        """Drive a series as far as it can go: plan, outlines, then scripts in order.

        Each stage skips what is already done, so the same call resumes a series
        after an approval, a failure or a restart of the process.
        """
        with self._lock:
            if series_id in self._active_series:
                return
            self._active_series.add(series_id)
        try:
            self._drive_series(series_id)
        except Exception as exc:  # noqa: BLE001 - recorded on the series
            logger.exception("series %s failed", series_id)
            self._fail_series(series_id, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._active_series.discard(series_id)

    def _drive_series(self, series_id: str) -> None:
        store = ArtifactStore(get_settings().artifacts_dir)
        channel = series_channel(series_id)
        bus.clear(channel)

        # -- 1. plan
        with session_scope() as session:
            series = session.get(Series, series_id)
            if series is None:
                logger.warning("series %s does not exist", series_id)
                return
            planner = plan_run(session, series_id)
            if planner is None:
                raise RuntimeError("the series has no planner run")
            planner_id = planner.id
            planned = planner.status == "completed" and series.plan_artifact_hash is not None
            request = dict(series.request_json or {})
            if not planned:
                series.status = "planning"
                if series.started_at is None:
                    series.started_at = datetime.now(UTC)
            series.error = None
        if not planned:
            bus.publish(channel, "series.planning", {"run_id": planner_id})
            self.execute_run(planner_id)
            with session_scope() as session:
                series = session.get(Series, series_id)
                planner = session.get(Run, planner_id)
                if series is None or planner is None:  # pragma: no cover
                    return
                if planner.status != "completed":
                    error = (planner.error or "the planner failed").split("\n\n")[0]
                    self._fail_series(series_id, error)
                    return
                flow = catalogue.flows(session).get(planner.flow_id)
                row = (
                    last_row_for_key(session, run_id=planner_id, flow=flow, key="series_plan")
                    if flow is not None
                    else None
                )
                if row is None or not row.artifact_hash:
                    self._fail_series(series_id, "the planner run produced no series plan")
                    return
                series.plan_artifact_hash = row.artifact_hash
                if request.get("review_plan", True) and not request.get("approved"):
                    series.status = "planned"
                    held = True
                else:
                    held = False
            if held:
                bus.publish(channel, "series.planned", {"run_id": planner_id})
                return

        # -- 2. every episode up to its outline
        with session_scope() as session:
            series = session.get(Series, series_id)
            assert series is not None
            plan = load_plan(store, series)
            if plan is None:
                raise RuntimeError("the series plan artifact is missing")
            series.status = "outlining"
            todo: list[str] = []
            runs = episode_runs(session, series_id)
            for episode in plan.episodes:
                run = runs.get(episode.index)
                if run is None:
                    brief = store.put("episode_brief", brief_for(plan, episode.index))
                    run = Run(
                        document_id=series.document_id,
                        flow_id=series.flow_id,
                        flow_version=series.flow_version,
                        config_json={
                            "target_minutes": int(request.get("minutes_per_episode") or 15),
                            "language": request.get("language"),
                            "force": bool(request.get("force")),
                            "episode_brief_artifact": brief.hash,
                            "stop_after": "outline",
                        },
                        format_spec_json=series.format_spec_json,
                        audience_spec_json=series.audience_spec_json,
                        status="queued",
                        created_by=series.created_by,
                        series_id=series_id,
                        episode_index=episode.index,
                    )
                    session.add(run)
                    session.flush()
                    _record_artifact(session, brief.hash, "episode_brief", brief.size_bytes)
                if run.status in SCRIPT_DONE or run.status == "outlined":
                    continue
                flow = catalogue.flows(session).get(run.flow_id)
                if flow is not None and last_row_for_key(
                    session, run_id=run.id, flow=flow, key="outline"
                ):
                    continue  # outlined before; it failed later, in its script
                config = dict(run.config_json or {})
                config["stop_after"] = "outline"
                config.pop("series_context_artifact", None)
                run.config_json = config
                todo.append(run.id)
        bus.publish(channel, "series.outlining", {"episodes": len(plan.episodes)})
        for run_id in todo:
            self.execute_run(run_id)
            if not self._episode_reached(series_id, run_id, {"outlined", *SCRIPT_DONE}):
                return

        # -- 3. scripts, one after another
        with session_scope() as session:
            series = session.get(Series, series_id)
            assert series is not None
            series.status = "writing"
        bus.publish(channel, "series.writing", {"episodes": len(plan.episodes)})
        for episode in plan.episodes:
            with session_scope() as session:
                run = episode_runs(session, series_id)[episode.index]
                if run.status in SCRIPT_DONE:
                    continue
                context = series_context_for(session, store, series_id, plan, episode.index)
                stored = store.put("series_context", context)
                _record_artifact(session, stored.hash, "series_context", stored.size_bytes)
                config = dict(run.config_json or {})
                config.pop("stop_after", None)
                config["series_context_artifact"] = stored.hash
                run.config_json = config
                run.context_hash = context_hash(context)
                run.status = "queued"
                run_id = run.id
            bus.publish(channel, "episode.writing", {"episode": episode.index, "run_id": run_id})
            self.execute_run(run_id)
            if not self._episode_reached(series_id, run_id, set(SCRIPT_DONE)):
                return

        # -- 4. series checks
        with session_scope() as session:
            series = session.get(Series, series_id)
            assert series is not None
            runs = episode_runs(session, series_id)
            scripts = {
                index: script
                for index, run in runs.items()
                if (script := final_script(session, store, run)) is not None
            }
            planner = plan_run(session, series_id)
            parsed = self._series_parse(session, store, planner)
            checks = dict(series.checks_json or {})
            if parsed is not None:
                checks["S1"] = coverage_check(parsed, plan, scripts)
            series.checks_json = checks
            series.status = "completed"
            series.finished_at = datetime.now(UTC)
        bus.publish(channel, "series.completed", {"episodes": len(plan.episodes)})

    def _episode_reached(self, series_id: str, run_id: str, states: set[str]) -> bool:
        """Whether the episode run ended in one of ``states``; fails the series otherwise."""
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is not None and run.status in states:
                return True
            index = run.episode_index if run is not None else "?"
            error = ((run.error if run is not None else None) or "the run failed").split("\n\n")[0]
        self._fail_series(series_id, f"Folge {index}: {error}")
        return False

    def _series_parse(
        self, session: Session, store: ArtifactStore, planner: Run | None
    ) -> ParsedDocument | None:
        if planner is None:
            return None
        row = session.scalars(
            sa_select(RunNode).where(RunNode.run_id == planner.id, RunNode.node_name == "ingest")
        ).first()
        if row is None or not row.artifact_hash or not store.exists(row.artifact_hash):
            return None
        return ParsedDocument.model_validate(store.get_raw(row.artifact_hash))

    def _fail_series(self, series_id: str, error: str) -> None:
        with session_scope() as session:
            series = session.get(Series, series_id)
            if series is None:
                return
            series.status = "failed"
            series.error = error
            series.finished_at = datetime.now(UTC)
        bus.publish(series_channel(series_id), "series.failed", {"error": error})

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
        self._emit(run_id, "run.paused", {"node": result.paused_at})

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
        self._emit(run_id, "run.failed", {"error": error, "verdict": verdict})

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
        if (
            existing is not None
            and record.cache_hit
            and existing.artifact_hash
            and existing.artifact_hash == record.artifact_hash
            and existing.cache_key == record.cache_key
        ):
            # This run computed the node in an earlier execution (a series'
            # outline stage, or before a pause). Keep that row: its cost,
            # tokens and timing are what the node really took.
            return
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


def _series_seeds(config: dict[str, Any], store: ArtifactStore) -> dict[str, Any]:
    """The extra seeds of a series run: its request, brief or context."""
    seeds: dict[str, Any] = {}
    if config.get("series_request"):
        seeds["series_request"] = SeriesRequest.model_validate(config["series_request"])
    if config.get("episode_brief_artifact"):
        seeds["episode_brief"] = store.get(config["episode_brief_artifact"], EpisodeBrief)
    if config.get("series_context_artifact"):
        seeds["series_context"] = store.get(config["series_context_artifact"], SeriesContext)
    return seeds


def _run_cost(session: Session, run_id: str, fallback: float) -> float:
    """What every node of the run cost, across all of its executions."""
    rows = session.scalars(sa_select(RunNode).where(RunNode.run_id == run_id)).all()
    total = round(sum(row.cost_usd or 0.0 for row in rows), 8)
    return total if rows else fallback


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
