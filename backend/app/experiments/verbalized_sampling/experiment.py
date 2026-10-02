"""Experiment 2, "Verbalized Sampling": k versions of one beat in one call.

One run is two model calls for the same beat: the VS call (shared base prompt
plus the VS instruction, asking for k versions with a probability each) and
one plain call (the same base prompt, production's closing line, production's
schema). The screen shows all drafts blind, so the user judges them without
knowing which method wrote which.

The beat comes in through the same named fields and user template as
"Direkter Stil": loaded from a finished run with the same loader, or pasted.
Speakers, passages and audience may be pasted as the JSON the Werkbank shows;
they are turned into the text production would send.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.experiments.base import (
    FIELD_NAME,
    ExperimentContext,
    ExperimentResult,
    FieldSpec,
    ItemRef,
    ModelSettings,
    PromptExperiment,
    SourceIn,
    SourceOut,
    TemplateError,
    render,
)
from app.experiments.registry import register_experiment
from app.experiments.sources import BEAT_TEMPLATE, SAMPLE_BEAT, load_beat_source
from app.experiments.verbalized_sampling.checks import check_citations, parse_passages
from app.experiments.verbalized_sampling.cost import BASELINE_NODE, VS_NODE, summarize
from app.experiments.verbalized_sampling.options import MAX_K, MIN_K, VSOptions
from app.experiments.verbalized_sampling.parse import (
    CandidateSegment,
    VSParseError,
    draft_segments,
    parse_completion,
)
from app.experiments.verbalized_sampling.prompts import (
    DEFAULT_BASE_PROMPT,
    DEFAULT_VS_INSTRUCTION,
    PAPER_VS_INSTRUCTION,
    baseline_system_prompt,
    render_vs_instruction,
    vs_system_prompt,
)
from app.experiments.verbalized_sampling.schema import baseline_schema, vs_schema
from app.llm.base import LLMClient, LLMError
from app.pipeline.framework.artifacts import ArtifactStore

#: The script node pins this model in both shipped flows.
PRODUCTION_MODEL = "claude-opus-5"

_SPEAKER_LINE = re.compile(r"^\s*-\s*([^(:\n]+?)\s*(?:\(|:|$)", re.MULTILINE)


class VSSetup(BaseModel):
    """Everything one VS run is configured with."""

    base_prompt: str
    vs_instruction: str
    user_template: str
    fields: dict[str, str] = Field(default_factory=dict)
    settings: ModelSettings
    k: int = Field(default=5, ge=MIN_K, le=MAX_K)

    @field_validator("fields")
    @classmethod
    def _field_names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [name for name in value if not FIELD_NAME.match(name)]
        if bad:
            raise ValueError(
                "field names must be lower-case letters, digits and underscores: " + ", ".join(bad)
            )
        return value


# ------------------------------------------------------------- pasted values


def _json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _speaker_lines(data: Any) -> str | None:
    """``FormatSpec`` JSON (or its ``speakers`` list) as production's speaker lines."""
    if isinstance(data, dict):
        data = data.get("speakers")
    if not isinstance(data, list):
        return None
    lines = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("name"):
            line = f"- {entry['name']} ({entry.get('role', '')})"
            if entry.get("voice_note"):
                line += f": {entry['voice_note']}"
            lines.append(line)
    return "\n".join(lines) or None


def normalise_fields(fields: dict[str, str]) -> dict[str, str]:
    """Turn values pasted as Werkbank JSON into the text production sends.

    Plain text is kept as typed.
    """
    out = dict(fields)
    if "speakers" in out:
        lines = _speaker_lines(_json(out["speakers"]))
        if lines is not None:
            out["speakers"] = lines
    if "audience" in out:
        data = _json(out["audience"])
        if isinstance(data, dict) and isinstance(data.get("description"), str):
            out["audience"] = data["description"]
    if "passages" in out and _json(out["passages"]) is not None:
        blocks = parse_passages(out["passages"])
        if blocks:
            out["passages"] = "\n\n".join(f"[{bid}]\n{text}" for bid, text in blocks.items())
    return out


def speaker_names(speakers: str) -> list[str]:
    return [match.group(1).strip() for match in _SPEAKER_LINE.finditer(speakers)]


def _word_budget(fields: dict[str, str]) -> int | None:
    match = re.search(r"\d+", fields.get("word_budget", ""))
    return int(match.group(0)) if match else None


# ------------------------------------------------------------------- drafts


