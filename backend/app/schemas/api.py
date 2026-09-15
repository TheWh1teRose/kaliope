"""API request and response models (§11)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.document import AnchorRect, IngestionReport
from app.schemas.gates import GateReport, GateSpec
from app.schemas.pipeline import AudienceSpec, FormatSpec, NoteSubject


class LoginRequest(BaseModel):
    # Deliberately a plain string rather than ``EmailStr``. Accounts are created
    # by an administrator on the command line, and an internal deployment may
    # well use an address like ``admin@company.internal`` — a strict validator
    # here would accept the account at creation and then refuse every login.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    active: bool


class DocumentOut(BaseModel):
    id: str
    filename: str
    sha256: str
    title: str | None
    uploaded_at: str
    uploaded_by: str | None
    page_count: int | None
    language: str | None
    parse_version: int
    parse_status: str
    parse_error: str | None = None
    report: IngestionReport | None = None
    #: ``None`` is the root of the folder tree.
    folder_id: str | None = None


# ------------------------------------------------------------------- folders


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    #: 0 at the root. Enough to indent the tree without walking it again.
    depth: int
    #: Names from the root down to and including this folder.
    path: list[str]
    document_count: int
    #: Including every subfolder.
    total_document_count: int
    created_at: str


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class FolderMove(BaseModel):
    #: ``None`` moves to the root.
    parent_id: str | None = None


class DocumentMove(BaseModel):
    #: ``None`` moves the document out of every folder, back to the root.
    folder_id: str | None = None


class BlockOut(BaseModel):
    id: str
    ordinal: int
    text: str
    page: int
    bboxes: list[tuple[int, tuple[float, float, float, float]]]
    zone: str
    zone_confidence: float
    zone_uncertain: bool
    salience: float
    section_id: str | None
    heading_level: int | None
    is_table: bool = False
    #: Set when a reviewer relabelled this block.
    zone_overridden_from: str | None = None


class SectionOut(BaseModel):
    id: str
    title: str
    level: int
    ordinal: int
    parent_id: str | None
    block_ids: list[str]
    page_start: int | None
    page_end: int | None


class StructureOut(BaseModel):
    document_id: str
    parse_version: int
    language: str
    page_count: int
    page_sizes: list[tuple[float, float]]
    sections: list[SectionOut] | None
    blocks: list[BlockOut]
    objectives: list[str]
    zone_catalogue: list[dict[str, Any]]


class ZoneUpdate(BaseModel):
    zone: str
    reason_code: str = "ZONE_WRONG"
    note: str | None = None


class CreateRunRequest(BaseModel):
    document_id: str
    flow_id: str = "baseline_v0"
    format_id: str = "two_host_dialogue"
    target_minutes: int | None = None
    audience_spec: AudienceSpec | None = None
    language: str | None = None
    force: bool = False


class RunNodeOut(BaseModel):
    node_name: str
    node_version: str
    cache_hit: bool
    artifact_hash: str | None
    cost_usd: float
    tokens_in: int
    tokens_out: int
    model_id: str | None
    started_at: str | None
    finished_at: str | None
    error: str | None


class PauseOut(BaseModel):
    node: str
    subject: NoteSubject
    instructions: str = ""


class RunOut(BaseModel):
    id: str
    document_id: str
    document_title: str | None = None
    flow_id: str
    flow_version: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    total_cost_usd: float
    target_minutes: int | None = None
    verdict: str | None = None
    format_spec: FormatSpec | None = None
    audience_spec: AudienceSpec | None = None
    nodes: list[RunNodeOut] = Field(default_factory=list)
    gates: list[GateReport] = Field(default_factory=list)
    manifest: dict[str, Any] | None = None
    pause: PauseOut | None = None


class AnchorOut(BaseModel):
    block_id: str
    char_start: int
    char_end: int
    text: str
    resolved: bool
    rects: list[AnchorRect]


class SegmentCommentOut(BaseModel):
    id: str
    user_id: str
    user_email: str | None = None
    note: str
    created_at: str


class SegmentOut(BaseModel):
    id: str
    ordinal: int
    speaker: str
    text: str
    kind: str
    beat_id: str | None
    anchors: list[AnchorOut]
    violations: list[dict[str, Any]] = Field(default_factory=list)
    #: Reviewer state derived from the run's edit events.
    accepted: bool = False
    flagged: bool = False
    edited: bool = False
    #: The generated text, kept alongside an edit so the editor can show both.
    original_text: str | None = None
    #: True when there is a review action on this segment an undo can take back.
    undoable: bool = False
    tags: list[str] = Field(default_factory=list)
    comments: list[SegmentCommentOut] = Field(default_factory=list)


class ScriptOut(BaseModel):
    run_id: str
    document_id: str
    parse_version: int
    language: str
    format_spec: FormatSpec
    segments: list[SegmentOut]
    word_count: int


class FlowOut(BaseModel):
    id: str
    version: str
    description: str | None
    nodes: list[str]
    gates: list[str]


# --------------------------------------------------------------- flow graph


class FieldOut(BaseModel):
    """One field of a node's input or output model."""

    name: str
    type: str
    required: bool
    description: str | None = None


