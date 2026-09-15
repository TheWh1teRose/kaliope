"""Flow execution and the run manifest (§7.2, §7.4, §7.6).

The runner owns caching, cost accounting and the manifest. It holds no database
session and no HTTP context: persistence and progress reporting arrive as
callbacks, so the same runner drives a background worker, the CLI and the tests.
"""

from __future__ import annotations

import logging
import traceback as traceback_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.llm.base import LLMClient, Usage
from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.pipeline.framework.keys import node_key
from app.pipeline.framework.node import (
    NodeContext,
    NodeError,
    NodePause,
    build_input,
    input_keys,
)
from app.pipeline.framework.registry import Flow, get_node

logger = logging.getLogger(__name__)


class NodeRecord(BaseModel):
    name: str
    version: str
    config: dict[str, Any] = Field(default_factory=dict)
    cache_key: str
    cache_hit: bool
    artifact_hash: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    wall_ms: int = 0
    model_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    #: Adaptation warnings raised by the provider layer.
    warnings: list[str] = Field(default_factory=list)


class RunManifest(BaseModel):
    run_id: str
    flow_id: str
    flow_version: str
    nodes: list[NodeRecord] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    llm_calls: int = 0
    started_at: str | None = None
    finished_at: str | None = None

    def recompute_total(self) -> float:
        self.total_cost_usd = round(sum(n.cost_usd for n in self.nodes), 8)
        return self.total_cost_usd


@dataclass
class RunResult:
    status: str
    manifest: RunManifest
    bag: dict[str, Any] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    traceback: str | None = None
    #: Set when a node failed with a modelled verdict (e.g. ``insufficient``).
    verdict: str | None = None
    #: Name of the node that raised :class:`NodePause`, when the run is waiting.
    paused_at: str | None = None
    #: Payload the paused node handed to the console (subject, instructions).
    pause: dict[str, Any] | None = None


LLMCallRecorder = Callable[..., None]
CacheLookup = Callable[[str], str | None]
ProgressSink = Callable[[str, dict[str, Any]], None]


