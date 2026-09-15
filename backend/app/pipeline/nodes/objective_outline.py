"""The ``objective_outline`` node.

Same job as ``outline`` — turn the selected passages into an ordered list of
beats — but the running order is planned against the listener objectives, not
the document's learning goals. Those goals are not generated here and are not
read off the selection. Each beat names the objective it serves.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.nodes.outline import _normalise_budgets
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    Beat,
    ContentBudget,
    FormatSpec,
    Objectives,
    Outline,
    Selection,
)

_SYSTEM = """\
You plan the running order of a grounded audio episode from the listener
objectives.

You are given the passages that were selected from a source document, the
listener objectives the episode must produce, and the format the episode will
take. Produce a sequence of beats.

Rules:
- Each beat covers one coherent idea and lists the ids of the passages it draws
  on. Only use ids from the list you are given.
- Each beat names the listener objective it serves. Use an id from the given
  list. Do not invent objectives and do not derive learning goals from the
  document.
- Every given objective should be served by at least one beat, unless the
  selected passages genuinely cannot support it.
- Allocate a word budget to each beat. The budgets must add up to the target
  word count. Give more words to higher Bloom levels when the material can
  carry them.
- Order the beats so a listener can follow them without prior knowledge: set up
  before payoff, general before specific. Prefer the order the objectives
  themselves imply over the order the document happens to use.
- Every selected passage should be covered by some beat unless it is genuinely
  redundant.
- Write titles and summaries in the document's language.

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "block_ids": {"type": "array", "items": {"type": "string"}},
                    "word_budget": {"type": "integer"},
                    "objective_id": {"type": "string"},
                },
                "required": ["title", "block_ids", "word_budget", "objective_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["beats"],
    "additionalProperties": False,
}


class ObjectiveOutlineInput(BaseModel):
    parsed: ParsedDocument
    selection: Selection
    budget: ContentBudget
    format_spec: FormatSpec
    objectives: Objectives


