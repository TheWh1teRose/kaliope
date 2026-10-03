"""The ``series_plan`` node: split one document into a series of episodes.

The model decides which passages and learning goals belong to which episode,
and gives the series a title, a through-line and its key terms. Everything
arithmetic is done here afterwards, never trusted to the model: unknown ids are
dropped, every narratable block gets at most one home, blocks the model did not
mention join a neighbour only while that episode stays within its source budget,
and an episode the model filled past that budget keeps the passages and is
named in a warning. Each episode's supportable length is measured from its own
passages, and an episode that cannot carry three minutes is merged into its
neighbour. Episodes
keep the requested length, so a higher count spreads the source instead of
shortening every episode. If that plan does not have the asked-for number of
episodes, the model is asked once more; a count that still differs is kept.

The planner writes no facts. Titles, summaries and goals describe the material;
the episodes' scripts still take every fact from their own passages.
"""

from __future__ import annotations

import math
from typing import Any, cast

from pydantic import BaseModel

from app.lang.resources import words_per_minute
from app.llm.base import CompletionRequest, Message
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.pipeline.nodes.content_budget import MIN_VIABLE_MINUTES, source_word_budget
from app.schemas.document import Block, ParsedDocument
from app.schemas.pipeline import (
    AudienceSpec,
    ContentBudget,
    EpisodePlan,
    FormatSpec,
    LearningGoal,
    SeriesBudget,
    SeriesPlan,
    SeriesRequest,
    SeriesTerm,
    UnassignedBlock,
)

#: Upper bound on the number of episodes, whoever chooses it.
MAX_EPISODES = 8
#: Fewest episodes a series has; one episode is a normal run.
MIN_EPISODES = 2
#: How many earlier passages one episode may cite again for a recap.
MAX_RECAP_BLOCKS = 3
#: Characters of each passage shown to the planner.
PASSAGE_CHARS = 600
#: Why a passage the model skipped stays out once an episode is full.
LEFT_OUT_FOR_DIALOGUE = "Left out to leave room for dialogue."