class PortOut(BaseModel):
    """A value flowing between nodes, named by the key it is published under."""

    key: str
    model: str
    fields: list[FieldOut] = Field(default_factory=list)
    #: Node that publishes this value. ``None`` means the run itself seeds it.
    produced_by: str | None = None


class FlowNodeOut(BaseModel):
    name: str
    version: str
    description: str | None = None
    consumes: list[PortOut] = Field(default_factory=list)
    produces: PortOut
    config: dict[str, Any] = Field(default_factory=dict)
    #: Gate ids whose findings are about this node's output.
    checked_by: list[str] = Field(default_factory=list)


class EdgeOut(BaseModel):
    #: ``None`` when the value is a run seed rather than a node's output.
    from_node: str | None
    to_node: str
    key: str


class FlowGraphOut(BaseModel):
    flow_id: str
    flow_version: str
    description: str | None = None
    nodes: list[FlowNodeOut]
    edges: list[EdgeOut]
    #: Keys the run supplies before any node runs.
    seeds: list[PortOut] = Field(default_factory=list)
    gates: list[GateSpec] = Field(default_factory=list)


NodeRunStatus = Literal["pending", "running", "cached", "ok", "failed", "blocked", "paused"]


class RunGraphNodeOut(FlowNodeOut):
    status: NodeRunStatus
    cache_hit: bool = False
    artifact_hash: str | None = None
    model_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wall_ms: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    #: Shape of what this node actually produced, without shipping the artifact.
    output_summary: dict[str, Any] = Field(default_factory=dict)
    #: One entry per gate that reported on this node's output.
    findings: list[dict[str, Any]] = Field(default_factory=list)


class RunGraphOut(BaseModel):
    run_id: str
    flow_id: str
    flow_version: str
    status: str
    nodes: list[RunGraphNodeOut]
    edges: list[EdgeOut]
    seeds: list[PortOut] = Field(default_factory=list)
    #: The node that failed, if one did. Everything after it is ``blocked``.
    failed_node: str | None = None
    error: str | None = None
    total_cost_usd: float = 0.0


class NodeValueOut(BaseModel):
    """One concrete input or output of a node in one run."""

    key: str
    model: str
    produced_by: str | None = None
    artifact_hash: str | None = None
    #: Present when the value is available; a summary, never the whole artifact.
    summary: dict[str, Any] = Field(default_factory=dict)
    #: Truncated JSON, for reading the value without downloading it.
    preview: str | None = None
    truncated: bool = False
    available: bool = True


class NodeIOOut(BaseModel):
    run_id: str
    node_name: str
    node_version: str
    status: NodeRunStatus
    config: dict[str, Any] = Field(default_factory=dict)
    cache_key: str | None = None
    cache_hit: bool = False
    model_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wall_ms: int | None = None
    error: str | None = None
    inputs: list[NodeValueOut] = Field(default_factory=list)
    output: NodeValueOut | None = None


class ReviewCompleteRequest(BaseModel):
    #: Final text per segment id. Segments not listed keep their generated text.
    segments: dict[str, str] = Field(default_factory=dict)
    note: str | None = None


class HealthOut(BaseModel):
    status: str
    version: str
    llm_configured: bool
    flows: list[str]
