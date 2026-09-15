"""Relational schema (§10).

JSON-shaped columns are stored as SQLite JSON. They hold pydantic models
serialised with ``model_dump(mode="json")``; the ORM layer deliberately does
not try to type them, because the pydantic schemas in ``app/schemas`` are the
authority on their shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, new_id, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()


class Folder(Base):
    """A place to put documents (§11).

    Sibling names are kept unique by the API rather than by a table constraint:
    SQLite treats every NULL as distinct, so a ``UNIQUE(parent_id, name)`` would
    hold inside a folder and quietly do nothing at the root — worse than no
    constraint, because it reads as if it covers both.
    """

    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parse_version: Mapped[int] = mapped_column(Integer, default=0)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Content hash of the ParsedDocument artifact for the current parse_version.
    parsed_artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: ``None`` is the root; deleting a folder clears it rather than the document.
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Flow(Base):
    """A pipeline, as the catalogue knows it (§7.5, §11).

    Flows ship as YAML files and can be edited in the console. ``spec_json``
    holds the edited definition; while it is ``NULL`` the file on disk is still
    the authority, so an unedited build keeps behaving exactly as it did before
    anyone opened the editor.
    """

    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: ``{"description": …, "nodes": [{"node": …, "config": {…}}], "gates": [...]}``.
    #: ``None`` means the flow has never been edited and lives only on disk.
    spec_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Monotonic revision counter. 0 while the flow is still file-backed.
    revision: Mapped[int] = mapped_column(Integer, default=0)
    #: ``file`` for a flow that shipped with the build, ``user`` for one created
    #: in the console.
    origin: Mapped[str] = mapped_column(String(10), default="file")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class FlowVersion(Base):
    """One saved revision of a flow.

    Append-only: nothing here is ever updated or deleted, and restoring an old
    revision writes a new one carrying the old content. The history is therefore
    the whole editing record, and a run's ``flow_revision`` always names a row
    that still says exactly what ran.
    """

    __tablename__ = "flow_versions"
    __table_args__ = (UniqueConstraint("flow_id", "revision", name="uq_flow_revision"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    flow_id: Mapped[str] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    #: The flow's own semantic version string at this revision.
    version: Mapped[str] = mapped_column(String(20))
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set when this revision was created by restoring an earlier one.
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FormatSpecRow(Base):
    """A format specification (§6.5), editable and versioned like a flow."""

    __tablename__ = "format_specs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(10), default="file")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class FormatVersion(Base):
    """One saved revision of a format spec. Append-only, like ``FlowVersion``."""

    __tablename__ = "format_versions"
    __table_args__ = (UniqueConstraint("format_id", "revision", name="uq_format_revision"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    format_id: Mapped[str] = mapped_column(
        ForeignKey("format_specs.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    flow_id: Mapped[str] = mapped_column(String(100))
    flow_version: Mapped[str] = mapped_column(String(20))
    #: Which saved revision of the flow this run used. 0 means the flow was
    #: still file-backed, so the YAML on disk is what ran.
    flow_revision: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    format_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    audience_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    document: Mapped[Document] = relationship()


class RunNode(Base):
    __tablename__ = "run_nodes"
    __table_args__ = (UniqueConstraint("run_id", "node_name", name="uq_run_node"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str] = mapped_column(String(100))
    node_version: Mapped[str] = mapped_column(String(20))
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    node_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GateResult(Base):
    __tablename__ = "gate_results"
    __table_args__ = (UniqueConstraint("run_id", "gate_id", name="uq_run_gate"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    gate_id: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    violations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: Why a skipped gate declined to run (AC-GATE-4).
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The numbers the gate computed, kept so a pass is auditable and not just
    #: a verdict the reviewer has to take on trust.
    measurements_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (Index("ix_segments_run_ordinal", "run_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    anchors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    beat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EditEvent(Base):
    __tablename__ = "edit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewSession(Base):
    __tablename__ = "review_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class BenchRun(Base):
    """One bench execution: a node or a short chain, not a production run.

    Kept off the ``runs`` table so experiments never appear in review, never
    feed the eval export, and never have to publish a full script.
    """

    __tablename__ = "bench_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )
    #: ``[{"node": name, "config": {...}}, ...]`` — what actually executed.
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: ``{key: {"artifact_hash": ..., "model": ...}}``.
    seeds_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Published keys after execution → artifact hash.
    bag_hashes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    verdict: Mapped[str | None] = mapped_column(String(40), nullable=True)

    document: Mapped[Document | None] = relationship()


class BenchNode(Base):
    __tablename__ = "bench_nodes"
    __table_args__ = (UniqueConstraint("bench_run_id", "node_name", name="uq_bench_node"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    bench_run_id: Mapped[str] = mapped_column(
        ForeignKey("bench_runs.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str] = mapped_column(String(100))
    node_version: Mapped[str] = mapped_column(String(20))
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
