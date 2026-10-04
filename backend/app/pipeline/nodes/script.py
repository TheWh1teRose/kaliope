"""The ``script`` node (§6.4).

The model is given block ids alongside block text and is told to cite them. It
returns a quote per citation rather than character offsets, because a model
cannot count characters reliably; the node then *locates* that quote in the
block and turns it into an anchor. That keeps anchors exact without asking the
model to do arithmetic.

The node does not verify anchors — gate G1 does. What it does guarantee is that
every anchor it emits points into a block that exists.

Beats are written in order, and each call sees the whole running order and the
text of every earlier beat, so the episode reads as one conversation. That
makes each beat depend on the ones before it, and the node caches them that
way: a beat's key chains the previous beat's key and result, so changing beat
k rewrites k and everything after it while the beats before it are reused.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.artifacts import hash_payload
from app.pipeline.framework.cancel import RunStopped
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import Anchor, Block, ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    Beat,
    FormatSpec,
    Outline,
    Script,
    Segment,
    SeriesContext,
)

#: Shortest quote worth trying to locate; below this, matches are coincidental.
MIN_QUOTE_CHARS = 12

_SYSTEM = (
    """\
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
"""
    "- Write speech, not prose: it will be heard once, not read twice. "
    "No bullet points, no headings, no markdown, no stage directions.\n"
    """\
- Keep it conversational, the speakers can interrupt, the conversation should feel human like.
- Use only the speakers you are given, by their exact names.
- Write in the document's language.
- Stay close to the word budget for this beat.

Continuity — the episode is one conversation:
- You see the whole running order and everything already written. Use them for
  continuity only. Facts still come only from this beat's passages; never
  restate a fact from the earlier text as a claim.
- Pick up where the previous beat ended. Answer its open question or bridge in
  one or two sentences. Do not recap it, do not repeat its lines, do not greet
  again.
- Reuse the examples, images and terms the earlier text already introduced
  when they fit. Do not bring in a second image for an idea that already has
  one, and do not explain a term again.
- Do not cover what a later beat covers. You may end with a short pointer
  towards the next beat.