class FlowRunner:
    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        llm: LLMClient,
        cache_lookup: CacheLookup | None = None,
        on_node: Callable[[NodeRecord], None] | None = None,
        progress: ProgressSink | None = None,
        document_path: Path | None = None,
        force: bool = False,
    ) -> None:
        self.artifacts = artifacts
        self.llm = llm
        self.cache_lookup = cache_lookup or (lambda _key: None)
        self.on_node = on_node or (lambda _record: None)
        self.progress = progress or (lambda _event, _data: None)
        self.document_path = document_path
        self.force = force

    def execute(
        self,
        flow: Flow,
        run_id: str,
        seeds: dict[str, Any],
        *,
        document_id: str | None = None,
        config_overrides: dict[str, dict[str, Any]] | None = None,
        resume_outputs: dict[str, str] | None = None,
    ) -> RunResult:
        manifest = RunManifest(
            run_id=run_id,
            flow_id=flow.id,
            flow_version=flow.version,
            started_at=_now(),
        )
        bag: dict[str, Any] = dict(seeds)
        hashes: dict[str, str] = {key: _seed_hash(value) for key, value in seeds.items()}
        overrides = config_overrides or {}
        resumed = resume_outputs or {}

        for entry in flow.nodes:
            node = get_node(entry.node)
            config = {**entry.config, **overrides.get(entry.node, {})}
            model_id = config.get("model")

            available = [k for k in input_keys(node) if k in hashes]
            key = node_key(
                name=node.name,
                version=node.version,
                config=config,
                model_id=str(model_id) if model_id else None,
                input_hashes=[hashes[k] for k in available],
            )

            record = NodeRecord(
                name=node.name,
                version=node.version,
                config=config,
                cache_key=key,
                cache_hit=False,
                model_id=str(model_id) if model_id else None,
                started_at=_now(),
            )

            resumed_hash = resumed.get(entry.node)
            if resumed_hash and self.artifacts.exists(resumed_hash):
                record.cache_hit = True
                record.artifact_hash = resumed_hash
                record.finished_at = _now()
                bag[node.produces] = node.Output.model_validate(
                    self.artifacts.get_raw(resumed_hash)
                )
                hashes[node.produces] = resumed_hash
                manifest.nodes.append(record)
                self.on_node(record)
                self.progress("node.cached", {"node": node.name, "artifact": resumed_hash})
                continue

            cacheable = bool(getattr(node, "cacheable", True))
            cached = None if self.force or not cacheable else self.cache_lookup(key)
            if cached and self.artifacts.exists(cached):
                record.cache_hit = True
                record.artifact_hash = cached
                record.finished_at = _now()
                bag[node.produces] = node.Output.model_validate(self.artifacts.get_raw(cached))
                hashes[node.produces] = cached
                manifest.nodes.append(record)
                self.on_node(record)
                self.progress("node.cached", {"node": node.name, "artifact": cached})
                continue

            self.progress("node.started", {"node": node.name})
            started = datetime.now(UTC)
            usage_before = self.llm.total_usage
            cost_before = self.llm.total_cost_usd
            calls_before = self.llm.calls
            self.llm.node_name = node.name

            context = NodeContext(
                run_id=run_id,
                llm=self.llm,
                artifacts=self.artifacts,
                config=config,
                logger=logging.getLogger(f"kalliope.node.{node.name}"),
                document_path=self.document_path,
                document_id=document_id,
                progress=_progress_for(self.progress, node.name),
            )

            try:
                output = node.run(build_input(node, bag), context)
            except NodePause as exc:
                record.finished_at = None
                manifest.nodes.append(record)
                self.on_node(record)
                self.progress("run.paused", {"node": node.name})
                self.llm.node_name = None
                manifest.recompute_total()
                return RunResult(
                    status="paused",
                    manifest=manifest,
                    bag=bag,
                    artifact_hashes=hashes,
                    paused_at=node.name,
                    pause=dict(exc.payload),
                )
            except NodeError as exc:
                self._finish_failed_node(record, manifest, exc, usage_before, cost_before, started)
                return RunResult(
                    status="failed",
                    manifest=manifest,
                    bag=bag,
                    artifact_hashes=hashes,
                    error=str(exc),
                    traceback=traceback_module.format_exc(),
                    verdict=exc.verdict,
                )
            except Exception as exc:  # noqa: BLE001 - recorded, then the run fails
                self._finish_failed_node(record, manifest, exc, usage_before, cost_before, started)
                return RunResult(
                    status="failed",
                    manifest=manifest,
                    bag=bag,
                    artifact_hashes=hashes,
                    error=f"{type(exc).__name__}: {exc}",
                    traceback=traceback_module.format_exc(),
                )

            stored = self.artifacts.put(node.produces, output)
            usage_delta = _usage_delta(usage_before, self.llm.total_usage)
            record.artifact_hash = stored.hash
            record.finished_at = _now()
            record.wall_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            record.tokens_in = usage_delta.input_tokens + usage_delta.cache_read_tokens
            record.tokens_out = usage_delta.output_tokens
            record.cost_usd = round(self.llm.total_cost_usd - cost_before, 8)

            bag[node.produces] = output
            hashes[node.produces] = stored.hash
            manifest.nodes.append(record)
            manifest.llm_calls += self.llm.calls - calls_before
            self.on_node(record)
            self.progress(
                "node.finished",
                {"node": node.name, "artifact": stored.hash, "cost_usd": record.cost_usd},
            )

        self.llm.node_name = None
        manifest.finished_at = _now()
        manifest.recompute_total()
        return RunResult(status="completed", manifest=manifest, bag=bag, artifact_hashes=hashes)

    def _finish_failed_node(
        self,
        record: NodeRecord,
        manifest: RunManifest,
        exc: Exception,
        usage_before: Usage,
        cost_before: float,
        started: datetime,
    ) -> None:
        usage_delta = _usage_delta(usage_before, self.llm.total_usage)
        record.error = f"{type(exc).__name__}: {exc}"
        record.finished_at = _now()
        record.wall_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        record.tokens_in = usage_delta.input_tokens + usage_delta.cache_read_tokens
        record.tokens_out = usage_delta.output_tokens
        record.cost_usd = round(self.llm.total_cost_usd - cost_before, 8)
        manifest.nodes.append(record)
        manifest.finished_at = _now()
        manifest.recompute_total()
        self.on_node(record)
        self.progress("node.failed", {"node": record.name, "error": record.error})
        self.llm.node_name = None


def _progress_for(sink: ProgressSink, node_name: str) -> Callable[[str], None]:
    def report(message: str) -> None:
        sink("node.progress", {"node": node_name, "message": message})

    return report


def _usage_delta(before: Usage, after: Usage) -> Usage:
    return Usage(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
        cache_read_tokens=after.cache_read_tokens - before.cache_read_tokens,
        cache_write_tokens=after.cache_write_tokens - before.cache_write_tokens,
    )


def _seed_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        return hash_payload(value.model_dump(mode="json"))
    if isinstance(value, Path):
        return hash_payload(str(value))
    return hash_payload(value)


def _now() -> str:
    return datetime.now(UTC).isoformat()