_SYSTEM = """\
You split a source document into a series of grounded audio episodes.

You are given the document's passages grouped by section. Each passage has an
id, a weight, its word count and the start of its text. A higher weight means
the author gave it more prominence.

Rules:
- Plan exactly the number of episodes you are asked for.
- Every episode needs room for dialogue, explanation and questions. It develops
  a few topics; it does not read its passages down or rush through them.
- Give each episode about the source-word budget in the user message, by
  passage id. Prefer fewer, richer topics. Keep related passages together and
  keep the document's order where it teaches well: set up before payoff,
  general before specific.
- Keep each episode at the length you were asked for. When more episodes are
  asked for, give each episode fewer passages so the material spreads and each
  episode still has room to talk.
- List passages that do not fit an episode's source budget under unassigned
  with a reason. Never invent ids.
- Each episode has a title, a role in the series (for example introduction,
  core, deepening, application, closing), a one-sentence summary and two to
  four learning goals that say what a listener can do or explain afterwards.
- For episodes after the first, name what to recall briefly from earlier
  episodes (recap) and up to three earlier passage ids that the recap may cite
  (recap_block_ids). For every episode but the last, name what the next episode
  will answer (preview).
- Give the series a title and a through-line: one or two sentences on how the
  episodes build on each other.
- List the key terms with a short gloss and the episode that introduces each
  one, so every episode uses the same name for the same idea.
- Do not state facts of your own. Titles, summaries and goals describe the
  material; they do not add to it.
- Write in the document's language.

Return JSON only.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "through_line": {"type": "string"},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "gloss": {"type": "string"},
                    "first_episode": {"type": "integer"},
                },
                "required": ["term", "first_episode"],
                "additionalProperties": False,
            },
        },
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "role": {"type": "string"},
                    "summary": {"type": "string"},
                    "block_ids": {"type": "array", "items": {"type": "string"}},
                    "recap_block_ids": {"type": "array", "items": {"type": "string"}},
                    "goals": {"type": "array", "items": {"type": "string"}},
                    "objectives": {"type": "array", "items": {"type": "integer"}},
                    "recap": {"type": "string"},
                    "preview": {"type": "string"},
                },
                "required": ["title", "block_ids", "goals"],
                "additionalProperties": False,
            },
        },
        "unassigned": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"block_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["block_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "through_line", "episodes"],
    "additionalProperties": False,
}


class SeriesPlanInput(BaseModel):
    parsed: ParsedDocument
    budget: ContentBudget
    format_spec: FormatSpec
    audience_spec: AudienceSpec
    series_request: SeriesRequest


class SeriesPlanNode:
    name = "series_plan"
    title = "Folgen planen"
    version = "1.1"
    Input: type[BaseModel] = SeriesPlanInput
    Output: type[BaseModel] = SeriesPlan
    produces = "series_plan"

    doc = NodeDoc(
        summary="Splits the document into episodes with room for dialogue, and decides what "
        "each episode is about.",
        detail=[
            "The number of episodes is either the one the run asked for or, when it asked "
            "for none, as many as the content budget carries at the requested length (at "
            "least two, at most eight).",
            "The model sees every narratable passage, grouped by section, with its id, "
            "weight, word count and the start of its text. It returns the episodes with "
            "their passages, learning goals, role, recap and preview, plus the series title, "
            "through-line and key terms. If that answer does not have the asked-for number "
            "of episodes, it is asked once more; a count that still differs is kept.",
            "The answer is then checked, not trusted: unknown ids are dropped, a passage "
            "named twice keeps its first episode, and passages the model did not mention "
            "join a neighbour only while that episode stays within its source-word budget. "
            "The rest stay unassigned, so an episode cannot be filled past the point where "
            "dialogue still fits. An episode the model filled past that budget keeps its "
            "passages and is named in a warning. An episode below three supportable minutes "
            "is merged into its smaller neighbour. Episodes keep the requested length.",
            "The planner writes no facts. Each episode later selects, outlines and writes "
            "from its own passages through the normal nodes.",
        ],
        inputs={
            "parsed": "The passages and sections to split.",
            "budget": "How many minutes the whole document supports.",
            "format_spec": "The format every episode will take.",
            "audience_spec": "Who is listening.",
            "series_request": "Number of episodes (or none), minutes per episode and an "
            "optional hint on how to split.",
        },
        output="A SeriesPlan: title, through-line, terms, the episodes with their passages "
        "and goals, the passages left out on purpose, the budget verdict, and a warning "
        "for each episode that carries more source words than its budget.",
        failure_modes=[
            "The document supports fewer than two episodes of three minutes — verdict "
            "'insufficient'; a single episode fits better.",
            "The model returned no episode with a passage that exists.",
        ],
        cost="One call over a shortened view of the document, or a second when the "
        "episode count does not match: about as expensive as the outline step, far "
        "cheaper than one script.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=_SYSTEM,
            description=(
                "How the split is decided. It must keep asking for passages by id and for "
                "JSON; the node discards anything else."
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
            default=16000,
            minimum=1000,
            maximum=64000,
            advanced=True,
        ),
    ]

    def run(self, inp: SeriesPlanInput, ctx: NodeContext) -> SeriesPlan:
        request = inp.series_request
        minutes = max(1, int(request.minutes_per_episode))
        supportable = float(inp.budget.max_supportable_minutes)
        count = episode_count(supportable, minutes, request.episodes)
        if supportable / count < MIN_VIABLE_MINUTES:
            raise NodeError(
                f"The document supports about {supportable:.1f} minutes, which is under "
                f"{MIN_VIABLE_MINUTES:.0f} minutes for each of {count} episodes. A single "
                "episode fits better.",
                verdict="insufficient",
            )

        candidates = inp.parsed.narratable_blocks()
        if not candidates:
            raise NodeError("no narratable blocks are available to split")

        def ask(note: str | None) -> dict[str, Any]:
            message = _user_message(inp, candidates, count, minutes)
            if note:
                message = f"{note}\n\n{message}"
            return cast(
                dict[str, Any],
                ctx.llm.complete(
                    CompletionRequest(
                        model=ctx.model("claude-opus-5"),
                        system=str(ctx.get("system_prompt") or _SYSTEM),
                        messages=[Message(role="user", content=message)],
                        max_tokens=int(ctx.get("max_tokens", 16000)),
                        temperature=ctx.get("temperature"),
                        json_schema=_SCHEMA,
                        cache_system=True,
                    )
                ).json_payload(),
            )

        data = ask(None)
        plan = build_plan(
            data,
            parsed=inp.parsed,
            budget=inp.budget,
            minutes=minutes,
            count=count,
        )
        if len(plan.episodes) != count:
            answered = len(plan.episodes)
            data = ask(
                f"Your previous answer planned {answered} episodes. Plan exactly {count} episodes."
            )
            plan = build_plan(
                data,
                parsed=inp.parsed,
                budget=inp.budget,
                minutes=minutes,
                count=count,
            )
        if len(plan.episodes) < count:
            ctx.progress(f"planned {len(plan.episodes)} of {count} episodes")
        return plan


def episode_count(supportable: float, minutes: int, requested: int | None) -> int:
    """How many episodes the series gets.

    A requested count is kept within the bounds. Otherwise the count is what the
    budget carries at the requested length, never fewer than two: a series of
    one is a normal run.
    """
    if requested:
        return max(MIN_EPISODES, min(MAX_EPISODES, int(requested)))
    return max(MIN_EPISODES, min(MAX_EPISODES, math.floor(supportable / max(minutes, 1))))


def _user_message(inp: SeriesPlanInput, candidates: list[Block], count: int, minutes: int) -> str:
    request = inp.series_request
    section_titles = {s.id: s.title for s in (inp.parsed.sections or [])}
    lines: list[str] = []
    current: str | None = "\0"
    for block in candidates:
        if block.section_id != current:
            current = block.section_id
            title = section_titles.get(current or "") or "(no section)"
            lines.append(f"\n## {title}")
        text = block.llm_text()
        if len(text) > PASSAGE_CHARS:
            text = text[:PASSAGE_CHARS].rsplit(" ", 1)[0] + " …"
        lines.append(
            f"[{block.id}] (weight {round(block.salience, 2)}, {block.word_count()} words)\n{text}"
        )

    objectives = inp.parsed.objectives
    objective_text = (
        "The document states these objectives; list the numbers each episode serves:\n"
        + "\n".join(f"{i + 1}. {o}" for i, o in enumerate(objectives))
        if objectives
        else "The document states no objectives of its own."
    )
    hint_text = (request.hint or "").strip()
    hint = f"Guidance on the split: {hint_text}\n" if hint_text else ""
    total_words = sum(block.word_count() for block in candidates)
    expansion = inp.budget.dialogue_expansion
    wpm = inp.budget.words_per_minute
    per_episode_cap = source_word_budget(minutes, wpm, expansion)
    even_share = max(1, total_words // max(count, 1))
    target_source = min(per_episode_cap, even_share)
    return (
        f"Plan {count} episodes of about {minutes} minutes each.\n"
        f"Each episode should use about {target_source} words of source material "
        f"so the {minutes} minutes have room for dialogue, explanation and questions. "
        f"{per_episode_cap} words is the most one episode should carry; "
        f"the document's {total_words} narratable words spread across {count} episodes "
        f"come to about {even_share} words each. Prefer the smaller figure, and prefer "
        f"fewer, richer topics. Passages beyond that stay unassigned.\n"
        f"Format: {inp.format_spec.name}. "
        f"Speakers: {', '.join(f'{s.name} ({s.role})' for s in inp.format_spec.speakers)}.\n"
        f"Audience: {inp.audience_spec.description}\n"
        f"Document language: {inp.parsed.language}\n"
        f"{hint}\n"
        f"{objective_text}\n\n"
        f"Passages ({len(candidates)}):" + "\n".join(lines)
    )


def build_plan(
    data: dict[str, Any],
    *,
    parsed: ParsedDocument,
    budget: ContentBudget,
    minutes: int,
    count: int,
) -> SeriesPlan:
    """Turn the model's answer into a plan.

    ``count`` is how many episodes were asked for; it is recorded and nothing is
    dropped or invented to match it. An episode under three minutes is merged
    into a neighbour. Forgotten passages join a neighbour only while it stays
    within the source-word budget for ``minutes``. An episode that still holds
    more source words than that budget keeps them and records a warning.
    """
    candidates = parsed.narratable_blocks()
    known = {b.id: b for b in candidates}
    order = {b.id: index for index, b in enumerate(candidates)}

    raw_episodes = [e for e in data.get("episodes", []) if isinstance(e, dict)][:MAX_EPISODES]
    homes: dict[str, int] = {}
    drafts: list[dict[str, Any]] = []
    for entry in raw_episodes:
        ids: list[str] = []
        for value in entry.get("block_ids", []):
            block_id = str(value).strip()
            if block_id in known and block_id not in homes:
                homes[block_id] = len(drafts)
                ids.append(block_id)
        drafts.append({"entry": entry, "ids": ids})

    unassigned: list[UnassignedBlock] = []
    for item in data.get("unassigned", []) or []:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id", "")).strip()
        if block_id in known and block_id not in homes:
            unassigned.append(
                UnassignedBlock(block_id=block_id, reason=str(item.get("reason", "")).strip())
            )
    left_out = {u.block_id for u in unassigned}

    # Passages nobody mentioned join a neighbour only while that episode stays
    # inside its source budget. Anything more would turn the episode into a
    # read-through. The model's own assignments are kept even when they run over;
    # each such episode is named in the plan's warnings.
    wpm = words_per_minute(parsed.language)
    expansion = budget.dialogue_expansion
    cap = source_word_budget(minutes, wpm, expansion)

    def _words(ids: list[str]) -> int:
        return sum(known[block_id].word_count() for block_id in ids)

    def _absorb(home: int, block_ids: list[str]) -> list[str]:
        left: list[str] = []
        for block_id in block_ids:
            if _words(drafts[home]["ids"]) + known[block_id].word_count() <= cap:
                homes[block_id] = home
                drafts[home]["ids"].append(block_id)
            else:
                left.append(block_id)
        return left

    def _leave_out(block_ids: list[str]) -> None:
        for block_id in block_ids:
            unassigned.append(UnassignedBlock(block_id=block_id, reason=LEFT_OUT_FOR_DIALOGUE))

    if any(d["ids"] for d in drafts):
        last_home: int | None = None
        pending: list[str] = []
        for block in candidates:
            if block.id in homes:
                if pending and last_home is None:
                    pending = _absorb(homes[block.id], pending)
                _leave_out(pending)
                pending = []
                last_home = homes[block.id]
            elif block.id not in left_out:
                if last_home is None:
                    pending.append(block.id)
                else:
                    pending.extend(_absorb(last_home, [block.id]))
        if last_home is not None:
            pending = _absorb(last_home, pending)
        _leave_out(pending)

    drafts = [d for d in drafts if d["ids"]]
    if not drafts:
        raise NodeError("the series plan returned no episode with passages that exist")
    for draft in drafts:
        draft["ids"].sort(key=lambda block_id: order[block_id])

    def supportable(ids: list[str]) -> float:
        words = sum(known[block_id].word_count() for block_id in ids)
        return words / wpm * expansion

    merged = False
    while len(drafts) > 1:
        sizes = [supportable(d["ids"]) for d in drafts]
        weakest = min(range(len(drafts)), key=lambda i: sizes[i])
        if sizes[weakest] >= MIN_VIABLE_MINUTES:
            break
        if weakest == 0:
            neighbour = 1
        elif weakest == len(drafts) - 1:
            neighbour = weakest - 1
        else:
            neighbour = weakest - 1 if sizes[weakest - 1] <= sizes[weakest + 1] else weakest + 1
        keep, drop = min(weakest, neighbour), max(weakest, neighbour)
        drafts[keep]["ids"] = sorted(
            drafts[keep]["ids"] + drafts[drop]["ids"], key=lambda block_id: order[block_id]
        )
        del drafts[drop]
        merged = True

    objectives = parsed.objectives
    source = "document" if objectives else "generated"
    episodes: list[EpisodePlan] = []
    for position, draft in enumerate(drafts):
        entry = draft["entry"]
        index = position + 1
        episode_id = f"ep{index:02d}"
        can_carry = round(supportable(draft["ids"]), 2)
        target = float(minutes)
        earlier = {block_id for d in drafts[:position] for block_id in d["ids"]}
        recap_ids = [
            str(b).strip() for b in entry.get("recap_block_ids", []) if str(b).strip() in earlier
        ][:MAX_RECAP_BLOCKS]
        goal_texts = [t for t in (str(g).strip() for g in entry.get("goals", [])) if t]
        goals = [
            LearningGoal(id=f"{episode_id}-g{i}", text=text, source=source)  # type: ignore[arg-type]
            for i, text in enumerate(goal_texts)
        ]
        refs: list[str] = []
        for number in entry.get("objectives", []) or []:
            try:
                position_in_list = int(number) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= position_in_list < len(objectives):
                text = objectives[position_in_list]
                if text not in refs:
                    refs.append(text)
        episodes.append(
            EpisodePlan(
                id=episode_id,
                index=index,
                title=str(entry.get("title", "")).strip() or f"Folge {index}",
                role=str(entry.get("role", "")).strip(),
                summary=(str(entry.get("summary", "")).strip() or None),
                block_ids=draft["ids"],
                recap_block_ids=recap_ids if position > 0 else [],
                goals=goals,
                objective_refs=refs,
                target_minutes=round(target, 2),
                supportable_minutes=can_carry,
                recap=(str(entry.get("recap", "")).strip() or None) if position > 0 else None,
                preview=(str(entry.get("preview", "")).strip() or None)
                if position < len(drafts) - 1
                else None,
            )
        )

    terms = []
    for item in data.get("terms", []) or []:
        if not isinstance(item, dict) or not str(item.get("term", "")).strip():
            continue
        try:
            first = int(item.get("first_episode", 1))
        except (TypeError, ValueError):
            first = 1
        terms.append(
            SeriesTerm(
                term=str(item["term"]).strip(),
                gloss=str(item.get("gloss", "")).strip(),
                first_episode=max(1, min(len(episodes), first)),
            )
        )

    warnings: list[str] = []
    for episode in episodes:
        words = _words(episode.block_ids)
        if words > cap:
            warnings.append(
                f"Folge {episode.index} übersteigt das Quellbudget um {words - cap} Wörter, "
                "mehr Folgen wählen"
            )

    verdict = "reduced" if merged else "ok"
    explanation = (
        f"The document supports about {budget.max_supportable_minutes:.1f} minutes "
        f"of dialogue. {len(episodes)} episode(s) of {minutes} minutes"
    )
    if merged:
        explanation += "; episodes below three minutes were merged into a neighbour"
    explanation += "."

    return SeriesPlan(
        title=str(data.get("title", "")).strip() or (parsed.title or "Serie"),
        through_line=str(data.get("through_line", "")).strip(),
        terms=terms,
        episodes=episodes,
        unassigned=unassigned,
        warnings=warnings,
        budget=SeriesBudget(
            max_supportable_minutes=round(float(budget.max_supportable_minutes), 2),
            minutes_per_episode=minutes,
            requested_episodes=count,
            verdict=verdict,  # type: ignore[arg-type]
            explanation=explanation,
        ),
    )


register_node(SeriesPlanNode())
