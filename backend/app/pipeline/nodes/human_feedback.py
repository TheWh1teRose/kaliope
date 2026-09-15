"""The ``human_feedback`` node.

Pauses the run so a person can write notes on an outline or a script. The
console collects the notes; submitting them resumes the flow. This node never
calls a model and is never reused from another run's cache — the notes belong
to this run.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.pipeline.framework.node import NodeContext, NodePause
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.notes import resolve_subject
from app.schemas.pipeline import Notes, Outline, Script

_DEFAULT_INSTRUCTIONS = (
    "Lesen Sie den Ablaufplan oder das Skript und schreiben Sie Anmerkungen "
    "an die Stellen, die geändert werden sollen. Jede Anmerkung bezieht sich "
    "auf einen Abschnitt oder ein Segment."
)


class HumanFeedbackInput(BaseModel):
    outline: Outline | None = None
    script: Script | None = None
    notes: Notes | None = None


class HumanFeedbackNode:
    name = "human_feedback"
    title = "Menschliche Anmerkungen"
    version = "1.0"
    Input: type[BaseModel] = HumanFeedbackInput
    Output: type[BaseModel] = Notes
    produces = "notes"
    #: Notes are written for this run. Reusing another run's notes would
    #: skip the person the node exists for.
    cacheable = False

    doc = NodeDoc(
        summary="Pauses the pipeline so a person can write notes on the outline or script.",
        detail=[
            "Does not call a model. The node raises a pause; the worker stores "
            "the outline or script that is in the bag and waits.",
            "The console shows the same kind of two-pane view as review, but "
            "the actions are only: attach a note to a beat or a segment. "
            "Review (accept, edit, flag) is a separate step after the run "
            "finishes and is not replaced.",
            "Submitting the notes resumes the run. If an earlier critic "
            "already published notes, the human notes are appended.",
            "An empty list is allowed: the person had nothing to add.",
            "In the bench, the same pause happens, and the notes can be "
            "written there. Seeding a notes value and running apply_notes "
            "alone is the other way to test a revision without this node.",
        ],
        inputs={
            "outline": "Beats the notes may point at. Optional when a script is present.",
            "script": "Spoken segments the notes may point at. Optional when only "
            "an outline is being annotated.",
            "notes": "Notes already collected; the person's notes are appended on submit.",
        },
        output="A Notes list written by a person, merged with any notes already in the bag.",
        failure_modes=[
            "Neither an outline nor a script is in the bag.",
            "The subject parameter asks for an artifact that is not there.",
        ],
        cost="None. The run waits; no model is called.",
    )

    params = [
        NodeParam(
            key="instructions",
            label="Instructions",
            type="text",
            default=_DEFAULT_INSTRUCTIONS,
            description="Shown to the person who writes the notes.",
        ),
        NodeParam(
            key="subject",
            label="Subject",
            type="select",
            default="auto",
            options=["auto", "outline", "script"],
            description="Which artifact to annotate. Auto prefers the script when both exist.",
        ),
    ]

    def run(self, inp: HumanFeedbackInput, ctx: NodeContext) -> Notes:
        subject = resolve_subject(str(ctx.get("subject") or "auto"), inp.outline, inp.script)
        instructions = str(ctx.get("instructions") or _DEFAULT_INSTRUCTIONS).strip()
        raise NodePause(
            f"waiting for human notes on the {subject}",
            payload={
                "subject": subject,
                "instructions": instructions,
                "existing_count": len(inp.notes.items) if inp.notes else 0,
            },
        )


register_node(HumanFeedbackNode())
