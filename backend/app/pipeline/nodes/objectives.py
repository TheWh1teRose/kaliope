"""The ``objectives`` node.

Turns the audience's desired outcome into listener-facing objectives: what
should be different after the episode, each classified on Bloom's taxonomy.
The document is only a constraint — it names the material the episode can
honestly cover — not the source of the objectives.
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
    BLOOM_LEVELS,
    AudienceSpec,
    BloomLevel,
    ContentBudget,
    Objective,
    Objectives,
)

DEFAULT_MAX_OBJECTIVES = 5
#: Total characters of document text offered so the model can make objectives
#: concrete without turning this into a second selection pass.
DEFAULT_MAX_CONTEXT_CHARS = 8_000
MIN_BLOCK_CHARS = 80

_SYSTEM = """\
You write the learning objectives for a grounded audio episode.

An objective describes what changes for the listener after they have heard the
episode — a capability they did not have before, stated so it can be checked.
It is not a topic, a chapter title, or a summary of the source.

You are given a desired outcome. That is the only source. Derive every
objective from it. The document is there so you can make an objective concrete
and refuse ones the material cannot support; it is not a second source of
objectives. Do not invent a goal the desired outcome does not imply.

You are also given a time budget: how long the episode will actually last.
That is a hard constraint on what can change. Write only objectives a listener
can honestly reach in that time. A short episode cannot produce apply,
evaluate or create unless the desired outcome is already that narrow — and
even then, usually only one such objective. Prefer fewer objectives over a
list that would need a lecture. If the desired outcome is larger than the
time allows, keep the achievable core and leave the rest out; do not dilute
it into a catalogue of unfinishable goals.

Classify each objective on Bloom's taxonomy, using the lowest honest level:

- remember: recall a fact, term or sequence
- understand: explain, paraphrase or give the idea in their own words
- apply: use the idea in a familiar situation
- analyse: break a situation into parts and see how they relate
- evaluate: judge against a criterion
- create: produce something new from the ideas

Do not inflate the level. A short episode that should leave the listener able
to explain a mechanism is 'understand', not 'create'. Not every level needs to
appear. Prefer fewer, sharper objectives over a full taxonomy for its own sake.

Write the objectives in the language of the desired outcome, as change
statements (what the listener can do or explain afterwards).

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "bloom_level": {
                        "type": "string",
                        "enum": list(BLOOM_LEVELS),
                    },
                    "derivation": {"type": "string"},
                },
                "required": ["text", "bloom_level"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["objectives", "rationale"],
    "additionalProperties": False,
}

#: Spellings a model commonly returns that still name a real Bloom level.
_BLOOM_ALIASES: dict[str, BloomLevel] = {
    "remember": "remember",
    "knowledge": "remember",
    "understand": "understand",
    "comprehension": "understand",
    "apply": "apply",
    "application": "apply",
    "analyse": "analyse",
    "analyze": "analyse",
    "analysis": "analyse",
    "evaluate": "evaluate",
    "evaluation": "evaluate",
    "create": "create",
    "synthesis": "create",
}


class ObjectivesInput(BaseModel):
    parsed: ParsedDocument
    audience_spec: AudienceSpec
    budget: ContentBudget


