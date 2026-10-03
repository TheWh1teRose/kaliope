"""Experiment "Audio ausprobieren": the audio tagging step on a short script.

It starts from exactly what the ``audio_script`` node sends for one beat: its
system prompt and schema, and its user message as a template over named fields
(``pipeline/audio_tags.AUDIO_TEMPLATE``). The lines can be typed
(``Moderator: …``) with any speakers, or loaded from a beat of a finished run.
One run is one model call; the answer goes through the same word guard as in
production and comes back as an ``AudioScript``. The node would ask once more for
refused lines; the experiment shows the first answer as it is, so a prompt can
be judged on it.

This version only places tags. No audio is generated and no speech API is called.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.experiments.base import (
    BeatOut,
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
from app.pipeline.audio_tags import (
    AUDIO_SCHEMA,
    AUDIO_SYSTEM,
    AUDIO_TEMPLATE,
    DEFAULT_MAX_TAGS,
    lines_from_segments,
    parse_lines,
    prompt_fields,
    read_answer,
    speakers_text,
)
from app.pipeline.bench import coerce_value, load_values
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.audio_script import DEFAULT_MODEL
from app.schemas.audio import AudioScript
from app.schemas.pipeline import Script

#: Longest script one experiment run accepts; a longer one belongs in a run.
MAX_LINES_CHARS = 6000

#: A short exchange so the experiment runs before any run is loaded.
SAMPLE_FIELDS: dict[str, str] = {
    "speakers": (
        "- Moderator (asks the questions a listener would ask): Curious and precise. Asks "
        "one thing at a time, never lectures, and never answers the question it just asked.\n"
        "- Expertin (explains the material): Explains in plain language, gives one concrete "
        "example per idea, and says plainly when the source does not settle a question."
    ),
    "language": "de",
    "tag_language": "English",
    "max_tags": str(DEFAULT_MAX_TAGS),
    "context": "",
    "lines": (
        "Moderator: Wovon lebt eigentlich eine Pflanze, wenn sie nichts isst?\n"
        "Expertin (claim): Von Licht, Wasser und CO₂. Und davon landet erstaunlich wenig im "
        "Zucker: rund 1 bis 2 % des eingestrahlten Sonnenlichts.\n"
        "Moderator: Moment, nur 1 bis 2 Prozent? Das klingt nach ziemlich wenig.\n"
        "Expertin: Ist es auch. Aber stell dir vor, wie viel Licht jeden Tag auf ein "
        "Weizenfeld fällt – das reicht trotzdem.\n"
        "Moderator: Und wo genau passiert das, z. B. in einem Blatt?\n"
        "Expertin (claim): In den Chloroplasten, also in den grünen Körnchen in den Zellen "
        "des Blattes."
    ),
}


class AudioTagsExperiment(PromptExperiment):
    key = "audio_tags"
    title = "Audio ausprobieren"
    summary = (
        "Ein kurzes Skript mit eigenen Sprechern für die Sprachausgabe vorbereiten: "
        "Audio-Tags setzen, Zahlen ausschreiben lassen und jede Zeile gegen das Original "
        "prüfen. Noch ohne Audio."
    )
    target = "Audio-Skript-Schritt"
    version = "1"

    def defaults(self) -> BaseModel:
        return PromptSetup(
            system_prompt=AUDIO_SYSTEM,
            user_template=AUDIO_TEMPLATE,
            fields=dict(SAMPLE_FIELDS),
            settings=ModelSettings(model=DEFAULT_MODEL, temperature=0.3, max_tokens=8000),
            json_schema=copy.deepcopy(AUDIO_SCHEMA),
        )

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                key="lines",
                label="Skript",
                multiline=True,
                hint="eine Zeile pro Beitrag: „Sprecher: Text“, optional mit Id und Art, "
                "„[s1] Expertin (claim): …“",
            ),
            FieldSpec(
                key="speakers",
                label="Sprecher",
                multiline=True,
                hint="je Zeile „- Name (Rolle): Stimmbeschreibung“",
            ),
            FieldSpec(key="language", label="Sprache der Zeilen"),
            FieldSpec(key="tag_language", label="Sprache der Tags", hint="English oder German"),
            FieldSpec(key="max_tags", label="Höchstens Tags pro Zeile"),
            FieldSpec(
                key="context",
                label="Zeile davor",
                multiline=True,
                hint="nur für den Anschluss; wird nicht getaggt",
            ),
        ]

    def validate_setup(self, setup: BaseModel) -> list[str]:
        problems = super().validate_setup(setup)
        assert isinstance(setup, PromptSetup)
        text = setup.fields.get("lines", "")
        lines, line_problems = parse_lines(text)
        problems.extend(line_problems)
        if not lines and not line_problems:
            problems.append("the script has no lines")
        if len(text) > MAX_LINES_CHARS:
            problems.append(
                f"the script has {len(text)} characters; the experiment takes at most "
                f"{MAX_LINES_CHARS}"
            )
        if not _max_tags(setup.fields).isdigit():
            problems.append("max_tags must be a whole number")
        return problems

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult:
        assert isinstance(setup, PromptSetup)
        lines, _problems = parse_lines(setup.fields.get("lines", ""))
        # Typed lines get their ids here, so the model can answer per line.
        sent = setup.model_copy(
            update={
                "fields": {
                    **setup.fields,
                    "lines": "\n".join(line.formatted() for line in lines),
                }
            }
        )
        result = super().run(sent, llm, ctx)
        output = dict(result.output_json)
        warnings = list(result.warnings)
        payload = output.get("payload")
        if payload is None and not setup.settings.structured:
            try:
                payload = parse_json(result.text or "")
            except LLMError:
                warnings.append("the answer is not JSON, so it is shown as raw text")
            output["payload"] = payload

        max_tags = int(_max_tags(setup.fields))
        audio_lines, problems = read_answer(payload, lines, max_tags=max_tags)
        script = AudioScript(lines=audio_lines, model=setup.settings.model)
        if problems:
            warnings.append(
                f"{len(problems)} of {len(lines)} line(s) failed the word check; the node would "
                "ask once more and otherwise speak them untagged: " + ", ".join(problems)
            )
        output["audio_script"] = script.model_dump(mode="json")
        output["guard"] = {
            "lines": len(lines),
            "passed": len(lines) - len(problems),
            "tags": sum(len(line.tags) for line in audio_lines),
            "characters": script.character_count(),
        }
        return ExperimentResult(output_json=output, text=result.text, warnings=warnings)

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_audio_source(session, store, source)


def _max_tags(fields: dict[str, str]) -> str:
    return (fields.get("max_tags") or str(DEFAULT_MAX_TAGS)).strip()


def load_audio_source(session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    """One beat of a finished run's script as the node would send it.

    Raises ``LookupError`` when the run is missing or has no script yet.
    """
    run = session.get(Run, source.run_id)
    if run is None:
        raise LookupError(f"run '{source.run_id}' does not exist")
    try:
        values, _nodes, _document = load_values(
            session,
            store,
            run_id=run.id,
            keys=["parsed", "outline", "format_spec", "script"],
            include_payload=True,
        )
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    payloads: dict[str, Any] = {v.key: v.payload for v in values if v.payload is not None}
    for needed in ("format_spec", "script"):
        if needed not in payloads:
            raise LookupError(f"run '{run.id}' has no {needed} yet")

    script: Script = coerce_value("script", payloads["script"])
    format_spec = coerce_value("format_spec", payloads["format_spec"])
    if not script.segments:
        raise LookupError(f"run '{run.id}' has an empty script")
    language = coerce_value("parsed", payloads["parsed"]).language if "parsed" in payloads else "de"

    beats = _beat_list(
        script, coerce_value("outline", payloads["outline"]) if "outline" in payloads else None
    )
    ids = [beat.id for beat in beats]
    beat_id = source.beat_id or ids[0]
    if beat_id not in ids:
        raise LookupError(f"beat '{beat_id}' is not in run '{run.id}'")
    index = ids.index(beat_id)

    lines = lines_from_segments(s for s in script.segments if s.beat_id == beat_id)
    first = next(i for i, s in enumerate(script.segments) if s.beat_id == beat_id)
    previous = lines_from_segments(script.segments[first - 1 : first]) if first else []
    fields = prompt_fields(
        lines,
        speakers=speakers_text(format_spec),
        language=language,
        tag_language="en",
        max_tags=DEFAULT_MAX_TAGS,
        previous=previous[0] if previous else None,
    )
    # The tag settings stay as the person set them.
    fields.pop("tag_language")
    fields.pop("max_tags")

    document = session.get(Document, run.document_id)
    return SourceOut(
        fields=fields,
        beats=beats,
        source={
            "run_id": run.id,
            "document_id": run.document_id,
            "document_title": (document.title or document.filename) if document else None,
            "beat_id": beat_id,
            "beat_title": beats[index].title,
            "beat_position": index + 1,
            "beat_total": len(beats),
        },
    )


def _beat_list(script: Script, outline: Any) -> list[BeatOut]:
    """The beats that have lines, titled from the outline when there is one."""
    present: list[str] = []
    for segment in script.segments:
        if segment.beat_id not in present:
            present.append(segment.beat_id)
    titles = {beat.id: beat for beat in outline.beats} if outline is not None else {}
    counts = {beat_id: 0 for beat_id in present}
    for segment in script.segments:
        counts[segment.beat_id] += 1
    beats = []
    for beat_id in present:
        beat = titles.get(beat_id)
        beats.append(
            BeatOut(
                id=beat_id,
                title=beat.title if beat is not None else beat_id,
                word_budget=beat.word_budget if beat is not None else 0,
                passage_count=counts[beat_id],
            )
        )
    return beats


register_experiment(AudioTagsExperiment())
