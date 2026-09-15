"""The ``script`` node (§6.4).

The model is given block ids alongside block text and is told to cite them. It
returns a quote per citation rather than character offsets, because a model
cannot count characters reliably; the node then *locates* that quote in the
block and turns it into an anchor. That keeps anchors exact without asking the
model to do arithmetic.

The node does not verify anchors — gate G1 does. What it does guarantee is that
every anchor it emits points into a block that exists.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import Anchor, Block, ParsedDocument
from app.schemas.pipeline import AudienceSpec, FormatSpec, Outline, Script, Segment

#: Shortest quote worth trying to locate; below this, matches are coincidental.
MIN_QUOTE_CHARS = 12

_SYSTEM = """\
You write one beat of a grounded audio episode.

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

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
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


class ScriptInput(BaseModel):
    parsed: ParsedDocument
    outline: Outline
    format_spec: FormatSpec
    audience_spec: AudienceSpec


class ScriptNode:
    name = "script"
    title = "Skript schreiben"
    version = "1.0"
    Input: type[BaseModel] = ScriptInput
    Output: type[BaseModel] = Script
    produces = "script"

    doc = NodeDoc(
        summary="Writes the spoken segments, one beat at a time, and turns every citation "
        "into an exact anchor into the source.",
        detail=[
            "Calls the model once per beat rather than once per episode. Each call sees only "
            "that beat's passages, its word budget and its position in the running order, so "
            "the model cannot borrow facts from material the beat does not cover.",
            "Segments come back in two kinds. A 'claim' carries facts and must cite the "
            "passage it took them from. A 'pedagogy' segment — an analogy, a transition, a "
            "question — is written freely but may introduce no new fact, and carries no "
            "citation. Gate G2 later checks that split held.",
            "Citations are requested as a quoted sentence, not as character offsets: a model "
            "cannot count characters reliably. The node then locates that quote in the block "
            "itself, tolerating whitespace and case differences, and builds the anchor from "
            "where it actually found it. A quote it cannot locate becomes an anchor spanning "
            "the whole cited block, and the count is reported as progress.",
            "Speaker names are snapped onto the format's declared speakers, so a "
            "capitalisation difference does not fail gate G8 across a whole run.",
            "This node does not verify its own anchors — gate G1 does. What it guarantees is "
            "that every anchor it emits points into a block that exists.",
        ],
        inputs={
            "parsed": "The block texts that facts may be drawn from, and their ids.",
            "outline": "The beats: order, titles, block ids and word budgets.",
            "format_spec": "Speakers with their voice notes, register, opening and closing "
            "guidance.",
            "audience_spec": "Who is listening, which sets the level of explanation.",
        },
        output="A Script: ordered segments with speaker, text, kind, beat id and anchors.",
        failure_modes=[
            "The format spec declares no speakers.",
            "No segment survived — every beat's passage ids were missing from the parse, or "
            "the model answered with empty text throughout.",
        ],
        cost="One call per beat: the most expensive step of the flow, and the one where the "
        "model choice matters most.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "The groundedness policy and the speaking style. This is the prompt that "
                "decides script quality. Keep the claim/pedagogy split and the demand for a "
                "quoted citation — gates G1 and G2 assume both, and dropping them turns "
                "verifiable output into prose that only looks sourced."
            ),
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
            default=0.7,
            minimum=0.0,
            maximum=2.0,
            description=(
                "The one place in the flow where some variation helps: this step writes "
                "speech. Ignored by models that do not accept sampling."
            ),
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens per beat",
            type="int",
            default=8000,
            minimum=1000,
            maximum=64000,
            advanced=True,
        ),
    ]

    def run(self, inp: ScriptInput, ctx: NodeContext) -> Script:
        blocks = {b.id: b for b in inp.parsed.blocks}
        speaker_names = inp.format_spec.speaker_names()
        if not speaker_names:
            raise NodeError("the format spec declares no speakers")

        segments: list[Segment] = []
        unlocated = 0
        beat_count = len(inp.outline.beats)

        for position, beat in enumerate(inp.outline.beats):
            ctx.progress(f"writing beat {position + 1} of {beat_count}: {beat.title}")
            beat_blocks = [blocks[bid] for bid in beat.block_ids if bid in blocks]
            if not beat_blocks:
                continue

            data = ctx.llm.complete(
                CompletionRequest(
                    model=ctx.model("claude-opus-5"),
                    system=str(ctx.get("system_prompt") or _SYSTEM),
                    messages=[
                        Message(
                            role="user",
                            content=_beat_prompt(inp, beat, beat_blocks, position, beat_count),
                        )
                    ],
                    max_tokens=int(ctx.get("max_tokens", 8000)),
                    temperature=ctx.get("temperature"),
                    json_schema=_SCHEMA,
                    cache_system=True,
                )
            ).json_payload()

            for entry in data.get("segments", []):
                text = str(entry.get("text", "")).strip()
                if not text:
                    continue
                kind = "claim" if str(entry.get("kind")) == "claim" else "pedagogy"
                anchors, missed = _resolve_citations(entry.get("citations", []), blocks, inp.parsed)
                unlocated += missed
                segments.append(
                    Segment(
                        id=f"{beat.id}-s{len(segments):04d}",
                        speaker=_closest_speaker(str(entry.get("speaker", "")), speaker_names),
                        text=text,
                        kind=kind,  # type: ignore[arg-type]
                        anchors=anchors,
                        beat_id=beat.id,
                    )
                )

        if not segments:
            raise NodeError("the script node produced no segments")
        if unlocated:
            ctx.progress(
                f"{unlocated} citation quote(s) could not be located exactly; those "
                "anchors span the whole cited block"
            )
        return Script(segments=segments)


