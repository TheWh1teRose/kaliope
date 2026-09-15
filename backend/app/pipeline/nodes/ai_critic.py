"""The ``ai_critic`` node.

A model reads an outline or a script against author-supplied criteria and
publishes a list of notes. Each note points at a beat or a script segment.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.notes import merge_notes, new_note_id, resolve_subject
from app.schemas.pipeline import (
    AudienceSpec,
    FormatSpec,
    Note,
    Notes,
    Outline,
    Script,
)

_SYSTEM = """\
You review an outline or a script against the given criteria.

Write notes a later revision step can act on. Each note:
- points at exactly one beat or one spoken segment, using an id from the list;
- states a concrete problem or a concrete change in one or two sentences;
- names the criterion it applies, in a short phrase.

Prefer a few high-value notes over a long list. Do not rewrite the outline
or the script. Do not invent ids. If nothing fails the criteria, return an
empty notes list.

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_kind": {"type": "string", "enum": ["beat", "segment"]},
                    "target_id": {"type": "string"},
                    "text": {"type": "string"},
                    "criterion": {"type": "string"},
                },
                "required": ["target_kind", "target_id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["notes"],
    "additionalProperties": False,
}

_DEFAULT_CRITERIA = (
    "Fakten müssen aus den genannten Passagen stammen.\n"
    "Die Sprache ist gesprochen, nicht geschrieben: keine Aufzählungen, "
    "keine Überschriften.\n"
    "Jeder Beat hat einen klaren Fokus und eine nachvollziehbare Reihenfolge.\n"
    "Übergänge erklären, warum der nächste Gedanke jetzt kommt."
)


class CriticInput(BaseModel):
    outline: Outline | None = None
    script: Script | None = None
    format_spec: FormatSpec | None = None
    audience_spec: AudienceSpec | None = None
    notes: Notes | None = None


class AiCriticNode:
    name = "ai_critic"
    title = "KI-Kritik"
    version = "1.0"
    Input: type[BaseModel] = CriticInput
    Output: type[BaseModel] = Notes
    produces = "notes"

    doc = NodeDoc(
        summary="A model writes notes on an outline or a script against given criteria.",
        detail=[
            "Reads whichever artifact is in the bag — a script when both are "
            "present, unless the subject parameter says otherwise — and the "
            "criteria the author configured.",
            "Each note names a beat or a segment that already exists. Notes "
            "that point at an unknown id are dropped and reported as progress, "
            "so a single hallucinated id does not fail the run.",
            "If an earlier node already published notes, this step appends to "
            "them rather than replacing them.",
            "An empty list is a valid result: the model found nothing that "
            "fails the criteria.",
        ],
        inputs={
            "outline": "Beats the notes may point at. Optional when a script is present.",
            "script": "Spoken segments the notes may point at. Optional when only "
            "an outline is being reviewed.",
            "format_spec": "Speakers and register, so the critic can judge voice.",
            "audience_spec": "Who is listening, so the critic can judge level.",
            "notes": "Notes already collected; new ones are appended.",
        },
        output="A Notes list: each item has text, a beat or segment target, and a criterion.",
        failure_modes=[
            "Neither an outline nor a script is in the bag.",
            "The subject parameter asks for an artifact that is not there.",
        ],
        cost="One call over the whole outline or script.",
    )

    params = [
        NodeParam(
            key="criteria",
            label="Criteria",
            type="text",
            default=_DEFAULT_CRITERIA,
            description="What the model should look for. One idea per line works well.",
        ),
        NodeParam(
            key="subject",
            label="Subject",
            type="select",
            default="auto",
            options=["auto", "outline", "script"],
            description="Which artifact to review. Auto prefers the script when both exist.",
        ),
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description="How the model writes notes. Keep the demand for real ids.",
        ),
        NodeParam(
            key="model",
            label="Model",
            type="model",
            description="Empty falls back to DEFAULT_MODEL from the environment.",
        ),
        NodeParam(
            key="temperature",
            label="Temperature",
            type="float",
            default=0.3,
            minimum=0.0,
            maximum=2.0,
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens",
            type="int",
            default=16000,
            minimum=500,
            maximum=32000,
            advanced=True,
        ),
    ]

    def run(self, inp: CriticInput, ctx: NodeContext) -> Notes:
        subject = resolve_subject(str(ctx.get("subject") or "auto"), inp.outline, inp.script)
        criteria = str(ctx.get("criteria") or _DEFAULT_CRITERIA).strip()
        if not criteria:
            raise NodeError("the critic has no criteria to apply")

        ctx.progress(f"reviewing the {subject}")
        data = ctx.llm.complete(
            CompletionRequest(
                model=ctx.model("claude-opus-5"),
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=_prompt(inp, subject, criteria))],
                max_tokens=int(ctx.get("max_tokens", 16000)),
                temperature=ctx.get("temperature"),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        ).json_payload()

        beats = {beat.id for beat in (inp.outline.beats if inp.outline else [])}
        if inp.script:
            beats.update(segment.beat_id for segment in inp.script.segments if segment.beat_id)
        segments = {segment.id for segment in inp.script.segments} if inp.script else set()

        collected: list[Note] = []
        dropped = 0
        for entry in data.get("notes", []):
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text", "")).strip()
            target_id = str(entry.get("target_id", "")).strip()
            kind = str(entry.get("target_kind", "")).strip()
            if not text or not target_id:
                continue
            if kind == "segment":
                if subject == "outline" or target_id not in segments:
                    dropped += 1
                    continue
            elif kind == "beat":
                if target_id not in beats:
                    dropped += 1
                    continue
            else:
                dropped += 1
                continue
            collected.append(
                Note(
                    id=new_note_id(),
                    text=text,
                    target={"kind": kind, "id": target_id},  # type: ignore[arg-type]
                    source="ai_critic",
                    criterion=str(entry.get("criterion") or "").strip() or None,
                )
            )

        if dropped:
            ctx.progress(f"dropped {dropped} note(s) that pointed at unknown ids")
        return merge_notes(inp.notes, collected, subject=subject)


def _prompt(inp: CriticInput, subject: str, criteria: str) -> str:
    parts = [f"Review the {subject} against these criteria:\n{criteria}\n"]
    if inp.audience_spec:
        parts.append(f"Audience: {inp.audience_spec.description}")
    if inp.format_spec:
        parts.append(f"Register: {inp.format_spec.register}")
        parts.append("Speakers: " + ", ".join(inp.format_spec.speaker_names()))
    if inp.outline:
        parts.append("Beats:")
        for beat in inp.outline.beats:
            summary = f" — {beat.summary}" if beat.summary else ""
            parts.append(f"- [{beat.id}] {beat.title}{summary}")
    if inp.script:
        parts.append("Segments:")
        for segment in inp.script.segments:
            preview = segment.text if len(segment.text) <= 280 else segment.text[:277] + "…"
            parts.append(
                f"- [{segment.id}] beat={segment.beat_id} {segment.speaker}: {preview}"
            )
    return "\n".join(parts)


register_node(AiCriticNode())
