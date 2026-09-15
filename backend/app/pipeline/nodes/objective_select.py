"""The ``objective_select`` node.

Same job as ``select`` — pick the passages the episode will be built from —
but the criterion is the listener objectives, not the document's own learning
goals. Those goals are not generated here and are not copied onto the
selection. The rationale is kept: it is what makes a selection reviewable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.nodes.select import (
    DEFAULT_MAX_PROMPT_CHARS,
    _audience_text,
    _candidate_payload,
)
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    ContentBudget,
    Objectives,
    SelectedBlock,
    Selection,
)

_SYSTEM = """\
You choose which passages of a source document serve the given listener
objectives.

You are given the objectives the episode must produce — what should be
different for the listener afterwards — and a set of candidate passages. Each
passage has an id, a weight, and its text. A higher weight means the author
gave that passage more prominence; treat it as a prior, not an instruction.
Passages that must never be narrated have already been removed, so everything
you see is fair game.

Rules:
- Select enough material to support the target length with room to spare. The
  episode must be able to *choose* from more material than it emits.
- Select whole passages by id. Do not invent ids and do not paraphrase here.
- Prefer passages that explain, define, or give a worked example over passages
  that merely list or cross-reference.
- Every selected passage must serve at least one of the given objectives.
  Name those objective ids. Do not invent objectives and do not derive
  learning goals from the document.
- A passage that does not help any objective stays out, even if it is
  prominent.

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "objective_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["block_id", "objective_ids"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["selected_blocks", "rationale"],
    "additionalProperties": False,
}


class ObjectiveSelectInput(BaseModel):
    parsed: ParsedDocument
    budget: ContentBudget
    audience_spec: AudienceSpec
    objectives: Objectives