def _beat_prompt(
    inp: ScriptInput, beat: Any, beat_blocks: list[Block], position: int, total: int
) -> str:
    speakers = "\n".join(
        f"- {s.name} ({s.role})" + (f": {s.voice_note}" if s.voice_note else "")
        for s in inp.format_spec.speakers
    )
    passages = "\n\n".join(f"[{b.id}]\n{b.llm_text()}" for b in beat_blocks)

    opening = ""
    if position == 0 and inp.format_spec.opening:
        opening = f"\nThis is the first beat. Opening guidance: {inp.format_spec.opening}\n"
    closing = ""
    if position == total - 1 and inp.format_spec.closing:
        closing = f"\nThis is the final beat. Closing guidance: {inp.format_spec.closing}\n"

    return (
        f"Beat {position + 1} of {total}: {beat.title}\n"
        + (f"Beat summary: {beat.summary}\n" if beat.summary else "")
        + f"Word budget for this beat: about {beat.word_budget} words.\n"
        f"Register: {inp.format_spec.register}\n"
        f"Document language: {inp.parsed.language}\n"
        f"Audience: {inp.audience_spec.description}\n"
        f"{opening}{closing}\n"
        f"Speakers:\n{speakers}\n\n"
        f"Passages you may draw facts from:\n{passages}"
    )


def _closest_speaker(requested: str, names: list[str]) -> str:
    """Map the model's speaker string onto a declared speaker.

    Gate G8 fails on an undeclared speaker, so a near-miss is corrected here
    rather than being allowed to fail a whole run on a capitalisation.
    """
    stripped = requested.strip()
    for name in names:
        if stripped.casefold() == name.casefold():
            return name
    for name in names:
        if name.casefold() in stripped.casefold() or stripped.casefold() in name.casefold():
            return name
    return stripped or names[0]


def _resolve_citations(
    citations: Any, blocks: dict[str, Block], parsed: ParsedDocument
) -> tuple[list[Anchor], int]:
    anchors: list[Anchor] = []
    unlocated = 0
    if not isinstance(citations, list):
        return anchors, 0

    for citation in citations:
        if not isinstance(citation, dict):
            continue
        block = blocks.get(str(citation.get("block_id", "")).strip())
        if block is None or not block.text:
            continue
        quote = str(citation.get("quote", "")).strip()
        span = locate_quote(block.text, quote)
        if span is None:
            unlocated += 1
            span = (0, len(block.text))
        anchors.append(
            Anchor(
                document_id=parsed.document_id,
                parse_version=parsed.parse_version,
                block_id=block.id,
                char_start=span[0],
                char_end=span[1],
            )
        )
    return anchors, unlocated


_WS = re.compile(r"\s+")


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Find ``quote`` in ``text``, tolerating whitespace differences."""
    if len(quote) < MIN_QUOTE_CHARS:
        return None

    index = text.find(quote)
    if index >= 0:
        return (index, index + len(quote))

    normalized, offsets = _normalize_with_offsets(text)
    needle = _WS.sub(" ", quote).strip()
    if not needle:
        return None
    position = normalized.find(needle)
    if position < 0:
        position = normalized.casefold().find(needle.casefold())
    if position < 0:
        return None
    start = offsets[position]
    end_index = min(position + len(needle), len(offsets) - 1)
    end = offsets[end_index]
    return (start, max(end, start + 1))


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed text plus a map back to original character offsets."""
    chars: list[str] = []
    offsets: list[int] = []
    previous_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if previous_space:
                continue
            chars.append(" ")
            offsets.append(index)
            previous_space = True
        else:
            chars.append(char)
            offsets.append(index)
            previous_space = False
    offsets.append(len(text))
    return "".join(chars), offsets


register_node(ScriptNode())