def _draft(
    item: str,
    source: str,
    index: int,
    segments: list[CandidateSegment],
    passages: dict[str, str],
    probability: float | None = None,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    citations = check_citations(segments, passages).model_dump() if passages else None
    return {
        "item": item,
        "source": source,
        "index": index,
        "probability": probability,
        "segments": [segment.model_dump() for segment in segments],
        "words": sum(len(segment.text.split()) for segment in segments),
        "claims": sum(1 for segment in segments if segment.kind == "claim"),
        "pedagogy": sum(1 for segment in segments if segment.kind == "pedagogy"),
        "citations": citations,
        "flags": flags or [],
    }


class VerbalizedSampling(PromptExperiment):
    key = "verbalized_sampling"
    title = "Verbalized Sampling"
    summary = (
        "Einen Abschnitt in k Fassungen mit Wahrscheinlichkeit schreiben lassen, neben einer "
        "Fassung ohne VS, und blind vergleichen."
    )
    target = "Skript-Schritt"
    version = "1"
    Setup: ClassVar[type[BaseModel]] = VSSetup

    def defaults(self) -> BaseModel:
        return VSSetup(
            base_prompt=DEFAULT_BASE_PROMPT,
            vs_instruction=DEFAULT_VS_INSTRUCTION,
            user_template=BEAT_TEMPLATE,
            fields=dict(SAMPLE_BEAT),
            settings=ModelSettings(model=PRODUCTION_MODEL, max_tokens=16000),
        )

    def extras(self) -> dict[str, Any]:
        """Texts and schemas the screen shows but the setup does not hold."""
        return {
            "vs_instructions": {"kalliope": DEFAULT_VS_INSTRUCTION, "paper": PAPER_VS_INSTRUCTION},
            "vs_schema": vs_schema(),
            "baseline_schema": baseline_schema(),
        }

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="beat_title", label="Abschnitt"),
            FieldSpec(key="word_budget", label="Wortbudget"),
            FieldSpec(
                key="speakers",
                label="Sprecher",
                multiline=True,
                hint="Zeilen „- Name (Rolle): Hinweis“ oder format_spec-JSON aus der Werkbank",
            ),
            FieldSpec(
                key="passages",
                label="Belegstellen",
                multiline=True,
                hint="Text mit [Block-ID]-Kopfzeilen oder Blöcke als JSON aus der Werkbank",
            ),
            FieldSpec(key="beat_position", label="Position"),
            FieldSpec(key="beat_total", label="Abschnitte gesamt"),
            FieldSpec(key="beat_summary_line", label="Zusammenfassung (Zeile)"),
            FieldSpec(key="register", label="Register"),
            FieldSpec(key="language", label="Sprache"),
            FieldSpec(
                key="audience",
                label="Zielgruppe",
                multiline=True,
                hint="Text oder audience_spec-JSON aus der Werkbank",
            ),
            FieldSpec(
                key="opening_closing",
                label="Einstieg/Schluss",
                multiline=True,
                hint="nur im ersten und letzten Abschnitt",
            ),
        ]

    def validate_setup(self, setup: BaseModel) -> list[str]:
        assert isinstance(setup, VSSetup)
        try:
            render(setup.user_template, setup.fields)
        except TemplateError as exc:
            return [str(exc)]
        return []

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult:
        assert isinstance(setup, VSSetup)
        options = VSOptions(k=setup.k)
        fields = normalise_fields(setup.fields)
        rendered = render(setup.user_template, fields)
        speakers = speaker_names(fields.get("speakers", ""))
        passages = parse_passages(fields.get("passages", ""))
        # VS answers are JSON by construction; the toggle does not apply here.
        settings = setup.settings.model_copy(update={"structured": True})

        warnings: list[str] = []
        if rendered.unused:
            warnings.append("fields not used by the template: " + ", ".join(rendered.unused))
        instruction, instruction_warnings = render_vs_instruction(setup.vs_instruction, options)
        warnings.extend(instruction_warnings)

        # A provider error here fails the run; the traces keep what was sent.
        llm.node_name = VS_NODE
        completion = llm.complete(
            settings.request(
                system=vs_system_prompt(setup.base_prompt, instruction),
                user=rendered.text,
                json_schema=vs_schema(),
            )
        )
        vs: dict[str, Any] = {"drafts": [], "warnings": list(completion.warnings), "error": None}
        try:
            parsed = parse_completion(completion, options, speakers=speakers or None)
        except VSParseError as exc:
            vs["error"] = str(exc)
            vs["raw"] = completion.text
        else:
            vs["warnings"].extend(parsed.warnings)
            vs["drafts"] = [
                _draft(
                    f"vs:{candidate.index}",
                    "vs",
                    candidate.index,
                    candidate.segments,
                    passages,
                    probability=candidate.probability,
                    flags=candidate.flags,
                )
                for candidate in parsed.candidates
            ]

        llm.node_name = BASELINE_NODE
        baseline: dict[str, Any] = {"drafts": [], "warnings": [], "error": None}
        try:
            plain = llm.complete(
                settings.request(
                    system=baseline_system_prompt(setup.base_prompt),
                    user=rendered.text,
                    json_schema=baseline_schema(),
                )
            )
            baseline["warnings"].extend(plain.warnings)
            segments = draft_segments(plain.json_payload(), speakers or None)
            if not segments:
                raise LLMError("Die Antwort enthält keine verwertbaren Segmente.")
            baseline["drafts"] = [_draft("baseline:0", "baseline", 0, segments, passages)]
        except LLMError as exc:
            baseline["error"] = str(exc)

        drafts = len(vs["drafts"])
        return ExperimentResult(
            output_json={
                "variant": "standard",
                "k": options.k,
                "word_budget": _word_budget(fields),
                "citation_check": bool(passages),
                "vs": vs,
                "baseline": baseline,
                "cost": summarize(llm.traces, drafts).model_dump(),
            },
            warnings=warnings,
        )

    def items(self, output_json: dict[str, Any]) -> list[ItemRef]:
        refs: list[ItemRef] = []
        for side in ("vs", "baseline"):
            for draft in (output_json.get(side) or {}).get("drafts") or []:
                refs.append(
                    ItemRef(
                        item=draft["item"],
                        title=f"VS #{draft['index'] + 1}" if side == "vs" else "Ohne VS",
                        meta={
                            "vs_source": side,
                            "vs_index": draft["index"],
                            "probability": draft.get("probability"),
                            "k": output_json.get("k"),
                            "variant": output_json.get("variant"),
                            "words": draft.get("words"),
                        },
                    )
                )
        return refs

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_beat_source(session, store, source)


register_experiment(VerbalizedSampling())