class ObjectiveSelectNode:
    name = "objective_select"
    title = "Auswahl nach Hörzielen"
    version = "1.0"
    Input: type[BaseModel] = ObjectiveSelectInput
    Output: type[BaseModel] = Selection
    produces = "selection"

    doc = NodeDoc(
        summary="Picks the passages the episode will be built from, using the listener "
        "objectives as the only selection criterion.",
        detail=[
            "Offers the model every narratable block as an id, a weight and its text, "
            "together with the listener objectives produced upstream. A passage is "
            "worth selecting when it can help produce one of those changes; prominence "
            "is a prior, not an instruction.",
            "The document's own stated objectives are not shown and are not turned into "
            "learning goals. This node does not generate goals. What it publishes is "
            "the chosen block ids — each tagged with the objective ids it serves — and "
            "the rationale.",
            "The reply is filtered, not trusted: block ids that do not exist are "
            "discarded and counted, duplicates are dropped, and objective references "
            "that point at no objective are stripped. What survives is sorted back "
            "into document order.",
            "Blocks are truncated to fit the prompt budget, evenly across candidates "
            "rather than by dropping the tail — a long document loses detail everywhere "
            "instead of losing its last chapter entirely.",
        ],
        inputs={
            "parsed": "The narratable blocks, their salience and section titles. Document "
            "objectives are ignored.",
            "budget": "Target length and available material, so the model knows how much "
            "to select.",
            "audience_spec": "Who is listening, which shifts what counts as enough "
            "support for an objective.",
            "objectives": "The listener changes the selection must serve. Every chosen "
            "passage is tagged with the ids it supports.",
        },
        output="A Selection: the chosen block ids with the objectives each serves, and "
        "the rationale. Learning goals are empty — they are not this node's job.",
        failure_modes=[
            "No listener objectives were published upstream.",
            "No narratable blocks at all — everything was classified as furniture, "
            "exercise or reference.",
            "The model returned no block id that exists in the parse. Usually a sign "
            "the prompt was edited into something the model answers in prose.",
        ],
        cost="One call, with the whole candidate set in it. The largest single prompt "
        "in the flow; the system prompt is cached across runs.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "The selection policy. It must keep demanding whole passages by id, "
                "objective ids from the given list, and JSON output — the node discards "
                "anything else. Do not add a learning-goal instruction here."
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
            minimum=0.0,
            maximum=2.0,
            description=(
                "Left empty for the models that do not take it. Selection is a "
                "judgement task, so low is right when it is set at all."
            ),
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens",
            type="int",
            default=16000,
            minimum=1000,
            maximum=64000,
            advanced=True,
        ),
        NodeParam(
            key="max_prompt_chars",
            label="Prompt budget in characters",
            type="int",
            default=DEFAULT_MAX_PROMPT_CHARS,
            minimum=10000,
            description=(
                "Total block text offered to the model. Above the model's context "
                "window the call fails; well below it, long documents get truncated "
                "passage by passage."
            ),
            advanced=True,
        ),
    ]

    def run(self, inp: ObjectiveSelectInput, ctx: NodeContext) -> Selection:
        if not inp.objectives.items:
            raise NodeError("no listener objectives are available to select against")

        candidates = inp.parsed.narratable_blocks()
        if not candidates:
            raise NodeError("no narratable blocks are available to select from")

        section_titles = {s.id: s.title for s in (inp.parsed.sections or [])}
        payload, truncated_share = _candidate_payload(
            candidates, section_titles, int(ctx.get("max_prompt_chars", DEFAULT_MAX_PROMPT_CHARS))
        )
        if truncated_share > 0.5:
            ctx.progress(
                f"{truncated_share:.0%} of candidate passages were truncated for the prompt"
            )

        objective_ids = {o.id for o in inp.objectives.items}
        objectives_block = "\n".join(
            f"- {o.id} [{o.bloom_level}]: {o.text}" for o in inp.objectives.items
        )

        user = (
            f"Audience: {_audience_text(inp.audience_spec)}\n\n"
            f"Target length: {inp.budget.target_minutes:.1f} minutes "
            f"(~{inp.budget.target_words} words at {inp.budget.words_per_minute} wpm).\n"
            f"Available narratable material: {inp.budget.narratable_words} words.\n"
            f"Document language: {inp.parsed.language}\n\n"
            f"Listener objectives — select passages that serve these, and no others:\n"
            f"{objectives_block}\n\n"
            f"Candidate passages ({len(payload)}):\n"
            + "\n".join(
                f"[{e['id']}] (weight {e['weight']})"
                + (f" section: {e['section']}" if e["section"] else "")
                + f"\n{e['text']}"
                for e in payload
            )
        )

        data = ctx.llm.complete(
            CompletionRequest(
                model=ctx.model("claude-opus-5"),
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=user)],
                max_tokens=int(ctx.get("max_tokens", 16000)),
                temperature=ctx.get("temperature"),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        ).json_payload()

        known = {b.id: b for b in candidates}
        selected: list[SelectedBlock] = []
        seen: set[str] = set()
        unknown = 0
        for entry in data.get("selected_blocks", []):
            block_id = str(entry.get("block_id", "")).strip()
            block = known.get(block_id)
            if block is None:
                unknown += 1
                continue
            if block_id in seen:
                continue
            seen.add(block_id)
            raw_ids = entry.get("objective_ids") or entry.get("goal_ids") or []
            selected.append(
                SelectedBlock(
                    block_id=block_id,
                    salience=block.salience,
                    reason=(entry.get("reason") or None),
                    goal_ids=[str(g) for g in raw_ids if str(g) in objective_ids],
                )
            )

        if unknown:
            ctx.progress(f"discarded {unknown} selected id(s) that do not exist")
        if not selected:
            raise NodeError("the selection returned no usable block ids")

        selected.sort(key=lambda s: known[s.block_id].ordinal)
        return Selection(
            learning_goals=[],
            selected_blocks=selected,
            rationale=str(data.get("rationale", "")).strip(),
        )


register_node(ObjectiveSelectNode())
