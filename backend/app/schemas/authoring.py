"""Wire types for the pipeline and format editors (§11).

Kept apart from ``schemas/api.py`` because they are the only ones that travel in
both directions: everything else the console receives is a projection of a run,
while these are edited and posted back.
"""

from __future__ import annotations

import warnings
from typing import Any

from pydantic import BaseModel, Field

from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.api import FieldOut, PortOut
from app.schemas.pipeline import FormatSpec

# Same field name, same reason as ``FormatSpec.register`` in ``schemas.pipeline``:
# it shadows a class-level method that instances never reach.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "register" in "FormatSummaryOut" shadows an attribute',
    category=UserWarning,
)

# ------------------------------------------------------------------- nodes


class NodeSpecOut(BaseModel):
    """Everything the editor needs to place and configure one node."""

    name: str
    title: str
    version: str
    doc: NodeDoc
    params: list[NodeParam] = Field(default_factory=list)
    #: Input value names, in the order the node declares them.
    consumes: list[str] = Field(default_factory=list)
    #: The key this node publishes for later nodes.
    produces: str
    input_model: str
    output_model: str
    output_fields: list[FieldOut] = Field(default_factory=list)
    #: Quality checks that inspect this node's output.
    checked_by: list[str] = Field(default_factory=list)


class NodeCatalogueOut(BaseModel):
    nodes: list[NodeSpecOut]
    #: Values every run supplies before the first node, with their types.
    seeds: list[PortOut] = Field(default_factory=list)
    #: Keys a flow must publish for a run to finish.
    required_outputs: list[str] = Field(default_factory=list)
    #: Model ids the deployment knows how to price and call.
    models: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- pipelines


class FlowNodeIn(BaseModel):
    node: str
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineDraft(BaseModel):
    """A pipeline definition as the editor posts it."""

    name: str | None = None
    version: str = "1.0"
    description: str | None = None
    nodes: list[FlowNodeIn] = Field(default_factory=list)
    gates: list[str] = Field(default_factory=list)


class PipelineCreate(PipelineDraft):
    id: str
    note: str | None = None


class PipelineSave(PipelineDraft):
    #: What changed and why. Shown in the revision list.
    note: str | None = None


class ArchiveRequest(BaseModel):
    archived: bool = True


class RevisionOut(BaseModel):
    revision: int
    version: str
    note: str | None = None
    restored_from: int | None = None
    created_at: str
    created_by: str | None = None
    created_by_email: str | None = None
    #: Present only when a single revision is fetched.
    spec: dict[str, Any] | None = None


class PipelineSummaryOut(BaseModel):
    id: str
    name: str
    version: str
    revision: int
    description: str | None = None
    origin: str
    archived: bool
    editable: bool = True
    nodes: list[str] = Field(default_factory=list)
    gates: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    updated_by_email: str | None = None
    #: Runs recorded against this pipeline, whatever their outcome.
    run_count: int = 0
    valid: bool = True


class PipelineDetailOut(PipelineSummaryOut):
    definition: PipelineDraft
    validation: Any = None


# ----------------------------------------------------------------- formats


class FormatSummaryOut(BaseModel):
    id: str
    name: str
    revision: int
    origin: str
    archived: bool
    speakers: int
    target_minutes: int
    register: str
    updated_at: str | None = None
    updated_by_email: str | None = None
    run_count: int = 0


class FormatDetailOut(FormatSummaryOut):
    spec: FormatSpec


class FormatSave(BaseModel):
    spec: FormatSpec
    note: str | None = None
