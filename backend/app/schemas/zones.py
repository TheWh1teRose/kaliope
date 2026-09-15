"""Zone taxonomy (§5.6) — the single shared definition.

The taxonomy is data, not control flow. Nothing in the codebase may branch on a
specific zone with a ``switch``-like construct; consumers ask the table for the
properties they care about (``is_narratable``, ``salience``) so that adding a
zone is a one-line change here (§15).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Zone(StrEnum):
    BODY = "body"
    HEADING = "heading"
    EMPHASIS_CALLOUT = "emphasis_callout"
    OBJECTIVE = "objective"
    EXERCISE = "exercise"
    ANSWER_SPACE = "answer_space"
    REFERENCE = "reference"
    NAVIGATION = "navigation"
    CAPTION = "caption"
    DATA_TABLE = "data_table"
    METADATA = "metadata"
    OTHER = "other"


class ZoneSpec(BaseModel):
    zone: Zone
    meaning: str
    #: Whether the zone's text may be turned into narration.
    narratable: bool
    #: ``True`` when the zone informs the model but is never narrated verbatim.
    context_only: bool = False
    #: Relative weight passed to the selection prompt. ``0.0`` for zones that
    #: never reach it.
    salience: float


ZONE_TABLE: dict[Zone, ZoneSpec] = {
    spec.zone: spec
    for spec in [
        ZoneSpec(
            zone=Zone.BODY,
            meaning="Running prose, the substance of the document",
            narratable=True,
            salience=1.0,
        ),
        ZoneSpec(
            zone=Zone.HEADING,
            meaning="Section or subsection title",
            narratable=False,
            context_only=True,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.EMPHASIS_CALLOUT,
            meaning="Highlighted key-point box the author marked as important",
            narratable=True,
            salience=2.0,
        ),
        ZoneSpec(
            zone=Zone.OBJECTIVE,
            meaning="Stated learning objectives or goals",
            narratable=True,
            salience=1.0,
        ),
        ZoneSpec(
            zone=Zone.EXERCISE,
            meaning="Tasks, questions or assignments addressed to the learner",
            narratable=False,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.ANSWER_SPACE,
            meaning="Blank forms or tables for the learner to fill in",
            narratable=False,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.REFERENCE,
            meaning="Bibliography, citations, further reading",
            narratable=False,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.NAVIGATION,
            meaning="Table of contents, index, page furniture",
            narratable=False,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.CAPTION,
            meaning="Figure or table caption",
            narratable=True,
            salience=1.0,
        ),
        ZoneSpec(
            zone=Zone.DATA_TABLE,
            meaning="Content-bearing table",
            narratable=True,
            salience=1.0,
        ),
        ZoneSpec(
            zone=Zone.METADATA,
            meaning="Cover, imprint, legal notices",
            narratable=False,
            salience=0.0,
        ),
        ZoneSpec(
            zone=Zone.OTHER,
            meaning="Unclassifiable",
            narratable=False,
            salience=0.0,
        ),
    ]
}

#: Zone assigned when the classifier is not confident enough (§5.6). Losing real
#: content is worse than a reviewer deleting a stray line, so the fallback is the
#: coverage-maximising choice.
UNCERTAIN_FALLBACK_ZONE = Zone.BODY

#: Blocks classified below this confidence are relabelled to the fallback zone
#: and flagged ``zone_uncertain``.
ZONE_CONFIDENCE_THRESHOLD = 0.6


def is_narratable(zone: Zone) -> bool:
    return ZONE_TABLE[zone].narratable


def is_context_only(zone: Zone) -> bool:
    return ZONE_TABLE[zone].context_only


def salience_of(zone: Zone) -> float:
    return ZONE_TABLE[zone].salience


def narratable_zones() -> set[Zone]:
    return {z for z, spec in ZONE_TABLE.items() if spec.narratable}


def zone_catalogue() -> list[dict[str, object]]:
    """Serialisable taxonomy for the API and the console."""
    return [
        {
            "zone": spec.zone.value,
            "meaning": spec.meaning,
            "narratable": spec.narratable,
            "context_only": spec.context_only,
            "salience": spec.salience,
        }
        for spec in ZONE_TABLE.values()
    ]
