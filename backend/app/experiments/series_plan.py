"""Experiment: tune the series planner's prompt and input.

It starts from the production ``series_plan`` system prompt and response schema.
The user message is a template over the fields the planner needs: a document
summary, the selection, the content budget, format and audience, and how many
episodes of what length. One run is one model call. The answer is read as a
series plan when it has episodes with titles, roles, goals and passage ids;
any other shape stays raw text with a warning. Goals use the same formulation
rule as the objectives node. Nothing here assigns leftover passages or warns
about the source-word budget: the page shows what the model planned.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.experiments.base import (
    ExperimentContext,
    ExperimentResult,
    FieldSpec,
    ModelSettings,
    PromptExperiment,
    PromptSetup,
    SourceIn,
    SourceOut,
)
from app.experiments.registry import register_experiment
from app.llm.base import LLMClient, LLMError, parse_json
from app.models import Document, Run
from app.pipeline.bench import coerce_value, load_values
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.content_budget import source_word_budget
from app.pipeline.nodes.series_plan import _SCHEMA as PLAN_SCHEMA
from app.pipeline.nodes.series_plan import _SYSTEM as PLAN_SYSTEM
from app.pipeline.nodes.series_plan import PASSAGE_CHARS
from app.pipeline.objective_rule import (
    DOCUMENT_OBJECTIVES_NOTE,
    desired_outcome_source,
    formulated_goal,
    time_budget_constraint,
)
from app.schemas.document import Block, ParsedDocument
from app.schemas.pipeline import AudienceSpec, ContentBudget, FormatSpec, Selection

#: The series planner pins this model and this ``max_tokens``.
PRODUCTION_MODEL = "claude-opus-5"
PRODUCTION_MAX_TOKENS = 16000

SERIES_TEMPLATE = (
    "Plan {{episode_count}} episodes of about {{minutes_per_episode}} minutes each.\n"
    "{{content_budget}}\n"
    "{{format_audience}}\n"
    "Selection:\n{{selection}}\n\n"
    "Document:\n{{document}}\n"
)

SAMPLE_SERIES: dict[str, str] = {
    "episode_count": "2",
    "minutes_per_episode": "15",
    "content_budget": (
        "810 narratable words support about 15.0 minutes at 135 words per minute "
        "with a 2.5× dialogue expansion. Each episode should use about 405 words "
        "of source so the 15 minutes have room for dialogue."
    ),
    "format_audience": (
        "Format: Zwei Stimmen im Gespräch. Speakers: Moderator (asks the questions "
        "a listener would ask), Expertin (explains the material).\n"
        "Audience: Erwachsene ohne Fachstudium, die den Stoff verstehen wollen.\n"
        "Desired outcome (the source — derive every objective from this):\n"
        "Die Hörerin kann erklären, wie Fotosynthese Licht in Zucker verwandelt.\n"
        "Time budget (constraint — only write objectives a listener can reach in "
        "this time): 15.0 minutes per episode."
    ),
    "selection": (
        "Learning goals:\n"
        "- g0: Erklären, wo die Lichtreaktion stattfindet und was sie liefert.\n"
        "Selected passages: b12, b14"
    ),
    "document": (
        "## Licht\n"
        "[b12] (weight 1.0, 40 words)\n"
        "Die Fotosynthese wandelt Lichtenergie in chemische Energie um.\n"
        "[b14] (weight 0.8, 30 words)\n"
        "Die Lichtreaktion findet an den Thylakoidmembranen der Chloroplasten statt."
    ),
}


class PlanGoal(BaseModel):
    text: str
    bloom_level: str
    derivation: str | None = None


class PlanEpisode(BaseModel):
    title: str
    role: str = ""
    summary: str | None = None
    block_ids: list[str]
    goals: list[PlanGoal]


class PlanView(BaseModel):
    """The planner's answer, shown as episodes. Not the node's checked SeriesPlan."""

    title: str = ""
    through_line: str = ""
    episodes: list[PlanEpisode]


def _clip(text: str) -> str:
    if len(text) <= PASSAGE_CHARS:
        return text
    return text[:PASSAGE_CHARS].rsplit(" ", 1)[0] + " …"


def document_summary(parsed: ParsedDocument) -> str:
    """Passages the way the planner sees them: section, id, weight, words, start of the text."""
    titles = {section.id: section.title for section in (parsed.sections or [])}
    lines: list[str] = []
    current: str | None = "\0"
    for block in parsed.narratable_blocks():
        if block.section_id != current:
            current = block.section_id
            lines.append(f"## {titles.get(current or '') or '(no section)'}")
        lines.append(_passage_line(block))
    objectives = parsed.objectives
    if objectives:
        lines.append(
            DOCUMENT_OBJECTIVES_NOTE
            + "\n"
            + "\n".join(f"{index + 1}. {objective}" for index, objective in enumerate(objectives))
        )
    return "\n".join(lines)


def _passage_line(block: Block) -> str:
    return (
        f"[{block.id}] (weight {round(block.salience, 2)}, {block.word_count()} words)\n"
        f"{_clip(block.llm_text())}"
    )


def selection_summary(selection: Selection | None) -> str:
    if selection is None:
        return "No selection."
    goals = "\n".join(f"- {goal.id}: {goal.text}" for goal in selection.learning_goals) or "(none)"
    ids = ", ".join(item.block_id for item in selection.selected_blocks) or "(none)"
    return f"Learning goals:\n{goals}\nSelected passages: {ids}"


def budget_summary(budget: ContentBudget, minutes: int, count: int) -> str:
    per_episode = source_word_budget(minutes, budget.words_per_minute, budget.dialogue_expansion)
    return (
        f"{budget.narratable_words} narratable words support about "
        f"{budget.max_supportable_minutes:.1f} minutes at {budget.words_per_minute} words "
        f"per minute with a {budget.dialogue_expansion:g}× dialogue expansion. "
        f"Each of {count} episodes of {minutes} minutes should use about {per_episode} "
        "words of source so there is room for dialogue."
    )


def format_audience_text(format_spec: FormatSpec, audience: AudienceSpec, minutes: int) -> str:
    speakers = ", ".join(f"{speaker.name} ({speaker.role})" for speaker in format_spec.speakers)
    desired = (audience.desired_outcome or "").strip()
    outcome = (
        desired_outcome_source(desired)
        if desired
        else "The audience states no desired outcome. Do not invent ambition from the document."
    )
    return (
        f"Format: {format_spec.name}. Speakers: {speakers}.\n"
        f"Audience: {audience.description}\n"
        f"{outcome}\n"
        f"{time_budget_constraint(float(minutes))} per episode."
    )


def series_fields(
    parsed: ParsedDocument,
    selection: Selection | None,
    budget: ContentBudget,
    format_spec: FormatSpec,
    audience: AudienceSpec,
    *,
    episode_count: int,
    minutes: int,
) -> dict[str, str]:
    return {
        "episode_count": str(episode_count),
        "minutes_per_episode": str(minutes),
        "content_budget": budget_summary(budget, minutes, episode_count),
        "format_audience": format_audience_text(format_spec, audience, minutes),
        "selection": selection_summary(selection),
        "document": document_summary(parsed),
    }


def read_series_plan(payload: Any) -> tuple[PlanView | None, str | None]:
    """The answer as episodes, or why it is not a series plan.

    A goal that is a bare string, empty, or not a Bloom level is dropped.
    ``analyze`` becomes ``analyse``. Episodes are not filled, merged or trimmed.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("episodes"), list):
        return None, 'the answer has no "episodes" list, so it is shown as raw text'
    episodes: list[PlanEpisode] = []
    for entry in payload["episodes"]:
        if not isinstance(entry, dict):
            return None, "an episode is not an object, so the answer is shown as raw text"
        title = str(entry.get("title", "")).strip()
        raw_ids = entry.get("block_ids")
        if (
            not title
            or not isinstance(raw_ids, list)
            or not all(isinstance(block_id, str) for block_id in raw_ids)
        ):
            return (
                None,
                "an episode has no title or passage ids, so the answer is shown as raw text",
            )
        block_ids = [block_id for block_id in raw_ids if isinstance(block_id, str)]
        goals: list[PlanGoal] = []
        for goal in entry.get("goals") or []:
            formulated = formulated_goal(goal)
            if formulated is None:
                continue
            text, level, derivation = formulated
            goals.append(PlanGoal(text=text, bloom_level=level, derivation=derivation))
        summary = str(entry.get("summary") or "").strip() or None
        episodes.append(
            PlanEpisode(
                title=title,
                role=str(entry.get("role") or "").strip(),
                summary=summary,
                block_ids=list(block_ids),
                goals=goals,
            )
        )
    if not episodes:
        return None, "the answer has no episodes, so it is shown as raw text"
    return (
        PlanView(
            title=str(payload.get("title") or "").strip(),
            through_line=str(payload.get("through_line") or "").strip(),
            episodes=episodes,
        ),
        None,
    )


