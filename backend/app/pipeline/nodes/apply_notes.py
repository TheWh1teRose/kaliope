"""Apply collected notes, one at a time, to a script or an outline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.nodes.script import _closest_speaker, _resolve_citations
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    FormatSpec,
    Note,
    Notes,
    Outline,
    Script,
    Segment,
)

_SCRIPT_SYSTEM = """\
You revise one part of a script according to a single note.

Groundedness policy — this is the part that matters:
- Every factual assertion must come from the provided passages, and the segment
  that makes it must cite the passage it came from, quoting the exact sentence
  or clause that supports it.
- Analogies, transitions, questions, framing and encouragement are yours to
  write freely, but they must introduce no new facts. Mark those segments as
  pedagogy; they carry no citations.
- Do not state a number, name, date, definition or causal claim that is not in
  the passages. If the passages do not support something, leave it out.

Style:
- Write speech, not prose: it will be heard once, not read twice. No bullet points, no headings, no markdown, no stage directions.
- Keep it conversational, the speakers can interrupt, the conversation should feel human like.
- Use only the speakers you are given, by their exact names.
- Write in the document's language.
- Stay close to the word budget for this beat.

Return JSON only: the replacement segments for the marked target, not the
rest of the beat. You may split the target into more than one segment.
"""

_OUTLINE_SYSTEM = """\
You revise one beat of an outline according to a single note.

Rules:
- Keep the beat's id. You may change title, summary, block ids and word budget.
- Only use passage ids from the list you are given.
- Do not invent facts. The summary describes what the beat will cover, not new content.
- Write title and summary in the document's language.
- Change only what the note asks for.

