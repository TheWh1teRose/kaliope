"""Experiment 3, "Ablaufplan": tune the outline step's prompt and output shape.

It starts from exactly what production sends to the plain ``outline`` node: its
system prompt and response schema, and its user message expressed as a template
over named fields. Everything is editable, fields can be added, and one run is
one model call. The answer is read as an ``Outline`` when it has that shape, so
the page can show it as a running order; any other shape (for example after
switching structured output off and asking for different JSON) stays raw text
with a warning. Budgets are reported, never rescaled: the experiment shows what
the model planned, not what the node would make of it.
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
from app.pipeline.nodes.outline import _SCHEMA as OUTLINE_SCHEMA
from app.pipeline.nodes.outline import _SYSTEM as OUTLINE_SYSTEM
from app.pipeline.nodes.outline import BUDGET_TOLERANCE
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import ContentBudget, FormatSpec, Outline, Selection

#: The outline node pins this model in both shipped flows.
PRODUCTION_MODEL = "claude-opus-5"

#: The outline node's user message (``nodes/outline.OutlineNode.run``) as a template.
OUTLINE_TEMPLATE = (
    "Format: {{format_name}}. Speakers: {{speakers}}. Register: {{register}}.\n"
    "{{beat_guidance}}"
    "Target length: {{target_minutes}} minutes (~{{target_words}} words). "
    "The beat budgets must sum to {{target_words}}.\n"
    "Document language: {{language}}\n\n"
    "Learning goals:\n{{learning_goals}}\n\n"
    "Selected passages:\n{{passages}}"
)

#: A small, self-contained input so the experiment runs before any run is loaded.
SAMPLE_OUTLINE: dict[str, str] = {
    "format_name": "Zwei Stimmen im Gespräch",
    "speakers": "Moderator (asks the questions a listener would ask), "
    "Expertin (explains the material)",
    "register": "formal",
    "beat_guidance": "",
    "target_minutes": "5.0",
    "target_words": "750",
    "language": "de",
    "learning_goals": (
        "- g0: Erklären, wo die Lichtreaktion stattfindet und was sie liefert.\n"
        "- g1: Beschreiben, wie der Calvin-Zyklus die gespeicherte Energie nutzt."
    ),
    "passages": (
        "[b12] Die Fotosynthese wandelt Lichtenergie in chemische Energie um. Sie läuft in "
        "zwei Stufen ab: der Lichtreaktion und dem Calvin-Zyklus.\n"
        "[b14] Die Lichtreaktion findet an den Thylakoidmembranen der Chloroplasten statt. "
        "Chlorophyll absorbiert dort vor allem rotes und blaues Licht.\n"
        "[b15] Die aufgenommene Lichtenergie wird genutzt, um Wasser zu spalten. Dabei "
        "entsteht Sauerstoff als Nebenprodukt.\n"
        "[b16] Die Energie wird in Form von ATP und NADPH zwischengespeichert und "
        "anschließend im Calvin-Zyklus verwendet.\n"
        "[b21] Im Calvin-Zyklus wird Kohlendioxid aus der Luft gebunden und mit Hilfe von "
        "ATP und NADPH zu Zucker aufgebaut."
    ),
}

#: ``[b14] …`` at the start of a line of the passages field.
_PASSAGE_ID = re.compile(r"^\[([^\]\s]+)\]", re.MULTILINE)


def outline_fields(
    parsed: ParsedDocument,
    selection: Selection,
    budget: ContentBudget,
    format_spec: FormatSpec,
) -> dict[str, str]:
    """The values ``OutlineNode.run`` puts into its user message."""
    blocks = {b.id: b for b in parsed.blocks}
    selected_ids = [s.block_id for s in selection.selected_blocks if s.block_id in blocks]
    return {
        "format_name": format_spec.name,
        "speakers": ", ".join(f"{s.name} ({s.role})" for s in format_spec.speakers),
        "register": format_spec.register,
        "beat_guidance": (
            f"Beat guidance: {format_spec.beats_hint}\n" if format_spec.beats_hint else ""
        ),
        "target_minutes": f"{budget.target_minutes:.1f}",
        "target_words": str(budget.target_words),
        "language": parsed.language,
        "learning_goals": "\n".join(f"- {g.id}: {g.text}" for g in selection.learning_goals),
        "passages": "\n".join(f"[{bid}] {blocks[bid].llm_text()[:600]}" for bid in selected_ids),
    }


def read_outline(payload: Any) -> tuple[Outline | None, str | None]:
    """The answer as an ``Outline``, or why it is not one.

    Beats get the ids the node would give them (``beat000`` …) unless the
    answer brings its own; nothing is dropped, filtered or rescaled.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("beats"), list):
        return None, 'the answer has no "beats" list, so it is shown as raw text'
    beats = [
        {"id": f"beat{index:03d}", **entry} if isinstance(entry, dict) else entry
        for index, entry in enumerate(payload["beats"])
    ]
    try:
        return Outline.model_validate({"beats": beats}), None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        problem = f"the answer is not an outline ({where}: {first.get('msg')})"
        return None, f"{problem}, so it is shown as raw text"


