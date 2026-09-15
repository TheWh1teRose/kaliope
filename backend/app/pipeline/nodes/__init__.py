"""Baseline flow nodes (§6). Importing this package registers every node."""

from app.pipeline.nodes import (  # noqa: F401
    ai_critic,
    apply_notes,
    content_budget,
    human_feedback,
    ingest,
    objective_outline,
    objective_select,
    objectives,
    outline,
    script,
    select,
)

__all__ = [
    "ai_critic",
    "apply_notes",
    "content_budget",
    "human_feedback",
    "ingest",
    "objective_outline",
    "objective_select",
    "objectives",
    "outline",
    "script",
    "select",
]