Return JSON only.
"""

_SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["claim", "pedagogy"]},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "block_id": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["block_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["speaker", "text", "kind", "citations"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}

_OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "block_ids": {"type": "array", "items": {"type": "string"}},
        "word_budget": {"type": "integer"},
    },
    "required": ["title", "block_ids", "word_budget"],
    "additionalProperties": False,
}


class ApplyNotesInput(BaseModel):
    notes: Notes
    parsed: ParsedDocument
    script: Script
    format_spec: FormatSpec
    audience_spec: AudienceSpec
    outline: Outline | None = None


class ApplyOutlineNotesInput(BaseModel):
    notes: Notes
    outline: Outline
    parsed: ParsedDocument | None = None


class ApplyNotesNode:
    name = "apply_notes"
    title = "Anmerkungen umsetzen (Skript)"
    version = "1.0"
    Input: type[BaseModel] = ApplyNotesInput
    Output: type[BaseModel] = Script
    produces = "script"

    doc = NodeDoc(
        summary="Reads the notes one at a time and revises the matching part of the script.",
        detail=[
            "Walks the notes in order. A note on a segment replaces that "
            "segment (the model may split it). A note on a beat replaces "
            "every segment of that beat. Notes that point at an unknown "
            "id are skipped.",
            "Each call sees the note and the source passages that beat "
            "draws on — the same groundedness rule the script node uses, "
            "including quoted citations that the node turns into anchors. "
            "A segment note also sees the whole beat for continuity; the "
            "model still returns only the replacement for the target.",
            "An empty notes list is a no-op: the script is published unchanged.",
            "This overwrites the script in the bag. Gates and review see the "
            "revised script, not the one written before the notes.",
        ],
        inputs={
            "notes": "The notes to apply, in order.",
            "parsed": "Passage text, so new claims can be anchored.",
            "script": "The script being revised.",
            "format_spec": "Declared speakers and register.",
            "audience_spec": "Who is listening.",
            "outline": "Beat titles and passage ids, when present.",
        },
        output="A Script: the previous script with each note applied in turn.",
        failure_modes=[
            "The format spec declares no speakers.",
            "Applying the notes left the script with no segments.",
        ],
        cost="One call per note that has a matching target.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SCRIPT_SYSTEM,
            description="How the model revises a part of the script. Keep the citation rule.",
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
            default=0.5,
            minimum=0.0,
            maximum=2.0,
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens per note",
            type="int",
            default=8000,
            minimum=1000,
            maximum=32000,
            advanced=True,
        ),
    ]

    def run(self, inp: ApplyNotesInput, ctx: NodeContext) -> Script:
        speakers = inp.format_spec.speaker_names()
        if not speakers:
            raise NodeError("the format spec declares no speakers")

        script = inp.script
        total = len(inp.notes.items)
        if not total:
            ctx.progress("no notes to apply")
            return script

        for index, note in enumerate(inp.notes.items):
            ctx.progress(f"applying note {index + 1} of {total}: {note.id}")
            script = _apply_script_note(script, note, inp, ctx, speakers)

        if not script.segments:
            raise NodeError("applying the notes left the script empty")
        return script


class ApplyOutlineNotesNode:
    name = "apply_outline_notes"
    title = "Anmerkungen umsetzen (Ablaufplan)"
    version = "1.0"
    Input: type[BaseModel] = ApplyOutlineNotesInput
    Output: type[BaseModel] = Outline
    produces = "outline"

    doc = NodeDoc(
        summary="Reads the notes one at a time and revises the matching beat of the outline.",
        detail=[
            "Walks the notes in order. Only notes that target a beat are "
            "applied; segment notes are skipped because an outline has no "
            "segments.",
            "Each call sees that beat, the note, and the passage ids it may "
            "keep or swap. The beat's id is preserved so a later script node "
            "still lines up.",
            "An empty notes list is a no-op.",
        ],
        inputs={
            "notes": "The notes to apply, in order.",
            "outline": "The outline being revised.",
            "parsed": "Passage ids that a revised beat may cite.",
        },
        output="An Outline: the previous outline with each beat note applied in turn.",
        failure_modes=["Applying the notes left the outline with no beats."],
        cost="One call per note that targets a beat.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_OUTLINE_SYSTEM,
            description="How the model revises one beat.",
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
            default=0.4,
            minimum=0.0,
            maximum=2.0,
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens per note",
            type="int",
            default=2000,
            minimum=400,
            maximum=16000,
            advanced=True,
        ),
    ]

    def run(self, inp: ApplyOutlineNotesInput, ctx: NodeContext) -> Outline:
        outline = inp.outline
        total = len(inp.notes.items)
        if not total:
            ctx.progress("no notes to apply")
            return outline

        for index, note in enumerate(inp.notes.items):
            ctx.progress(f"applying note {index + 1} of {total}: {note.id}")
            outline = _apply_outline_note(outline, note, inp, ctx)

        if not outline.beats:
            raise NodeError("applying the notes left the outline empty")
        return outline


def _apply_script_note(
    script: Script,
    note: Note,
    inp: ApplyNotesInput,
    ctx: NodeContext,
    speakers: list[str],
) -> Script:
    if note.target.kind == "segment":
        positions = [i for i, segment in enumerate(script.segments) if segment.id == note.target.id]
    else:
        positions = [
            i for i, segment in enumerate(script.segments) if segment.beat_id == note.target.id
        ]
    if not positions:
        ctx.progress(f"note {note.id} has no matching target; skipped")
        return script

    start, end = positions[0], positions[-1] + 1
    current = script.segments[start:end]
    beat_id = current[0].beat_id
    context = [segment for segment in script.segments if segment.beat_id == beat_id]
    beat = next((b for b in (inp.outline.beats if inp.outline else []) if b.id == beat_id), None)
    blocks = {block.id: block for block in inp.parsed.blocks}
    beat_block_ids = list(beat.block_ids) if beat else []
    if not beat_block_ids:
        beat_block_ids = [anchor.block_id for segment in context for anchor in segment.anchors]
    beat_blocks = [blocks[bid] for bid in beat_block_ids if bid in blocks]

    data = ctx.llm.complete(
        CompletionRequest(
            model=ctx.model("claude-opus-5"),
            system=str(ctx.get("system_prompt") or _SCRIPT_SYSTEM),
            messages=[
                Message(
                    role="user",
                    content=_script_prompt(inp, note, current, beat_blocks, beat_id, context),
                )
            ],
            max_tokens=int(ctx.get("max_tokens", 8000)),
            temperature=ctx.get("temperature"),
            json_schema=_SCRIPT_SCHEMA,
            cache_system=True,
        )
    ).json_payload()

    replacement: list[Segment] = []
    used_ids = {segment.id for segment in script.segments}
    next_index = 0
    for entry in data.get("segments", []):
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        kind = "claim" if str(entry.get("kind")) == "claim" else "pedagogy"
        anchors, _missed = _resolve_citations(entry.get("citations", []), blocks, inp.parsed)
        new_id = f"{beat_id}-s{next_index:04d}"
        while new_id in used_ids:
            next_index += 1
            new_id = f"{beat_id}-s{next_index:04d}"
        used_ids.add(new_id)
        next_index += 1
        replacement.append(
            Segment(
                id=new_id,
                speaker=_closest_speaker(str(entry.get("speaker", "")), speakers),
                text=text,
                kind=kind,  # type: ignore[arg-type]
                anchors=anchors,
                beat_id=beat_id,
            )
        )
    if not replacement:
        ctx.progress(f"note {note.id} produced no replacement segments; left unchanged")
        return script
    return Script(segments=[*script.segments[:start], *replacement, *script.segments[end:]])


def _apply_outline_note(
    outline: Outline, note: Note, inp: ApplyOutlineNotesInput, ctx: NodeContext
) -> Outline:
    if note.target.kind != "beat":
        ctx.progress(f"note {note.id} targets a segment; skipped on an outline")
        return outline
    index = next((i for i, beat in enumerate(outline.beats) if beat.id == note.target.id), None)
    if index is None:
        ctx.progress(f"note {note.id} has no matching beat; skipped")
        return outline

    beat = outline.beats[index]
    allowed = {block.id for block in inp.parsed.blocks} if inp.parsed else set(beat.block_ids)
    data = ctx.llm.complete(
        CompletionRequest(
            model=ctx.model("claude-opus-5"),
            system=str(ctx.get("system_prompt") or _OUTLINE_SYSTEM),
            messages=[Message(role="user", content=_outline_prompt(note, beat, sorted(allowed)))],
            max_tokens=int(ctx.get("max_tokens", 2000)),
            temperature=ctx.get("temperature"),
            json_schema=_OUTLINE_SCHEMA,
            cache_system=True,
        )
    ).json_payload()

    block_ids = [str(item) for item in data.get("block_ids", []) if str(item) in allowed]
    if not block_ids:
        block_ids = list(beat.block_ids)
    try:
        word_budget = max(1, int(data.get("word_budget", beat.word_budget)))
    except (TypeError, ValueError):
        word_budget = beat.word_budget

    revised = beat.model_copy(
        update={
            "title": str(data.get("title") or beat.title).strip() or beat.title,
            "summary": str(data.get("summary") or beat.summary or "").strip() or beat.summary,
            "block_ids": block_ids,
            "word_budget": word_budget,
        }
    )
    beats = list(outline.beats)
    beats[index] = revised
    return Outline(beats=beats)


def _format_segments(segments: list[Segment], mark: str | None = None) -> str:
    lines: list[str] = []
    for segment in segments:
        line = f"[{segment.id}] {segment.speaker} ({segment.kind}): {segment.text}"
        if mark and segment.id == mark:
            line += "  ← replace this"
        lines.append(line)
    return "\n\n".join(lines)


def _script_prompt(
    inp: ApplyNotesInput,
    note: Note,
    current: list[Segment],
    beat_blocks: list[Any],
    beat_id: str,
    context: list[Segment],
) -> str:
    speakers = "\n".join(
        f"- {speaker.name} ({speaker.role})"
        + (f": {speaker.voice_note}" if speaker.voice_note else "")
        for speaker in inp.format_spec.speakers
    )
    passages = "\n\n".join(f"[{block.id}]\n{block.llm_text()}" for block in beat_blocks)
    extra = ""
    if note.target.kind == "segment" and len(context) > len(current):
        extra = (
            "Surrounding beat (read for continuity; do not rewrite unmarked "
            "segments):\n"
            f"{_format_segments(context, mark=note.target.id)}\n\n"
        )
    return (
        f"Note ({note.source}"
        + (f", {note.criterion}" if note.criterion else "")
        + f"): {note.text}\n"
        f"Target: {note.target.kind} {note.target.id}\n"
        f"Beat: {beat_id}\n"
        f"Register: {inp.format_spec.register}\n"
        f"Document language: {inp.parsed.language}\n"
        f"Audience: {inp.audience_spec.description}\n\n"
        f"Speakers:\n{speakers}\n\n"
        f"{extra}"
        f"Current segments to replace:\n{_format_segments(current)}\n\n"
        f"Passages you may draw facts from:\n{passages or '(none)'}"
    )


def _outline_prompt(note: Note, beat: Any, allowed: list[str]) -> str:
    return (
        f"Note ({note.source}"
        + (f", {note.criterion}" if note.criterion else "")
        + f"): {note.text}\n"
        f"Beat id (keep this): {beat.id}\n"
        f"Current title: {beat.title}\n"
        f"Current summary: {beat.summary or ''}\n"
        f"Current block ids: {', '.join(beat.block_ids)}\n"
        f"Current word budget: {beat.word_budget}\n"
        f"Passage ids you may use: {', '.join(allowed)}"
    )


register_node(ApplyNotesNode())
register_node(ApplyOutlineNotesNode())