def outline_warnings(outline: Outline, fields: dict[str, str]) -> list[str]:
    """What the production node would have corrected in this outline."""
    warnings: list[str] = []
    known = set(_PASSAGE_ID.findall(fields.get("passages", "")))
    if known:
        unknown = sorted(
            {bid for beat in outline.beats for bid in beat.block_ids if bid not in known}
        )
        if unknown:
            warnings.append("passage ids not in the input: " + ", ".join(unknown))
    try:
        target = int(fields.get("target_words", ""))
    except ValueError:
        target = 0
    total = outline.total_budget()
    if target > 0 and abs(total - target) / target > BUDGET_TOLERANCE:
        warnings.append(
            f"beat budgets sum to {total} against a target of {target}; "
            "production would rescale them"
        )
    return warnings


class OutlinePlan(PromptExperiment):
    key = "outline"
    title = "Ablaufplan"
    summary = (
        "System-Prompt, Eingabe und JSON-Form des Ablaufplan-Schritts ändern, einen "
        "Ablaufplan erstellen lassen und die Ausgaben sammeln."
    )
    target = "Ablaufplan-Schritt"
    version = "1"

    def defaults(self) -> BaseModel:
        return PromptSetup(
            system_prompt=OUTLINE_SYSTEM,
            user_template=OUTLINE_TEMPLATE,
            fields=dict(SAMPLE_OUTLINE),
            settings=ModelSettings(model=PRODUCTION_MODEL, max_tokens=12000),
            json_schema=copy.deepcopy(OUTLINE_SCHEMA),
        )

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="format_name", label="Format"),
            FieldSpec(key="speakers", label="Sprecher", multiline=True),
            FieldSpec(key="register", label="Register"),
            FieldSpec(
                key="beat_guidance",
                label="Abschnitts-Hinweis",
                multiline=True,
                hint="nur wenn das Format einen hat, mit Zeilenumbruch am Ende",
            ),
            FieldSpec(key="target_minutes", label="Ziellänge (Minuten)"),
            FieldSpec(key="target_words", label="Zielwörter"),
            FieldSpec(key="language", label="Sprache"),
            FieldSpec(key="learning_goals", label="Lernziele", multiline=True),
            FieldSpec(
                key="passages",
                label="Belegstellen",
                multiline=True,
                hint="je Stelle höchstens 600 Zeichen, wie in der Produktion",
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

        outline: Outline | None = None
        if payload is not None:
            outline, problem = read_outline(payload)
            if problem:
                warnings.append(problem)
        if outline is not None:
            warnings.extend(outline_warnings(outline, setup.fields))
        output["outline"] = outline.model_dump(mode="json") if outline else None
        return ExperimentResult(output_json=output, text=result.text, warnings=warnings)

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_outline_source(session, store, source)


def load_outline_source(session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    """The outline node's input fields from a finished run, plus its outline as reference.

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
            keys=["parsed", "selection", "budget", "format_spec", "outline"],
            include_payload=True,
        )
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    payloads: dict[str, Any] = {v.key: v.payload for v in values if v.payload is not None}
    for needed in ("parsed", "selection", "budget", "format_spec"):
        if needed not in payloads:
            raise LookupError(f"run '{run.id}' has no {needed} yet")

    fields = outline_fields(
        coerce_value("parsed", payloads["parsed"]),
        coerce_value("selection", payloads["selection"]),
        coerce_value("budget", payloads["budget"]),
        coerce_value("format_spec", payloads["format_spec"]),
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
            "outline": payloads.get("outline"),
        },
    )


register_experiment(OutlinePlan())