class SeriesPlanner(PromptExperiment):
    key = "series_plan"
    title = "Folgen planen"
    summary = (
        "System-Prompt, Eingabe und JSON-Form des Serienplaners ändern, einen "
        "Folgenplan erstellen lassen und die Ausgaben sammeln."
    )
    target = "Serienplaner"
    version = "1"

    def defaults(self) -> BaseModel:
        return PromptSetup(
            system_prompt=PLAN_SYSTEM,
            user_template=SERIES_TEMPLATE,
            fields=dict(SAMPLE_SERIES),
            settings=ModelSettings(model=PRODUCTION_MODEL, max_tokens=PRODUCTION_MAX_TOKENS),
            json_schema=copy.deepcopy(PLAN_SCHEMA),
        )

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="episode_count", label="Anzahl Folgen"),
            FieldSpec(key="minutes_per_episode", label="Minuten je Folge"),
            FieldSpec(key="content_budget", label="Inhaltsbudget", multiline=True),
            FieldSpec(key="format_audience", label="Format und Zielgruppe", multiline=True),
            FieldSpec(key="selection", label="Auswahl", multiline=True),
            FieldSpec(
                key="document",
                label="Dokument",
                multiline=True,
                hint="Abschnitte und Passagen, oder eine Zusammenfassung des geparsten Dokuments",
            ),
        ]

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult:
        assert isinstance(setup, PromptSetup)
        result = super().run(setup, llm, ctx)
        output = dict(result.output_json)
        warnings = list(result.warnings)
        payload = output.get("payload")
        if payload is None and not setup.settings.structured:
            try:
                payload = parse_json(result.text or "")
            except LLMError:
                warnings.append("the answer is not JSON, so it is shown as raw text")
            output["payload"] = payload

        plan: PlanView | None = None
        if payload is not None:
            plan, problem = read_series_plan(payload)
            if problem:
                warnings.append(problem)
        output["plan"] = plan.model_dump(mode="json") if plan else None
        return ExperimentResult(output_json=output, text=result.text, warnings=warnings)

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_series_source(session, store, source)


