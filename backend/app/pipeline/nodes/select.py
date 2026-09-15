"""The ``select`` node (§6.2).

Learning goals come from the document's own stated objectives when it has any,
and are generated otherwise. Both paths are first-class: the ``source`` field
records which one ran, and AC-BL-3 checks that the corpus exercises both.

The prompt shows each candidate block as an id, a weight and its text. No zone
is named in prose — the model receives generic labels and weights, so the
taxonomy can change without rewriting the prompt.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    ContentBudget,
    LearningGoal,
    SelectedBlock,
    Selection,
)

#: Total characters of block text offered to the model.
DEFAULT_MAX_PROMPT_CHARS = 400_000
#: Never truncate a block below this, or the model cannot judge it.
MIN_BLOCK_CHARS = 100

_SYSTEM = """\
You choose which passages of a source document a grounded audio episode should
be built from, and you state the learning goals the episode serves.

You are given candidate passages. Each has an id, a weight, and its text. A
higher weight means the author gave that passage more prominence; treat it as a
prior, not an instruction. Passages that must never be narrated have already
been removed, so everything you see is fair game.

Rules:
- Select enough material to support the target length with room to spare. The
  episode must be able to *choose* from more material than it emits.
- Select whole passages by id. Do not invent ids and do not paraphrase here.
- Prefer passages that explain, define, or give a worked example over passages
  that merely list or cross-reference.
- Every selected passage must serve at least one learning goal.
- Learning goals describe what a listener will be able to do or explain
  afterwards. Write them in the document's language.