class ObjectivesNode:
    name = "objectives"
    title = "Hörziele"
    version = "1.0"
    Input: type[BaseModel] = ObjectivesInput
    Output: type[BaseModel] = Objectives
    produces = "objectives"

    doc = NodeDoc(
        summary="Derives listener-facing objectives from the desired outcome and "
        "classifies each on Bloom's taxonomy.",
        detail=[
            "An objective is a change: what the listener can do or explain after the "
            "episode that they could not before. It is written as a checkable statement, "
            "not as a topic heading.",
            "The only source is the audience's desired outcome. The document is handed "
            "over as a constraint — section titles and a sample of narratable text — so "
            "an objective can name the actual material and so the model can refuse a "
            "change the source cannot support. Document-stated objectives, when present, "
            "are shown the same way: useful for concreteness, never a second source.",
            "The content budget is a second constraint: how many minutes the episode "
            "will actually last. Objectives the listener cannot reach in that time are "
            "left out, even when the desired outcome implies them. Ambition follows "
            "the clock, not the full wish list.",
            "Each objective is classified at the lowest honest Bloom level: remember, "
            "understand, apply, analyse, evaluate, create. Levels the desired outcome "
            "does not imply are omitted; the node does not fill the taxonomy for "
            "completeness.",
            "The reply is filtered, not trusted: empty texts are dropped, unknown Bloom "
            "labels are discarded (common aliases such as 'analyze' are snapped onto "
            "'analyse'), and the list is capped at the configured maximum. What survives "
            "is what the run publishes.",
        ],
        inputs={
            "audience_spec": "The desired outcome is the source of every objective. "
            "Description, prior knowledge, role and listening context set the pitch.",
            "parsed": "Language, section titles, a sample of narratable text, and any "
            "objectives the document states — used to make a change concrete, not to "
            "invent one.",
            "budget": "The settled episode length. Caps how ambitious the objectives "
            "may be — only changes a listener can reach in that time.",
        },
        output="An Objectives artifact: the desired outcome that was used, a rationale, "
        "and the list of objectives with Bloom level and derivation.",
        failure_modes=[
            "The audience spec has no desired outcome — there is nothing to derive from, "
            "so the node refuses rather than inventing goals from the document.",
            "The model returned no usable objective: empty texts, or Bloom labels that "
            "could not be snapped onto the taxonomy.",
        ],
        cost="One call. The document sample is small; cheaper than selection.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "What an objective is, how Bloom levels are assigned, and that the "
                "time budget caps ambition. Keep the rule that the desired outcome "
                "is the only source — otherwise this node collapses into a second "
                "learning-goal generator."
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
                "Left empty for models that do not take it. This is a classification "
                "and derivation task, so low is right when it is set at all."
            ),
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens",
            type="int",
            default=4000,
            minimum=500,
            maximum=16000,
            advanced=True,
        ),
        NodeParam(
            key="max_objectives",
            label="Maximum objectives",
            type="int",
            default=DEFAULT_MAX_OBJECTIVES,
            minimum=1,
            maximum=12,
            description=(
                "How many objectives to keep. Prefer a short list a listener can "
                "actually leave with over a full Bloom ladder."
            ),
        ),
        NodeParam(
            key="max_context_chars",
            label="Document sample in characters",
            type="int",
            default=DEFAULT_MAX_CONTEXT_CHARS,
            minimum=500,
            description=(
                "How much narratable text is shown so an objective can name the "
                "material. This is a constraint, not a second source."
            ),
            advanced=True,
        ),
    ]

    def run(self, inp: ObjectivesInput, ctx: NodeContext) -> Objectives:
        desired = (inp.audience_spec.desired_outcome or "").strip()
        if not desired:
            raise NodeError(
                "the audience spec has no desired outcome, so there is nothing to "
                "derive listener objectives from"
            )

        max_objectives = int(ctx.get("max_objectives", DEFAULT_MAX_OBJECTIVES))
        if max_objectives < 1:
            raise NodeError("max_objectives must be at least 1")

        user = _user_prompt(
            inp.audience_spec,
            inp.parsed,
            inp.budget,
            max_objectives,
            int(ctx.get("max_context_chars", DEFAULT_MAX_CONTEXT_CHARS)),
        )

        data = ctx.llm.complete(
            CompletionRequest(
                model=ctx.model("claude-opus-5"),
                system=str(ctx.get("system_prompt") or _SYSTEM),
                messages=[Message(role="user", content=user)],
                max_tokens=int(ctx.get("max_tokens", 4000)),
                temperature=ctx.get("temperature"),
                json_schema=_SCHEMA,
                cache_system=True,
            )
        ).json_payload()

        items: list[Objective] = []
        unknown_levels = 0
        for index, entry in enumerate(data.get("objectives", [])):
            if len(items) >= max_objectives:
                break
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            level = _bloom_level(entry.get("bloom_level"))
            if level is None:
                unknown_levels += 1
                continue
            derivation = str(entry.get("derivation") or "").strip() or None
            items.append(
                Objective(
                    id=str(entry.get("id") or f"o{index}").strip() or f"o{index}",
                    text=text,
                    bloom_level=level,
                    derivation=derivation,
                )
            )

        if unknown_levels:
            ctx.progress(f"discarded {unknown_levels} objective(s) with an unknown Bloom level")
        if not items:
            raise NodeError("the objectives step returned no usable listener objectives")

        return Objectives(
            items=items,
            desired_outcome=desired,
            rationale=str(data.get("rationale", "")).strip(),
        )


def _bloom_level(raw: Any) -> BloomLevel | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    return _BLOOM_ALIASES.get(key)


def _user_prompt(
    spec: AudienceSpec,
    parsed: ParsedDocument,
    budget: ContentBudget,
    max_objectives: int,
    max_context_chars: int,
) -> str:
    parts = [
        f"Desired outcome (the source — derive every objective from this):\n{spec.desired_outcome}",
        f"\nWrite at most {max_objectives} objectives.",
        (
            f"Time budget (constraint — only write objectives a listener can "
            f"reach in this time): {budget.target_minutes:.1f} minutes "
            f"(~{budget.target_words} words at {budget.words_per_minute} wpm)."
        ),
        f"Document language: {parsed.language}",
    ]
    audience = _audience_context(spec)
    if audience:
        parts.append(f"\nAudience context (pitch, not source):\n{audience}")

    if parsed.objectives:
        parts.append(
            "\nThe document states its own objectives. Use them only to make a "
            "desired-outcome objective concrete; do not copy them as a second list.\n"
            + "\n".join(f"- {o}" for o in parsed.objectives)
        )

    titles = [s.title for s in (parsed.sections or []) if s.title]
    if titles:
        parts.append("\nDocument sections:\n" + "\n".join(f"- {t}" for t in titles[:24]))

    sample = _document_sample(parsed, max_context_chars)
    if sample:
        parts.append(f"\nNarratable material (constraint, not source):\n{sample}")

    return "\n".join(parts)


def _audience_context(spec: AudienceSpec) -> str:
    parts: list[str] = []
    if spec.description.strip():
        parts.append(spec.description.strip())
    for label, value in (
        ("prior knowledge", spec.prior_knowledge),
        ("role", spec.role),
        ("listening context", spec.listening_context),
    ):
        if value and value.strip():
            parts.append(f"{label}: {value.strip()}")
    return "; ".join(parts)


def _document_sample(parsed: ParsedDocument, max_chars: int) -> str:
    candidates = parsed.narratable_blocks()
    if not candidates:
        return ""
    per_block = max(MIN_BLOCK_CHARS, max_chars // max(len(candidates), 1))
    lines: list[str] = []
    used = 0
    for block in candidates:
        text = block.llm_text()
        if len(text) > per_block:
            text = text[:per_block].rsplit(" ", 1)[0] + " …"
        line = f"[{block.id}] {text}"
        if used + len(line) > max_chars and lines:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


register_node(ObjectivesNode())