class ObjectiveOutlineNode:
    name = "objective_outline"
    title = "Ablaufplan nach Hörzielen"
    version = "1.0"
    Input: type[BaseModel] = ObjectiveOutlineInput
    Output: type[BaseModel] = Outline
    produces = "outline"

    doc = NodeDoc(
        summary="Turns the selected passages into an ordered list of beats, using the "
        "listener objectives as the only planning criterion.",
        detail=[
            "A beat is one coherent idea: a title, the passage ids it draws on, the "
            "listener objective it serves, and how many words it may spend. The script "
            "node later writes each beat separately, so this plan is what keeps a long "
            "episode coherent instead of drifting.",
            "The document's own learning goals are not shown and are not copied off the "
            "selection. What this node plans against is the listener-objective list "
            "published upstream, together with the passages already tagged as serving "
            "those objectives.",
            "Ordering is explicitly pedagogic — set up before payoff, general before "
            "specific — and prefers the order the objectives imply over the order the "
            "document happens to use, because the listener has no way to skip back.",
            "The returned budgets are then normalised, not taken as given. If their sum "
            "misses the target word count by more than ten percent, every beat is "
            "rescaled and the rounding remainder is absorbed into the largest beat so "
            "the total is exact. Models cannot be trusted to do this arithmetic.",
            "Beats whose passage ids do not exist in the parse are dropped before "
            "normalisation, and an objective id that points at no given objective is "
            "stripped. A hallucinated id costs a beat rather than corrupting the plan.",
        ],
        inputs={
            "parsed": "The block texts the beats are planned over.",
            "selection": "Which passages were chosen and which objectives each already "
            "serves. Learning goals on the selection are ignored.",
            "budget": "The target word count the beat budgets must sum to.",
            "format_spec": "Speakers, register and beat guidance for the chosen format.",
            "objectives": "The listener changes the running order must produce. Every "
            "beat is tagged with the id it serves.",
        },
        output="An Outline: ordered beats with title, block ids, word budget, the "
        "objective each serves, and a summary.",
        failure_modes=[
            "No listener objectives were published upstream.",
            "The selection contains no block id that exists in the parse — the two "
            "steps disagree about the document, which normally means the parse changed "
            "underneath.",
            "The model returned no beat with a usable block id.",
        ],
        cost="One call. The passages are truncated to 600 characters each here, so it "
        "is far cheaper than the selection call.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "How the running order is planned. Changing the ordering rules or what "
                "makes a beat is done here. Keep the rule that beats name a given "
                "listener objective — otherwise this node collapses into the document-"
                "goal outline. The word-budget instruction can stay or go; the node "
                "rescales the budgets afterwards either way."
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
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens",
            type="int",
            default=12000,
            minimum=1000,
            maximum=64000,
            advanced=True,
        ),
    ]

    def run(self, inp: ObjectiveOutlineInput, ctx: NodeContext) -> Outline:
        if not inp.objectives.items:
            raise NodeError("no listener objectives are available to plan against")

        blocks = {b.id: b for b in inp.parsed.blocks}
        selected = [s for s in inp.selection.selected_blocks if s.block_id in blocks]
        if not selected:
            raise NodeError("the selection contains no block ids that exist in the parse")

        objective_ids = {o.id for o in inp.objectives.items}
        selected_by_id = {s.block_id: s for s in selected}
        target_words = inp.budget.target_words
        passages = "\n".join(
            _passage_line(
                bid,
                blocks[bid].llm_text()[:600],
                selected_by_id[bid].goal_ids,
                objective_ids,
            )
            for bid in selected_by_id
        )
        objectives_block = "\n".join(
            f"- {o.id} [{o.bloom_level}]: {o.text}" for o in inp.objectives.items
        )

        user = (
            f"Format: {inp.format_spec.name}. "
            f"Speakers: {', '.join(f'{s.name} ({s.role})' for s in inp.format_spec.speakers)}. "
            f"Register: {inp.format_spec.register}.\n"
            + (
                f"Beat guidance: {inp.format_spec.beats_hint}\n"
                if inp.format_spec.beats_hint
                else ""
            )
            + f"Target length: {inp.budget.target_minutes:.1f} minutes "
            f"(~{target_words} words). The beat budgets must sum to {target_words}.\n"
            f"Document language: {inp.parsed.language}\n\n"
            f"Listener objectives — every beat must serve one of these, and no others:\n"
            f"{objectives_block}\n\n"
            f"Selected passages:\n{passages}"
        )

        data = ctx.llm.complete(
            CompletionRequest(
                model=ctx.model("claude-opus-5"),
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=user)],
                max_tokens=int(ctx.get("max_tokens", 12000)),
                temperature=ctx.get("temperature"),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        ).json_payload()

        beats: list[Beat] = []
        for index, entry in enumerate(data.get("beats", [])):
            ids = [str(b) for b in entry.get("block_ids", []) if str(b) in blocks]
            if not ids:
                continue
            raw_budget = entry.get("word_budget", 0)
            try:
                word_budget = max(0, int(raw_budget))
            except (TypeError, ValueError):
                word_budget = 0
            raw_objective = entry.get("objective_id") or entry.get("goal_id") or ""
            objective = str(raw_objective)
            beats.append(
                Beat(
                    id=f"beat{index:03d}",
                    title=str(entry.get("title", "")).strip() or f"Beat {index + 1}",
                    block_ids=ids,
                    word_budget=word_budget,
                    goal_id=objective if objective in objective_ids else None,
                    summary=(entry.get("summary") or None),
                )
            )

        if not beats:
            raise NodeError("the outline returned no beats with usable block ids")

        covered = {b.goal_id for b in beats if b.goal_id}
        missing = [o.id for o in inp.objectives.items if o.id not in covered]
        if missing:
            ctx.progress(f"{len(missing)} listener objective(s) have no beat")

        _normalise_budgets(beats, target_words, ctx)
        return Outline(beats=beats)


def _passage_line(block_id: str, text: str, tagged: list[str], objective_ids: set[str]) -> str:
    serves = [oid for oid in tagged if oid in objective_ids]
    suffix = f" (serves {', '.join(serves)})" if serves else ""
    return f"[{block_id}]{suffix} {text}"


register_node(ObjectiveOutlineNode())
