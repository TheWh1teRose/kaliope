"""Format specifications (§6.5).

Ships with exactly one instance, but it is data rather than prompt text: adding
a format is a new entry here, and nothing in the nodes changes.
"""

from __future__ import annotations

from app.schemas.pipeline import AudienceSpec, FormatSpec, SpeakerSpec

TWO_HOST_DIALOGUE = FormatSpec(
    id="two_host_dialogue",
    name="Zwei-Personen-Dialog",
    speakers=[
        SpeakerSpec(
            id="host",
            name="Moderator",
            role="asks the questions a listener would ask",
            voice_note=(
                "Curious and precise. Asks one thing at a time, never lectures, and "
                "never answers the question it just asked."
            ),
        ),
        SpeakerSpec(
            id="expert",
            name="Expertin",
            role="explains the material",
            voice_note=(
                "Explains in plain language, gives one concrete example per idea, and "
                "says plainly when the source does not settle a question."
            ),
        ),
    ],
    register="formal",
    target_minutes=15,
    opening="Name the topic and what the listener will be able to explain afterwards.",
    closing="Recap the through-line in a few sentences. Introduce nothing new.",
    beats_hint=(
        "One idea per beat, building on the previous one. Prefer depth on fewer ideas "
        "over coverage of many."
    ),
)

FORMATS: dict[str, FormatSpec] = {TWO_HOST_DIALOGUE.id: TWO_HOST_DIALOGUE}

DEFAULT_FORMAT_ID = TWO_HOST_DIALOGUE.id

#: Used when a run is created without an audience spec.
DEFAULT_AUDIENCE = AudienceSpec(
    description=(
        "Lernende, die den Stoff zum ersten Mal hören und ihn anschließend erklären können sollen."
    ),
    prior_knowledge="Grundkenntnisse des Fachgebiets, aber kein Detailwissen.",
    listening_context="Unterwegs, ohne die Möglichkeit mitzulesen.",
)


def get_format(format_id: str) -> FormatSpec:
    try:
        return FORMATS[format_id]
    except KeyError as exc:
        raise KeyError(
            f"unknown format '{format_id}'. Available: {', '.join(sorted(FORMATS))}"
        ) from exc
