"""Beat data for prompt experiments, shared by every script-shaped experiment.

``beat_fields`` produces the named values that the script node's user message
is built from, so a template written with these placeholders renders exactly
what production sends for that beat. ``load_beat_source`` reads them from a
finished run; ``SAMPLE_BEAT`` lets an experiment run before any run is loaded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.experiments.base import BeatOut, SourceIn, SourceOut
from app.models import Document, Run
from app.pipeline.bench import coerce_value, load_values
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.document import Block, ParsedDocument
from app.schemas.pipeline import AudienceSpec, Beat, FormatSpec, Outline, Script

#: The script node's user message (``nodes/script._beat_prompt``) as a template.
BEAT_TEMPLATE = (
    "Beat {{beat_position}} of {{beat_total}}: {{beat_title}}\n"
    "{{beat_summary_line}}Word budget for this beat: about {{word_budget}} words.\n"
    "Register: {{register}}\n"
    "Document language: {{language}}\n"
    "Audience: {{audience}}\n"
    "{{opening_closing}}\n"
    "Speakers:\n{{speakers}}\n\n"
    "Passages you may draw facts from:\n{{passages}}"
)

#: A small, self-contained beat so an experiment runs before any run is loaded.
SAMPLE_BEAT: dict[str, str] = {
    "beat_position": "2",
    "beat_total": "5",
    "beat_title": "Die Lichtreaktion",
    "beat_summary_line": "",
    "word_budget": "220",
    "register": "formal",
    "language": "de",
    "audience": (
        "Lernende, die den Stoff zum ersten Mal hören und ihn anschließend erklären können sollen."
    ),
    "opening_closing": "",
    "speakers": (
        "- Moderator (asks the questions a listener would ask): Curious and precise. Asks "
        "one thing at a time, never lectures, and never answers the question it just asked.\n"
        "- Expertin (explains the material): Explains in plain language, gives one concrete "
        "example per idea, and says plainly when the source does not settle a question."
    ),
    "passages": (
        "[b14]\nDie Lichtreaktion findet an den Thylakoidmembranen der Chloroplasten statt. "
        "Chlorophyll absorbiert dort vor allem rotes und blaues Licht.\n\n"
        "[b15]\nDie aufgenommene Lichtenergie wird genutzt, um Wasser zu spalten. Dabei "
        "entsteht Sauerstoff als Nebenprodukt.\n\n"
        "[b16]\nDie Energie wird in Form von ATP und NADPH zwischengespeichert und "
        "anschließend im Calvin-Zyklus verwendet."
    ),
}


def beat_fields(
    parsed: ParsedDocument,
    outline: Outline,
    format_spec: FormatSpec,
    audience_spec: AudienceSpec,
    beat_index: int,
) -> dict[str, str]:
    """The values ``_beat_prompt`` puts into the message for one beat."""
    beat = outline.beats[beat_index]
    total = len(outline.beats)
    blocks = {b.id: b for b in parsed.blocks}
    beat_blocks: list[Block] = [blocks[bid] for bid in beat.block_ids if bid in blocks]

    opening = ""
    if beat_index == 0 and format_spec.opening:
        opening = f"\nThis is the first beat. Opening guidance: {format_spec.opening}\n"
    closing = ""
    if beat_index == total - 1 and format_spec.closing:
        closing = f"\nThis is the final beat. Closing guidance: {format_spec.closing}\n"

    return {
        "beat_position": str(beat_index + 1),
        "beat_total": str(total),
        "beat_title": beat.title,
        "beat_summary_line": f"Beat summary: {beat.summary}\n" if beat.summary else "",
        "word_budget": str(beat.word_budget),
        "register": format_spec.register,
        "language": parsed.language,
        "audience": audience_spec.description,
        "opening_closing": f"{opening}{closing}",
        "speakers": "\n".join(
            f"- {s.name} ({s.role})" + (f": {s.voice_note}" if s.voice_note else "")
            for s in format_spec.speakers
        ),
        "passages": "\n\n".join(f"[{b.id}]\n{b.llm_text()}" for b in beat_blocks),
    }


def load_beat_source(session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
    """Fields for one beat of a finished run, plus the run's beats to choose from.

    Raises ``LookupError`` when the run is missing or has no outline yet.
    """
    run = session.get(Run, source.run_id)
    if run is None:
        raise LookupError(f"run '{source.run_id}' does not exist")
    try:
        values, _nodes, _document = load_values(
            session,
            store,
            run_id=run.id,
            keys=["parsed", "outline", "format_spec", "audience_spec", "script"],
            include_payload=True,
        )
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    payloads: dict[str, Any] = {v.key: v.payload for v in values if v.payload is not None}
    for needed in ("parsed", "outline", "format_spec", "audience_spec"):
        if needed not in payloads:
            raise LookupError(f"run '{run.id}' has no {needed} yet")

    parsed = coerce_value("parsed", payloads["parsed"])
    outline = coerce_value("outline", payloads["outline"])
    format_spec = coerce_value("format_spec", payloads["format_spec"])
    audience_spec = coerce_value("audience_spec", payloads["audience_spec"])
    if not outline.beats:
        raise LookupError(f"run '{run.id}' has an empty outline")

    index = 0
    if source.beat_id is not None:
        ids = [beat.id for beat in outline.beats]
        if source.beat_id not in ids:
            raise LookupError(f"beat '{source.beat_id}' is not in run '{run.id}'")
        index = ids.index(source.beat_id)
    beat: Beat = outline.beats[index]

    document = session.get(Document, run.document_id)
    reference: list[dict[str, Any]] = []
    if "script" in payloads:
        script: Script = coerce_value("script", payloads["script"])
        reference = [
            {"speaker": s.speaker, "text": s.text, "kind": s.kind}
            for s in script.segments
            if s.beat_id == beat.id
        ]

    return SourceOut(
        fields=beat_fields(parsed, outline, format_spec, audience_spec, index),
        beats=[
            BeatOut(
                id=b.id,
                title=b.title,
                word_budget=b.word_budget,
                passage_count=len(b.block_ids),
            )
            for b in outline.beats
        ],
        source={
            "run_id": run.id,
            "document_id": run.document_id,
            "document_title": (document.title or document.filename) if document else None,
            "beat_id": beat.id,
            "beat_title": beat.title,
            "beat_position": index + 1,
            "beat_total": len(outline.beats),
            "reference": reference,
        },
    )
