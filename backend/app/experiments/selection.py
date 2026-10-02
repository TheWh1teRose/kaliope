"""Experiment 4, "Auswahl": tune the selection step's prompt and input.

It starts from exactly what production sends to the ``select`` node: its system
prompt and response schema, and its user message expressed as a template over
named fields. Everything is editable, fields can be added, and one run is one
model call. The answer is read as a ``Selection`` when it has that shape, so the
page can show the learning goals and the chosen passages; any other shape (for
example after switching structured output off and asking for different JSON)
stays raw text with a warning. Nothing is filtered: ids the node would discard
are reported, not dropped, so the experiment shows what the model chose.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, ValidationError
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
from app.pipeline.nodes.select import _SCHEMA as SELECT_SCHEMA
from app.pipeline.nodes.select import _SYSTEM as SELECT_SYSTEM
from app.pipeline.nodes.select import (
    DEFAULT_MAX_PROMPT_CHARS,
    _audience_text,
    _candidate_payload,
)
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import AudienceSpec, ContentBudget, Selection

#: The select node pins this model in the baseline flow.
PRODUCTION_MODEL = "claude-opus-5"
#: The select node's default ``max_tokens``.
PRODUCTION_MAX_TOKENS = 16000

#: The opening of the select node's goal instruction when the document states objectives.
DOCUMENT_GOALS = (
    "The document states its own objectives below. Derive the learning goals "
    "from them, staying close to the author's intent.\n\n"
)
#: The select node's goal instruction when the document states none.
GENERATED_GOALS = (
    "The document states no objectives of its own. Infer learning goals "
    "from the material you select."
)

#: The select node's user message (``nodes/select.SelectNode.run``) as a template.
SELECTION_TEMPLATE = (
    "Audience: {{audience}}\n\n"
    "Target length: {{target_minutes}} minutes "
    "(~{{target_words}} words at {{words_per_minute}} wpm).\n"
    "Available narratable material: {{narratable_words}} words.\n"
    "Document language: {{language}}\n\n"
    "{{goal_instruction}}\n\n"
    "Candidate passages ({{passage_count}}):\n"
    "{{passages}}"
)

#: A small, self-contained input so the experiment runs before any run is loaded.
SAMPLE_SELECTION: dict[str, str] = {
    "audience": "Interessierte Laien ohne Vorwissen; prior knowledge: Schulbiologie",
    "target_minutes": "5.0",
    "target_words": "750",
    "words_per_minute": "150",
    "narratable_words": "260",
    "language": "de",
    "goal_instruction": GENERATED_GOALS,
    "passage_count": "6",
    "passages": (
        "[b3] (weight 0.6) section: Inhalt\n"
        "1 Einleitung · 2 Lichtreaktion · 3 Calvin-Zyklus\n"
        "[b12] (weight 1.0) section: Einleitung\n"
        "Die Fotosynthese wandelt Lichtenergie in chemische Energie um. Sie läuft in zwei "
        "Stufen ab: der Lichtreaktion und dem Calvin-Zyklus.\n"
        "[b14] (weight 1.0) section: Lichtreaktion\n"
        "Die Lichtreaktion findet an den Thylakoidmembranen der Chloroplasten statt. "
        "Chlorophyll absorbiert dort vor allem rotes und blaues Licht.\n"
        "[b15] (weight 1.3) section: Lichtreaktion\n"
        "Merke: Die aufgenommene Lichtenergie wird genutzt, um Wasser zu spalten. Dabei "
        "entsteht Sauerstoff als Nebenprodukt.\n"
        "[b16] (weight 1.0) section: Lichtreaktion\n"
        "Die Energie wird in Form von ATP und NADPH zwischengespeichert und anschließend im "
        "Calvin-Zyklus verwendet.\n"
        "[b21] (weight 1.0) section: Calvin-Zyklus\n"
        "Im Calvin-Zyklus wird Kohlendioxid aus der Luft gebunden und mit Hilfe von ATP und "
        "NADPH zu Zucker aufgebaut."
    ),
}

#: ``[b14] (weight 1.0) …`` at the start of a line of the passages field.
_PASSAGE = re.compile(r"^\[([^\]\s]+)\](?: \(weight ([0-9.]+)\))?", re.MULTILINE)


def selection_fields(
    parsed: ParsedDocument,
    budget: ContentBudget,
    audience: AudienceSpec,
    *,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
) -> dict[str, str]:
    """The values ``SelectNode.run`` puts into its user message.

    Raises ``LookupError`` when the document has nothing to select from, like the node.
    """
    candidates = parsed.narratable_blocks()
    if not candidates:
        raise LookupError("no narratable blocks are available to select from")
    section_titles = {s.id: s.title for s in (parsed.sections or [])}
    payload, _truncated = _candidate_payload(candidates, section_titles, max_prompt_chars)
    objectives = parsed.objectives
    return {
        "audience": _audience_text(audience),
        "target_minutes": f"{budget.target_minutes:.1f}",
        "target_words": str(budget.target_words),
        "words_per_minute": str(budget.words_per_minute),
        "narratable_words": str(budget.narratable_words),
        "language": parsed.language,
        "goal_instruction": (
            DOCUMENT_GOALS + "\n".join(f"- {o}" for o in objectives)
            if objectives
            else GENERATED_GOALS
        ),
        "passage_count": str(len(payload)),
        "passages": "\n".join(
            f"[{e['id']}] (weight {e['weight']})"
            + (f" section: {e['section']}" if e["section"] else "")
            + f"\n{e['text']}"
            for e in payload
        ),
    }


def _weights(fields: dict[str, str]) -> dict[str, float]:
    """Passage id → weight as offered in the passages field."""
    return {
        match.group(1): float(match.group(2)) if match.group(2) else 1.0
        for match in _PASSAGE.finditer(fields.get("passages", ""))
    }


def read_selection(payload: Any, fields: dict[str, str]) -> tuple[Selection | None, str | None]:
    """The answer as a ``Selection``, or why it is not one.

    Goals get the source the node would give them (``document`` when the goal
    instruction hands over the document's objectives), blocks the weight they
    were offered with. Nothing is dropped, filtered or sorted.
    """
    if not isinstance(payload, dict):
        return None, "the answer is not a JSON object, so it is shown as raw text"
    for key in ("learning_goals", "selected_blocks"):
        if not isinstance(payload.get(key), list):
            return None, f'the answer has no "{key}" list, so it is shown as raw text'
    source = (
        "document"
        if fields.get("goal_instruction", "").startswith(DOCUMENT_GOALS.strip())
        else "generated"
    )
    weights = _weights(fields)
    goals = [
        {"id": f"g{index}", **entry, "source": source} if isinstance(entry, dict) else entry
        for index, entry in enumerate(payload["learning_goals"])
    ]
    blocks = [
        {"salience": weights.get(str(entry.get("block_id")), 1.0), **entry}
        if isinstance(entry, dict)
        else entry
        for entry in payload["selected_blocks"]
    ]
    try:
        selection = Selection.model_validate(
            {
                "learning_goals": goals,
                "selected_blocks": blocks,
                "rationale": payload.get("rationale") or "",
            }
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        problem = f"the answer is not a selection ({where}: {first.get('msg')})"
        return None, f"{problem}, so it is shown as raw text"
    return selection, None


def selection_warnings(selection: Selection, fields: dict[str, str]) -> list[str]:
    """What the production node would have corrected or refused in this selection."""
    warnings: list[str] = []
    if not any(goal.text.strip() for goal in selection.learning_goals):
        warnings.append("no learning goals; production would fail the step")
    known = set(_weights(fields))
    ids = selection.block_ids()
    if known:
        unknown = sorted({bid for bid in ids if bid not in known})
        if unknown:
            warnings.append("passage ids not in the input: " + ", ".join(unknown))
        if not any(bid in known for bid in ids):
            warnings.append("no passage id from the input; production would fail the step")
    repeated = sorted({bid for bid in ids if ids.count(bid) > 1})
    if repeated:
        warnings.append("passages chosen more than once: " + ", ".join(repeated))
    goal_ids = {goal.id for goal in selection.learning_goals}
    dangling = sorted(
        {g for block in selection.selected_blocks for g in block.goal_ids if g not in goal_ids}
    )
    if dangling:
        warnings.append("goal ids without a learning goal: " + ", ".join(dangling))
    return warnings


class SelectionExperiment(PromptExperiment):
    key = "selection"
    title = "Auswahl"
    summary = (
        "System-Prompt, Eingabe und Modell des Auswahl-Schritts ändern, Lernziele und "
        "Belegstellen wählen lassen und die Ausgaben sammeln."
    )
    target = "Auswahl-Schritt"
    version = "1"

    def defaults(self) -> BaseModel:
        return PromptSetup(
            system_prompt=SELECT_SYSTEM,
            user_template=SELECTION_TEMPLATE,
            fields=dict(SAMPLE_SELECTION),
            settings=ModelSettings(model=PRODUCTION_MODEL, max_tokens=PRODUCTION_MAX_TOKENS),
            json_schema=copy.deepcopy(SELECT_SCHEMA),
        )

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="audience", label="Zielgruppe", multiline=True),
            FieldSpec(key="target_minutes", label="Ziellänge (Minuten)"),
            FieldSpec(key="target_words", label="Zielwörter"),
            FieldSpec(key="words_per_minute", label="Wörter pro Minute"),
            FieldSpec(key="narratable_words", label="Verfügbarer Stoff (Wörter)"),
            FieldSpec(key="language", label="Sprache"),
            FieldSpec(
                key="goal_instruction",
                label="Lernziel-Anweisung",
                multiline=True,
                hint="nennt die Lernziele des Dokuments, wenn es welche hat",
            ),
            FieldSpec(key="passage_count", label="Anzahl Kandidaten"),
            FieldSpec(
                key="passages",
                label="Kandidaten",
                multiline=True,
                hint="je Stelle Id, Gewicht und Abschnitt, gekürzt aufs Prompt-Budget "
                "wie in der Produktion",
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

        selection: Selection | None = None
        if payload is not None:
            selection, problem = read_selection(payload, setup.fields)
            if problem:
                warnings.append(problem)
        if selection is not None:
            warnings.extend(selection_warnings(selection, setup.fields))
        output["selection"] = selection.model_dump(mode="json") if selection else None
        return ExperimentResult(output_json=output, text=result.text, warnings=warnings)

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_selection_source(session, store, source)


def load_selection_source(session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    """The select node's input fields from a finished run, plus its selection as reference.

    Raises ``LookupError`` when the run is missing or lacks an input.
    """
    run = session.get(Run, source.run_id)
    if run is None:
        raise LookupError(f"run '{source.run_id}' does not exist")
    try:
        values, _nodes, _document = load_values(
            session,
            store,
            run_id=run.id,
            keys=["parsed", "budget", "audience_spec", "selection"],
            include_payload=True,
        )
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    payloads: dict[str, Any] = {v.key: v.payload for v in values if v.payload is not None}
    for needed in ("parsed", "budget", "audience_spec"):
        if needed not in payloads:
            raise LookupError(f"run '{run.id}' has no {needed} yet")

    fields = selection_fields(
        coerce_value("parsed", payloads["parsed"]),
        coerce_value("budget", payloads["budget"]),
        coerce_value("audience_spec", payloads["audience_spec"]),
    )
    document = session.get(Document, run.document_id)
    return SourceOut(
        fields=fields,
        beats=[],
        source={
            "run_id": run.id,
            "document_id": run.document_id,
            "document_title": (document.title or document.filename) if document else None,
            "selection": payloads.get("selection"),
        },
    )


register_experiment(SelectionExperiment())
