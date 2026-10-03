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
from app.pipeline.nodes.script import ScriptInput, beat_prompt_fields
from app.schemas.document import Block, ParsedDocument
from app.schemas.pipeline import AudienceSpec, Beat, FormatSpec, Outline, Script, Segment

#: The script node's user message (``nodes/script._beat_message``) as a template.
BEAT_TEMPLATE = (
    "{{running_order}}\n"
    "Register: {{register}}\n"
    "Document language: {{language}}\n"
    "Audience: {{audience}}\n\n"
    "Speakers:\n{{speakers}}\n\n"
    "{{written_so_far}}"
    "▶ You are writing beat {{beat_position}} of {{beat_total}}: {{beat_title}}\n"
    "{{beat_summary_line}}Word budget for this beat: about {{word_budget}} words.\n"
    "{{opening_closing}}{{transition}}\n"
    "Passages you may draw facts from:\n{{passages}}"
)

#: A small, self-contained beat so an experiment runs before any run is loaded.
SAMPLE_BEAT: dict[str, str] = {
    "running_order": (
        "Episode running order (5 beats, about 1100 words):\n"
        "1. Wovon lebt eine Pflanze? · 200 words\n"
        "2. Die Lichtreaktion · 220 words\n"
        "3. Der Calvin-Zyklus · 260 words\n"
        "4. Wohin der Zucker wandert · 240 words\n"
        "5. Rückblick · 180 words\n"
    ),
    "written_so_far": (
        "Written so far, for continuity only. Do not cite it; facts must still come "
        "from this beat's passages.\n\n"
        "[Beat 1: Wovon lebt eine Pflanze?]\n"
        "Moderator: Wir essen, Tiere fressen. Aber wovon lebt eigentlich eine Eiche?\n"
        "Expertin: Stell dir das Blatt wie eine kleine Küche vor: Die Zutaten sind Wasser "
        "und Kohlenstoffdioxid, und gekocht wird mit Licht.\n"
        "Moderator: Und wo steht in dieser Küche der Herd?\n\n"
    ),
    "transition": (
        "\nThe previous beat ended with:\nModerator: Und wo steht in dieser Küche der Herd?\n"
        "Pick up from there.\n"
        "\nNext comes beat 3: Der Calvin-Zyklus. Leave its content to it.\n"
    ),
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
    script: Script | None = None,
) -> dict[str, str]:
    """The values the script node puts into the message for one beat.

    ``script`` supplies the text of the earlier beats, as the node would have
    seen it while writing this beat; without it the beat reads as if nothing
    was written before.
    """
    blocks = {b.id: b for b in parsed.blocks}
    beat = outline.beats[beat_index]
    beat_blocks: list[Block] = [blocks[bid] for bid in beat.block_ids if bid in blocks]
    inp = ScriptInput(
        parsed=parsed, outline=outline, format_spec=format_spec, audience_spec=audience_spec
    )
    return beat_prompt_fields(
        inp, beat_index, beat_blocks, _written_before(outline, script, beat_index)
    )


def _written_before(
    outline: Outline, script: Script | None, beat_index: int
) -> list[tuple[int, Beat, list[Segment]]]:
    if script is None:
        return []
    written: list[tuple[int, Beat, list[Segment]]] = []
    for position, beat in enumerate(outline.beats[:beat_index]):
        segments = [s for s in script.segments if s.beat_id == beat.id]
        if segments:
            written.append((position, beat, segments))
    return written


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
    script: Script | None = None
    if "script" in payloads:
        script = coerce_value("script", payloads["script"])
        reference = [
            {"speaker": s.speaker, "text": s.text, "kind": s.kind}
            for s in script.segments
            if s.beat_id == beat.id
        ]

    return SourceOut(
        fields=beat_fields(parsed, outline, format_spec, audience_spec, index, script),
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
            "name": run.name,
            "document_title": (document.title or document.filename) if document else None,
            "beat_id": beat.id,
            "beat_title": beat.title,
            "beat_position": index + 1,
            "beat_total": len(outline.beats),
            "reference": reference,
        },
    )