def load_series_source(session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    """Planner input fields from a finished run, plus its series plan when the run has one.

    Raises ``LookupError`` when the run is missing or has no parsed document, budget,
    format or audience.
    """
    run = session.get(Run, source.run_id)
    if run is None:
        raise LookupError(f"run '{source.run_id}' does not exist")
    try:
        values, _nodes, _document = load_values(
            session,
            store,
            run_id=run.id,
            keys=[
                "parsed",
                "selection",
                "budget",
                "format_spec",
                "audience_spec",
                "target_minutes",
                "series_plan",
            ],
            include_payload=True,
        )
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    payloads: dict[str, Any] = {
        value.key: value.payload for value in values if value.payload is not None
    }
    for needed in ("parsed", "budget", "format_spec", "audience_spec"):
        if needed not in payloads:
            raise LookupError(f"run '{run.id}' has no {needed} yet")

    minutes = int(payloads.get("target_minutes") or 15)
    plan_payload = payloads.get("series_plan")
    episode_count = 2
    if isinstance(plan_payload, dict) and isinstance(plan_payload.get("episodes"), list):
        episode_count = max(2, len(plan_payload["episodes"]))
    fields = series_fields(
        coerce_value("parsed", payloads["parsed"]),
        coerce_value("selection", payloads["selection"]) if "selection" in payloads else None,
        coerce_value("budget", payloads["budget"]),
        coerce_value("format_spec", payloads["format_spec"]),
        coerce_value("audience_spec", payloads["audience_spec"]),
        episode_count=episode_count,
        minutes=minutes,
    )
    document = session.get(Document, run.document_id)
    return SourceOut(
        fields=fields,
        beats=[],
        source={
            "run_id": run.id,
            "document_id": run.document_id,
            "name": run.name,
            "document_title": (document.title or document.filename) if document else None,
            "plan": plan_payload,
        },
    )


register_experiment(SeriesPlanner())
