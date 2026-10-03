"""Experiment: tune the series planner's prompt and input.

It starts from the production ``series_plan`` system prompt and response schema.
The user message is the production planner template: document language, an
optional split hint, the document's objectives, the content budget, format and
audience, and how many episodes of what length. One run is one model call.
The answer is read as a series plan when it has episodes with titles, roles,
goals and passage ids; any other shape stays raw text with a warning. Goals
use the same formulation rule as the objectives node. Nothing here assigns
leftover passages or warns about the source-word budget: the page shows what
the model planned.
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
from app.models import Document, Run, Series
from app.pipeline.bench import coerce_value, load_values
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.series_plan import _SCHEMA as PLAN_SCHEMA
from app.pipeline.nodes.series_plan import _SYSTEM as PLAN_SYSTEM
from app.pipeline.nodes.series_plan import PLANNER_USER_TEMPLATE as SERIES_TEMPLATE
from app.pipeline.nodes.series_plan import planner_user_fields
from app.pipeline.objective_rule import formulated_goal
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import AudienceSpec, ContentBudget, FormatSpec, SeriesRequest

#: The series planner pins this model and this ``max_tokens``.
PRODUCTION_MODEL = "claude-opus-5"
PRODUCTION_MAX_TOKENS = 16000

SAMPLE_SERIES: dict[str, str] = {
    "episode_count": "2",
    "minutes_per_episode": "15",
    "source_budget": (
        "Each episode should use about 405 words of source material so the 15 minutes "
        "have room for dialogue, explanation and questions. 810 words is the most one "
        "episode should carry; the document's 810 narratable words spread across 2 "
        "episodes come to about 405 words each. Prefer the smaller figure, and prefer "
        "fewer, richer topics. Passages beyond that stay unassigned."
    ),
    "format_name": "Zwei Stimmen im Gespräch",
    "speakers": (
        "Moderator (asks the questions a listener would ask), Expertin (explains the material)"
    ),
    "audience": "Erwachsene ohne Fachstudium, die den Stoff verstehen wollen.",
    "outcome": (
        "Desired outcome (the source — derive every objective from this):\n"
        "Die Hörerin kann erklären, wie Fotosynthese Licht in Zucker verwandelt."
    ),
    "time_budget": (
        "Time budget (constraint — only write objectives a listener can reach in "
        "this time): 15.0 minutes"
    ),
    "language": "de",
    "hint": "",
    "objectives": "The document states no objectives of its own.",
    "passage_count": "2",
    "passages": (
        "\n## Licht\n"
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


def series_fields(
    parsed: ParsedDocument,
    budget: ContentBudget,
    format_spec: FormatSpec,
    audience: AudienceSpec,
    *,
    episode_count: int,
    minutes: int,
    hint: str | None = None,
) -> dict[str, str]:
    return planner_user_fields(
        parsed,
        budget,
        format_spec,
        audience,
        parsed.narratable_blocks(),
        episode_count,
        minutes,
        hint,
    )


def planner_minutes_and_count(
    plan: dict[str, Any] | None,
    request: SeriesRequest | None,
    target_minutes: Any,
) -> tuple[int, int]:
    """Per-episode minutes and the requested episode count.

    Taken from the plan budget, then from the series request. ``target_minutes``
    is used only when neither has a length. A missing count is one episode.
    """
    budget = plan.get("budget") if isinstance(plan, dict) else None
    if not isinstance(budget, dict):
        budget = {}
    minutes = _as_int(budget.get("minutes_per_episode"))
    count = _as_int(budget.get("requested_episodes"))
    if request is not None:
        if minutes is None:
            minutes = _as_int(request.minutes_per_episode)
        if count is None:
            count = _as_int(request.episodes)
    if minutes is None:
        minutes = _as_int(target_minutes) or 15
    if count is None:
        count = 1
    return minutes, count


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _series_request(raw: Any) -> SeriesRequest | None:
    if not isinstance(raw, dict):
        return None
    minutes = _as_int(raw.get("minutes_per_episode"))
    if minutes is None:
        return None
    hint = raw.get("hint")
    return SeriesRequest(
        episodes=_as_int(raw.get("episodes")),
        minutes_per_episode=minutes,
        hint=hint if isinstance(hint, str) and hint.strip() else None,
    )


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
            FieldSpec(key="source_budget", label="Quellenbudget", multiline=True),
            FieldSpec(key="format_name", label="Format"),
            FieldSpec(key="speakers", label="Stimmen", multiline=True),
            FieldSpec(key="audience", label="Zielgruppe", multiline=True),
            FieldSpec(key="outcome", label="Gewünschtes Ergebnis", multiline=True),
            FieldSpec(key="time_budget", label="Zeitbudget", multiline=True),
            FieldSpec(key="language", label="Dokumentsprache"),
            FieldSpec(key="hint", label="Hinweis zur Aufteilung", multiline=True),
            FieldSpec(key="objectives", label="Lernziele des Dokuments", multiline=True),
            FieldSpec(key="passage_count", label="Anzahl Passagen"),
            FieldSpec(
                key="passages",
                label="Passagen",
                multiline=True,
                hint="Abschnitte und Passagen, so wie der Serienplaner sie sieht",
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

    plan_payload, request = _plan_and_request(session, store, run, payloads)
    minutes, episode_count = planner_minutes_and_count(
        plan_payload, request, payloads.get("target_minutes")
    )
    fields = series_fields(
        coerce_value("parsed", payloads["parsed"]),
        coerce_value("budget", payloads["budget"]),
        coerce_value("format_spec", payloads["format_spec"]),
        coerce_value("audience_spec", payloads["audience_spec"]),
        episode_count=episode_count,
        minutes=minutes,
        hint=None if request is None else request.hint,
    )
    document = session.get(Document, run.document_id)
    own_plan = payloads.get("series_plan")
    return SourceOut(
        fields=fields,
        beats=[],
        source={
            "run_id": run.id,
            "document_id": run.document_id,
            "name": run.name,
            "document_title": (document.title or document.filename) if document else None,
            "plan": own_plan if isinstance(own_plan, dict) else None,
        },
    )


def _plan_and_request(
    session: Session, store: ArtifactStore, run: Run, payloads: dict[str, Any]
) -> tuple[dict[str, Any] | None, SeriesRequest | None]:
    own_plan = payloads.get("series_plan")
    plan = own_plan if isinstance(own_plan, dict) else None
    raw = (run.config_json or {}).get("series_request")
    if run.series_id and (plan is None or not isinstance(raw, dict)):
        series = session.get(Series, run.series_id)
        if series is not None:
            if not isinstance(raw, dict) and isinstance(series.request_json, dict):
                raw = series.request_json
            digest = series.plan_artifact_hash
            if plan is None and digest and store.exists(digest):
                loaded = store.get_raw(digest)
                if isinstance(loaded, dict):
                    plan = loaded
    return plan, _series_request(raw)


register_experiment(SeriesPlanner())