Return JSON only.
"""


class SelectInput(BaseModel):
    parsed: ParsedDocument
    budget: ContentBudget
    audience_spec: AudienceSpec


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "learning_goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        },
        "selected_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "goal_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["block_id", "goal_ids"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["learning_goals", "selected_blocks", "rationale"],
    "additionalProperties": False,
}


class SelectNode:
    name = "select"
    title = "Auswahl und Lernziele"
    version = "1.0"
    Input: type[BaseModel] = SelectInput
    Output: type[BaseModel] = Selection
    produces = "selection"

    doc = NodeDoc(
        summary="Names the learning goals and picks the passages the episode will be built from.",
        detail=[
            "Offers the model every narratable block as an id, a weight and its text. The "
            "weight is the zone salience — how prominent the author made that passage. It is "
            "given as a prior, not an instruction, and no zone is ever named in prose, so the "
            "zone taxonomy can change without anyone rewriting this prompt.",
            "Learning goals take one of two paths. If the document states its own objectives, "
            "they are handed over and the goals are derived from them. If it states none, the "
            "goals are inferred from the material. Which path ran is recorded on every goal as "
            "'document' or 'generated'.",
            "The reply is filtered, not trusted: block ids that do not exist are discarded and "
            "counted, duplicates are dropped, and goal references that point at no goal are "
            "stripped. What survives is sorted back into document order.",
            "Blocks are truncated to fit the prompt budget, evenly across candidates rather "
            "than by dropping the tail — a long document loses detail everywhere instead of "
            "losing its last chapter entirely.",
        ],
        inputs={
            "parsed": "The narratable blocks, their salience, section titles and stated "
            "objectives.",
            "budget": "Target length and available material, so the model knows how much to "
            "select.",
            "audience_spec": "Who is listening, which shifts what counts as worth selecting.",
        },
        output="A Selection: learning goals, the chosen block ids with the goals each serves, "
        "and the rationale.",
        failure_modes=[
            "No narratable blocks at all — everything was classified as furniture, exercise or "
            "reference.",
            "The model returned no learning goals, or no block id that exists in the parse. "
            "Usually a sign the prompt was edited into something the model answers in prose.",
        ],
        cost="One call, with the whole candidate set in it. The largest single prompt in the "
        "flow; the system prompt is cached across runs.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "The selection policy. It must keep demanding whole passages by id and JSON "
                "output — the node discards anything else. This is the right place to change "
                "what makes a passage worth selecting."
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
                "Left empty for the models that do not take it. Selection is a judgement "
                "task, so low is right when it is set at all."
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
                "Total block text offered to the model. Above the model's context window the "
                "call fails; well below it, long documents get truncated passage by passage."
            ),
            advanced=True,
        ),
    ]

    def run(self, inp: SelectInput, ctx: NodeContext) -> Selection:
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

        objectives = inp.parsed.objectives
        goal_instruction = (
            "The document states its own objectives below. Derive the learning goals "
            "from them, staying close to the author's intent.\n\n"
            + "\n".join(f"- {o}" for o in objectives)
            if objectives
            else (
                "The document states no objectives of its own. Infer learning goals "
                "from the material you select."
            )
        )

        user = (
            f"Audience: {_audience_text(inp.audience_spec)}\n\n"
            f"Target length: {inp.budget.target_minutes:.1f} minutes "
            f"(~{inp.budget.target_words} words at {inp.budget.words_per_minute} wpm).\n"
            f"Available narratable material: {inp.budget.narratable_words} words.\n"
            f"Document language: {inp.parsed.language}\n\n"
            f"{goal_instruction}\n\n"
            f"Candidate passages ({len(payload)}):\n"
            + "\n".join(
                f"[{e['id']}] (weight {e['weight']})"
                + (f" section: {e['section']}" if e["section"] else "")
                + f"\n{e['text']}"
                for e in payload
            )
        )

        response = ctx.llm.complete(
            CompletionRequest(
                model=ctx.model("claude-opus-5"),
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=user)],
                max_tokens=int(ctx.get("max_tokens", 16000)),
                temperature=ctx.get("temperature"),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        )
        data = response.json_payload()

        source = "document" if objectives else "generated"
        goals = [
            LearningGoal(
                id=str(g.get("id") or f"g{index}"),
                text=str(g.get("text", "")).strip(),
                source=source,  # type: ignore[arg-type]
            )
            for index, g in enumerate(data.get("learning_goals", []))
            if str(g.get("text", "")).strip()
        ]
        if not goals:
            raise NodeError("the selection returned no learning goals")

        known = {b.id: b for b in candidates}
        goal_ids = {g.id for g in goals}
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
            selected.append(
                SelectedBlock(
                    block_id=block_id,
                    salience=block.salience,
                    reason=(entry.get("reason") or None),
                    goal_ids=[g for g in entry.get("goal_ids", []) if g in goal_ids],
                )
            )

        if unknown:
            ctx.progress(f"discarded {unknown} selected id(s) that do not exist")
        if not selected:
            raise NodeError("the selection returned no usable block ids")

        selected.sort(key=lambda s: known[s.block_id].ordinal)
        return Selection(
            learning_goals=goals,
            selected_blocks=selected,
            rationale=str(data.get("rationale", "")).strip(),
        )


def _candidate_payload(
    candidates: list[Any], section_titles: dict[str, str], max_chars: int
) -> tuple[list[dict[str, Any]], float]:
    per_block = max(MIN_BLOCK_CHARS, max_chars // max(len(candidates), 1))
    truncated = 0
    payload: list[dict[str, Any]] = []
    for block in candidates:
        text = block.llm_text()
        if len(text) > per_block:
            text = text[:per_block].rsplit(" ", 1)[0] + " …"
            truncated += 1
        payload.append(
            {
                "id": block.id,
                "weight": round(block.salience, 2),
                "section": section_titles.get(block.section_id or "") or None,
                "text": text,
            }
        )
    return payload, truncated / len(candidates) if candidates else 0.0


def _audience_text(spec: AudienceSpec) -> str:
    parts = [spec.description.strip()]
    for label, value in (
        ("prior knowledge", spec.prior_knowledge),
        ("role", spec.role),
        ("listening context", spec.listening_context),
        ("desired outcome", spec.desired_outcome),
    ):
        if value and value.strip():
            parts.append(f"{label}: {value.strip()}")
    return "; ".join(parts)


register_node(SelectNode())