Return JSON only.
"""
)

#: Longest stretch of the previous beat's ending that is quoted back to the model.
ENDING_CHARS = 400

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
    #: Set for one episode of a series: the plan, the other episodes' outlines
    #: and the full text of the earlier episodes.
    series_context: SeriesContext | None = None


class ScriptNode:
    name = "script"
    title = "Skript schreiben"
    version = "2.0"
    Input: type[BaseModel] = ScriptInput
    Output: type[BaseModel] = Script
    produces = "script"

    doc = NodeDoc(
        summary="Writes the spoken segments, one beat at a time, and turns every citation "
        "into an exact anchor into the source.",
        detail=[
            "Calls the model once per beat rather than once per episode, in order. Each call "
            "sees the whole running order, the full text of every earlier beat and how the "
            "previous beat ended, so it can pick up from there and keep the examples already "
            "chosen. Facts still come only from that beat's own passages: the earlier text is "
            "sent without block ids, for continuity only.",
            "Each beat is cached on its own, under a key that chains the previous beat's key "
            "and result. Changing a beat in the outline rewrites that beat and every later one "
            "and reuses the earlier ones; a run that failed half-way resumes at the beat that "
            "failed. Forcing the run ignores this cache too.",
            "The running order, the frame and the earlier text come first and only grow from "
            "beat to beat, so the provider can cache that prefix and later beats read it "
            "cheaply.",
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
            "series_context": "Only in a series: the plan, every other episode's running order "
            "and the full text of the earlier episodes, sent once at the top of every beat's "
            "message for continuity only.",
        },
        output="A Script: ordered segments with speaker, text, kind, beat id and anchors.",
        failure_modes=[
            "The format spec declares no speakers.",
            "No segment survived — every beat's passage ids were missing from the parse, or "
            "the model answered with empty text throughout.",
        ],
        cost="One call per beat that is not cached: the most expensive step of the flow, and "
        "the one where the model choice matters most. Later beats carry the earlier text, "
        "mostly read from the provider's prompt cache.",
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

        model = ctx.model("claude-opus-5")
        segments: list[Segment] = []
        written: list[tuple[int, Beat, list[Segment]]] = []
        unlocated = 0
        reused = 0
        beat_count = len(inp.outline.beats)
        previous_key = ""
        previous_result = ""

        for position, beat in enumerate(inp.outline.beats):
            if ctx.stopped():
                raise RunStopped()
            beat_blocks = [blocks[bid] for bid in beat.block_ids if bid in blocks]
            key = _beat_key(
                inp,
                ctx,
                model=model,
                beat=beat,
                position=position,
                is_last=position == beat_count - 1,
                beat_blocks=beat_blocks,
                previous_key=previous_key,
                previous_result=previous_result,
            )
            if not beat_blocks:
                previous_key = key
                continue

            cached = None if ctx.force else ctx.artifacts.get_step(key)
            if cached is not None:
                ctx.progress(f"reusing beat {position + 1} of {beat_count}: {beat.title}")
                payload = ctx.artifacts.get_raw(cached)
                reused += 1
            else:
                ctx.progress(f"writing beat {position + 1} of {beat_count}: {beat.title}")
                payload = self._write_beat(
                    inp, ctx, model, beat, beat_blocks, position, written, len(segments)
                )
                cached = ctx.artifacts.put_raw("script_beat", payload).hash
                ctx.artifacts.put_step(key, cached)

            beat_segments = [Segment.model_validate(item) for item in payload["segments"]]
            unlocated += int(payload.get("unlocated", 0))
            segments.extend(beat_segments)
            if beat_segments:
                written.append((position, beat, beat_segments))
            previous_key = key
            previous_result = cached

        if not segments:
            raise NodeError("the script node produced no segments")
        if reused:
            ctx.progress(f"{reused} beat(s) reused from an earlier run")
        if unlocated:
            ctx.progress(
                f"{unlocated} citation quote(s) could not be located exactly; those "
                "anchors span the whole cited block"
            )
        return Script(segments=segments)

    def _write_beat(
        self,
        inp: ScriptInput,
        ctx: NodeContext,
        model: str,
        beat: Beat,
        beat_blocks: list[Block],
        position: int,
        written: Sequence[tuple[int, Beat, Sequence[Segment]]],
        first_index: int,
    ) -> dict[str, Any]:
        """One model call for one beat; the segments as stored in the step cache."""
        blocks = {b.id: b for b in inp.parsed.blocks}
        speaker_names = inp.format_spec.speaker_names()
        content, breaks = _beat_message(inp, position, beat_blocks, written)
        data = ctx.llm.complete(
            CompletionRequest(
                model=model,
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=content, cache_breaks=breaks)],
                max_tokens=int(ctx.get("max_tokens", 8000)),
                temperature=ctx.get("temperature"),
                effort=ctx.request_effort(model),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        ).json_payload()

        segments: list[Segment] = []
        unlocated = 0
        for entry in data.get("segments", []):
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            kind = "claim" if str(entry.get("kind")) == "claim" else "pedagogy"
            anchors, missed = _resolve_citations(entry.get("citations", []), blocks, inp.parsed)
            unlocated += missed
            segments.append(
                Segment(
                    id=f"{beat.id}-s{first_index + len(segments):04d}",
                    speaker=_closest_speaker(str(entry.get("speaker", "")), speaker_names),
                    text=text,
                    kind=kind,  # type: ignore[arg-type]
                    anchors=anchors,
                    beat_id=beat.id,
                )
            )
        return {
            "segments": [segment.model_dump(mode="json") for segment in segments],
            "unlocated": unlocated,
        }


def _beat_key(
    inp: ScriptInput,
    ctx: NodeContext,
    *,
    model: str,
    beat: Beat,
    position: int,
    is_last: bool,
    beat_blocks: list[Block],
    previous_key: str,
    previous_result: str,
) -> str:
    """Cache key for one beat.

    It covers everything this beat's call depends on except the later beats:
    their titles appear in the running order, but changing them must not
    rewrite the beats before them. The previous beat's key and result chain the
    beats, so a change to any earlier beat reaches every later one.
    """
    payload: dict[str, Any] = {
        "node": ScriptNode.name,
        "version": ScriptNode.version,
        "config": ctx.config,
        "model": model,
        "format_spec": inp.format_spec.model_dump(mode="json"),
        "audience_spec": inp.audience_spec.model_dump(mode="json"),
        "document": [inp.parsed.document_id, inp.parsed.parse_version, inp.parsed.language],
        "beat": beat.model_dump(mode="json"),
        "position": position,
        "is_last": is_last,
        "passages": [[b.id, b.llm_text()] for b in beat_blocks],
        "previous_key": previous_key,
        "previous_result": previous_result,
    }
    if inp.series_context is not None:
        # Only present in a series, so the keys of a single episode are unchanged.
        payload["series_context"] = inp.series_context.model_dump(mode="json")
    return hash_payload(payload)


def beat_prompt_fields(
    inp: ScriptInput,
    position: int,
    beat_blocks: list[Block],
    written: Sequence[tuple[int, Beat, Sequence[Segment]]],
) -> dict[str, str]:
    """The named values the user message for one beat is built from.

    ``written`` holds the earlier beats that produced text, with their
    positions, in order. ``experiments/sources.BEAT_TEMPLATE`` renders these
    same fields, so an experiment sends exactly what production sends.
    """
    beats = inp.outline.beats
    total = len(beats)
    beat = beats[position]

    opening = ""
    if position == 0 and inp.format_spec.opening:
        opening = f"\nThis is the first beat. Opening guidance: {inp.format_spec.opening}\n"
    closing = ""
    if position == total - 1 and inp.format_spec.closing:
        closing = f"\nThis is the final beat. Closing guidance: {inp.format_spec.closing}\n"

    transition = ""
    if written:
        last = written[-1][2][-1]
        ending = last.text if len(last.text) <= ENDING_CHARS else "…" + last.text[-ENDING_CHARS:]
        transition += (
            f"\nThe previous beat ended with:\n{last.speaker}: {ending}\nPick up from there.\n"
        )
    if position < total - 1:
        upcoming = beats[position + 1]
        transition += (
            f"\nNext comes beat {position + 2}: {upcoming.title}. Leave its content to it.\n"
        )

    return {
        "running_order": _running_order(inp.outline),
        "register": inp.format_spec.register,
        "language": inp.parsed.language,
        "audience": inp.audience_spec.description,
        "speakers": "\n".join(
            f"- {s.name} ({s.role})" + (f": {s.voice_note}" if s.voice_note else "")
            for s in inp.format_spec.speakers
        ),
        "written_so_far": "".join(_written_blocks(written)),
        "beat_position": str(position + 1),
        "beat_total": str(total),
        "beat_title": beat.title,
        "beat_summary_line": f"Beat summary: {beat.summary}\n" if beat.summary else "",
        "word_budget": str(beat.word_budget),
        "opening_closing": f"{opening}{closing}",
        "transition": transition,
        "passages": "\n\n".join(f"[{b.id}]\n{b.llm_text()}" for b in beat_blocks),
    }


def _beat_message(
    inp: ScriptInput,
    position: int,
    beat_blocks: list[Block],
    written: Sequence[tuple[int, Beat, Sequence[Segment]]],
) -> tuple[str, list[int]]:
    """The user message for one beat, and where its stable stretches end.

    The frame (running order, register, audience, speakers) is the same for
    every beat, and each earlier beat's text is appended as its own stretch,
    so every call's prefix is the previous call's prefix plus one beat. The
    beat-specific part comes last.
    """
    fields = beat_prompt_fields(inp, position, beat_blocks, written)
    stable = [_frame(fields), *_written_blocks(written)]
    if inp.series_context is not None:
        stable.insert(0, series_section(inp.series_context))
    breaks: list[int] = []
    offset = 0
    for part in stable:
        offset += len(part)
        breaks.append(offset)
    return "".join(stable) + _this_beat(fields), breaks


def series_section(context: SeriesContext) -> str:
    """The series around this episode, the same for every beat of it.

    It comes first in the message, so it sits in the cached prefix of every beat.
    The earlier episodes' text carries no block ids: it is for continuity only.
    """
    total = len(context.episodes)
    lines = [f'Series: "{context.series_title}", episode {context.episode_index} of {total}.']
    if context.through_line:
        lines.append(f"Through-line: {context.through_line}")
    if context.terms:
        lines.append("Terms of the series (use these names; introduced in the episode given):")
        lines.extend(
            f"- {t.term} (episode {t.first_episode})" + (f": {t.gloss}" if t.gloss else "")
            for t in context.terms
        )
    lines.append("Episodes:")
    for episode in context.episodes:
        marker = "▶ " if episode.index == context.episode_index else "  "
        lines.append(
            f"{marker}{episode.index}. {episode.title}"
            + (f" ({episode.role})" if episode.role else "")
            + (f": {episode.summary}" if episode.summary else "")
            + (" (this episode)" if episode.index == context.episode_index else "")
        )
    for outline in context.outlines:
        lines.append(f"\nRunning order of episode {outline.index}: {outline.title}")
        lines.extend(
            f"  {i + 1}. {title}" + (f": {summary}" if summary else "")
            for i, (title, summary) in enumerate(outline.beats)
        )
    if context.earlier:
        lines.append(
            "\nEarlier episodes in full, for continuity only. Do not cite them and take no "
            "facts from them; facts come only from this beat's passages."
        )
        for earlier in context.earlier:
            lines.append(f"[Episode {earlier.index}: {earlier.title}]")
            lines.extend(earlier.lines)
    lines.append(
        "\nSeries rules:\n"
        "- Refer back to earlier episodes by name where it helps. Do not explain again what "
        "an earlier episode covered; a short recap belongs in this episode's first beat only.\n"
        "- Do not cover what a later episode covers. The last beat may point ahead to the "
        "next episode.\n"
        "- Use the series' terms, not synonyms.\n"
        "- The opening guidance opens this episode: after the first episode, open with a "
        "short recap instead of introducing the subject anew. Only the last episode closes "
        "the whole series."
    )
    return "\n".join(lines) + "\n\n"


def _frame(fields: dict[str, str]) -> str:
    return (
        f"{fields['running_order']}\n"
        f"Register: {fields['register']}\n"
        f"Document language: {fields['language']}\n"
        f"Audience: {fields['audience']}\n\n"
        f"Speakers:\n{fields['speakers']}\n\n"
    )


def _this_beat(fields: dict[str, str]) -> str:
    return (
        f"▶ You are writing beat {fields['beat_position']} of {fields['beat_total']}: "
        f"{fields['beat_title']}\n"
        f"{fields['beat_summary_line']}"
        f"Word budget for this beat: about {fields['word_budget']} words.\n"
        f"{fields['opening_closing']}{fields['transition']}\n"
        f"Passages you may draw facts from:\n{fields['passages']}"
    )


def _running_order(outline: Outline) -> str:
    total_words = sum(beat.word_budget for beat in outline.beats)
    lines = [f"Episode running order ({len(outline.beats)} beats, about {total_words} words):"]
    for index, beat in enumerate(outline.beats):
        lines.append(f"{index + 1}. {beat.title} · {beat.word_budget} words")
        if beat.summary:
            lines.append(f"   {beat.summary}")
    return "\n".join(lines) + "\n"


def _written_blocks(written: Sequence[tuple[int, Beat, Sequence[Segment]]]) -> list[str]:
    """Earlier beats as plain dialogue, one stretch per beat, without block ids."""
    parts: list[str] = []
    for index, (position, beat, beat_segments) in enumerate(written):
        header = (
            "Written so far, for continuity only. Do not cite it; facts must still come "
            "from this beat's passages.\n\n"
            if index == 0
            else ""
        )
        lines = "\n".join(f"{segment.speaker}: {segment.text}" for segment in beat_segments)
        parts.append(f"{header}[Beat {position + 1}: {beat.title}]\n{lines}\n\n")
    return parts


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
