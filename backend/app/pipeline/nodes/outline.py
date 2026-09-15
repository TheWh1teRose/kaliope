"""The ``outline`` node (§6.3).

Each beat carries a title, the block ids it covers, an allocated word budget and
the learning goal it serves. The node normalises the returned budgets so that
their sum lands within ±10% of the target word count, which §6.3 requires and
which the model cannot be trusted to do arithmetically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import Beat, ContentBudget, FormatSpec, Outline, Selection

#: Allowed deviation of the summed beat budgets from the target (§6.3).
BUDGET_TOLERANCE = 0.10

_SYSTEM = """\
You plan the running order of a grounded audio episode.

You are given the passages that were selected from a source document, the
learning goals the episode serves, and the format the episode will take. Produce
a sequence of beats.

Rules:
- Each beat covers one coherent idea and lists the ids of the passages it draws
  on. Only use ids from the list you are given.
- Each beat names the learning goal it serves.
- Allocate a word budget to each beat. The budgets must add up to the target
  word count.
- Order the beats so a listener can follow them without prior knowledge: set up
  before payoff, general before specific.
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
                    "goal_id": {"type": "string"},
                },
                "required": ["title", "block_ids", "word_budget"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["beats"],
    "additionalProperties": False,
}


class OutlineInput(BaseModel):
    parsed: ParsedDocument
    selection: Selection
    budget: ContentBudget
    format_spec: FormatSpec


class OutlineNode:
    name = "outline"
    title = "Ablaufplan"
    version = "1.0"
    Input: type[BaseModel] = OutlineInput
    Output: type[BaseModel] = Outline
    produces = "outline"

    doc = NodeDoc(
        summary="Turns the selected passages into an ordered list of beats, each with its own "
        "word budget.",
        detail=[
            "A beat is one coherent idea: a title, the passage ids it draws on, the learning "
            "goal it serves, and how many words it may spend. The script node later writes "
            "each beat separately, so this plan is what keeps a long episode coherent instead "
            "of drifting.",
            "Ordering is explicitly pedagogic — set up before payoff, general before specific "
            "— because the listener has no way to skip back.",
            "The returned budgets are then normalised, not taken as given. If their sum misses "
            "the target word count by more than ten percent, every beat is rescaled and the "
            "rounding remainder is absorbed into the largest beat so the total is exact. "
            "Models cannot be trusted to do this arithmetic, and §6.3 requires the tolerance "
            "to hold.",
            "Beats whose passage ids do not exist in the parse are dropped before "
            "normalisation, so a hallucinated id costs a beat rather than corrupting the plan.",
        ],
        inputs={
            "parsed": "The block texts the beats are planned over.",
            "selection": "Which passages were chosen and the learning goals they serve.",
            "budget": "The target word count the beat budgets must sum to.",
            "format_spec": "Speakers, register and beat guidance for the chosen format.",
        },
        output="An Outline: ordered beats with title, block ids, word budget, goal and summary.",
        failure_modes=[
            "The selection contains no block id that exists in the parse — the two steps "
            "disagree about the document, which normally means the parse changed underneath.",
            "The model returned no beat with a usable block id.",
        ],
        cost="One call. The passages are truncated to 600 characters each here, so it is far "
        "cheaper than the selection call.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "How the running order is planned. Changing the ordering rules or what makes "
                "a beat is done here. The word-budget instruction can stay or go — the node "
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

    def run(self, inp: OutlineInput, ctx: NodeContext) -> Outline:
        blocks = {b.id: b for b in inp.parsed.blocks}
        selected_ids = [s.block_id for s in inp.selection.selected_blocks if s.block_id in blocks]
        if not selected_ids:
            raise NodeError("the selection contains no block ids that exist in the parse")

        target_words = inp.budget.target_words
        passages = "\n".join(f"[{bid}] {blocks[bid].llm_text()[:600]}" for bid in selected_ids)
        goals = "\n".join(f"- {g.id}: {g.text}" for g in inp.selection.learning_goals)

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
            f"Learning goals:\n{goals}\n\n"
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

        goal_ids = {g.id for g in inp.selection.learning_goals}
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
            goal = str(entry.get("goal_id") or "")
            beats.append(
                Beat(
                    id=f"beat{index:03d}",
                    title=str(entry.get("title", "")).strip() or f"Beat {index + 1}",
                    block_ids=ids,
                    word_budget=word_budget,
                    goal_id=goal if goal in goal_ids else None,
                    summary=(entry.get("summary") or None),
                )
            )

        if not beats:
            raise NodeError("the outline returned no beats with usable block ids")

        _normalise_budgets(beats, target_words, ctx)
        return Outline(beats=beats)


def _normalise_budgets(beats: list[Beat], target_words: int, ctx: NodeContext) -> None:
    """Scale the beat budgets so their sum is within ±10% of the target (§6.3)."""
    total = sum(b.word_budget for b in beats)
    if total <= 0:
        even = max(1, target_words // len(beats))
        for beat in beats:
            beat.word_budget = even
        total = even * len(beats)

    deviation = abs(total - target_words) / target_words if target_words else 0.0
    if deviation <= BUDGET_TOLERANCE:
        return

    ctx.progress(f"beat budgets summed to {total} against a target of {target_words}; rescaling")
    scale = target_words / total
    for beat in beats:
        beat.word_budget = max(1, int(round(beat.word_budget * scale)))

    # Absorb the rounding remainder in the largest beat so the sum is exact.
    drift = target_words - sum(b.word_budget for b in beats)
    if drift:
        largest = max(beats, key=lambda b: b.word_budget)
        largest.word_budget = max(1, largest.word_budget + drift)


register_node(OutlineNode())
