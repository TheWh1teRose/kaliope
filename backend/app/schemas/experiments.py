"""Wire types for the Experimentieren section."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.experiments.base import BeatOut, FieldSpec, SourceIn


class ExperimentStats(BaseModel):
    run_count: int = 0
    saved_count: int = 0
    spent_usd: float = 0.0
    last_run_at: str | None = None


class ExperimentSummaryOut(BaseModel):
    key: str
    title: str
    summary: str
    target: str
    version: str
    stats: ExperimentStats


class ExperimentDetailOut(ExperimentSummaryOut):
    #: The experiment's starting setup (its own ``Setup`` model, as JSON).
    defaults: dict[str, Any]
    fields: list[FieldSpec] = Field(default_factory=list)
    #: Experiment-specific texts the page shows but the setup does not hold.
    extras: dict[str, Any] = Field(default_factory=dict)


class ExperimentSourceOut(BaseModel):
    fields: dict[str, str]
    beats: list[BeatOut]
    source: dict[str, Any]


class ExperimentRunIn(BaseModel):
    #: Validated against the experiment's ``Setup`` model on the server.
    setup: dict[str, Any]
    source: SourceIn | None = None
    #: Shown with the run; the server keeps what the loader returned.
    source_meta: dict[str, Any] | None = None


class LLMCallOut(BaseModel):
    """One model call of a run, as it was sent."""

    node_name: str | None = None
    model: str
    system: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    response_text: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    thinking: str | None = None
    thinking_budget: int | None = None
    effort: str | None = None
    max_tokens: int = 0


class ItemOut(BaseModel):
    item: str
    title: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    #: Set when this item is already collected.
    output_id: str | None = None


class ExperimentRunOut(BaseModel):
    id: str
    experiment_key: str
    experiment_version: str
    status: str
    setup: dict[str, Any]
    source: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    total_cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    wall_ms: int | None = None
    created_at: str
    finished_at: str | None = None
    items: list[ItemOut] = Field(default_factory=list)
    calls: list[LLMCallOut] = Field(default_factory=list)


class SaveIn(BaseModel):
    item: str = "main"
    label: str | None = Field(default=None, max_length=200)


class OutputOut(BaseModel):
    id: str
    experiment_key: str
    run_id: str | None
    item: str
    label: str | None
    output: dict[str, Any] | None
    text: str | None
    meta: dict[str, Any]
    setup: dict[str, Any]
    created_at: str
