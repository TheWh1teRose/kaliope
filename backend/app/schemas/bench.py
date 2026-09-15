"""Wire types for the node testing bench."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.api import NodeValueOut, PauseOut
from app.schemas.authoring import FlowNodeIn
from app.schemas.pipeline import AudienceSpec, FormatSpec


class SeedIn(BaseModel):
    """One value in the bag, either inline or already stored as an artifact."""

    payload: Any | None = None
    artifact_hash: str | None = None

    @model_validator(mode="after")
    def _has_a_value(self) -> SeedIn:
        if self.payload is None and not self.artifact_hash:
            raise ValueError("A seed needs a payload or an artifact_hash.")
        return self


class BenchValidateIn(BaseModel):
    nodes: list[FlowNodeIn] = Field(default_factory=list)
    seed_keys: list[str] = Field(default_factory=list)


class BenchLoadIn(BaseModel):
    document_id: str | None = None
    run_id: str | None = None
    bench_run_id: str | None = None
    format_id: str | None = None
    keys: list[str] | None = None
    include_payload: bool = False


class BenchRunIn(BaseModel):
    document_id: str | None = None
    nodes: list[FlowNodeIn] = Field(min_length=1)
    seeds: dict[str, SeedIn] = Field(default_factory=dict)
    force: bool = False
    #: Execute only this node. Its inputs must already be in ``seeds``.
    only: str | None = None
    #: Execute this node and everything after it.
    from_node: str | None = None


class ValueKeyOut(BaseModel):
    key: str
    model: str
    produced_by: list[str] = Field(default_factory=list)
    consumed_by: list[str] = Field(default_factory=list)
    json_schema: dict[str, Any] = Field(default_factory=dict)
    template: Any = None
    seed: bool = False


class BenchCatalogueOut(BaseModel):
    values: list[ValueKeyOut]
    models: list[str] = Field(default_factory=list)
    default_format: FormatSpec | None = None
    default_audience: AudienceSpec | None = None


class BenchValueOut(NodeValueOut):
    """A bag value, with where it came from and optionally the full payload."""

    source: str = "empty"
    source_id: str | None = None
    payload: Any | None = None


class BenchLoadOut(BaseModel):
    values: list[BenchValueOut]
    nodes: list[FlowNodeIn] | None = None
    document_id: str | None = None


class LLMTraceOut(BaseModel):
    """One model call as the node sent it, plus the raw completion text."""

    node_name: str | None = None
    model: str
    system: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int = 0
    response_text: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class BenchNodeOut(BaseModel):
    node_name: str
    node_version: str
    cache_hit: bool
    artifact_hash: str | None = None
    cost_usd: float
    tokens_in: int
    tokens_out: int
    model_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    wall_ms: int | None = None
    error: str | None = None
    status: str


class BenchRunOut(BaseModel):
    id: str
    document_id: str | None
    document_title: str | None
    nodes: list[str]
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    total_cost_usd: float
    verdict: str | None
    force: bool
    records: list[BenchNodeOut] = Field(default_factory=list)
    values: list[BenchValueOut] = Field(default_factory=list)
    llm_traces: list[LLMTraceOut] = Field(default_factory=list)
    pause: PauseOut | None = None
