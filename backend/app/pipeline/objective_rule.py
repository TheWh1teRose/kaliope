"""How a learning objective is formulated.

The objectives node and the series planner both use this text and this parser,
so a goal is a checkable change for the listener, derived from the desired
outcome, limited by the episode's time, and classified at the lowest honest
Bloom level.
"""

from __future__ import annotations

from typing import Any

from app.schemas.pipeline import BLOOM_LEVELS, BloomLevel

OBJECTIVE_FORMULATION_RULE = """\
An objective describes what changes for the listener after they have heard the
episode — a capability they did not have before, stated so it can be checked.
It is not a topic, a chapter title, or a summary of the source.

You are given a desired outcome. That is the only source. Derive every
objective from it. The document is there so you can make an objective concrete
and refuse ones the material cannot support; it is not a second source of
objectives. Do not invent a goal the desired outcome does not imply.

You are also given a time budget: how long the episode will actually last.
That is a hard constraint on what can change. Write only objectives a listener
can honestly reach in that time. A short episode cannot produce apply,
evaluate or create unless the desired outcome is already that narrow — and
even then, usually only one such objective. Prefer fewer objectives over a
list that would need a lecture. If the desired outcome is larger than the
time allows, keep the achievable core and leave the rest out; do not dilute
it into a catalogue of unfinishable goals.

Classify each objective on Bloom's taxonomy, using the lowest honest level:

- remember: recall a fact, term or sequence
- understand: explain, paraphrase or give the idea in their own words
- apply: use the idea in a familiar situation
- analyse: break a situation into parts and see how they relate
- evaluate: judge against a criterion
- create: produce something new from the ideas

Do not inflate the level. A short episode that should leave the listener able
to explain a mechanism is 'understand', not 'create'. Not every level needs to
appear. Prefer fewer, sharper objectives over a full taxonomy for its own sake.

Write the objectives in the language of the desired outcome, as change
statements (what the listener can do or explain afterwards).\
"""

DOCUMENT_OBJECTIVES_NOTE = (
    "The document states its own objectives. Use them only to make a "
    "desired-outcome objective concrete; do not copy them as a second list."
)

#: Fields a formulated goal must carry. ``id`` is assigned by the node.
FORMULATED_GOAL_PROPERTIES: dict[str, Any] = {
    "text": {"type": "string"},
    "bloom_level": {"type": "string", "enum": list(BLOOM_LEVELS)},
    "derivation": {"type": "string"},
}
FORMULATED_GOAL_REQUIRED = ["text", "bloom_level"]

#: Spellings a model commonly returns that still name a real Bloom level.
_BLOOM_ALIASES: dict[str, BloomLevel] = {
    "remember": "remember",
    "knowledge": "remember",
    "understand": "understand",
    "comprehension": "understand",
    "apply": "apply",
    "application": "apply",
    "analyse": "analyse",
    "analyze": "analyse",
    "analysis": "analyse",
    "evaluate": "evaluate",
    "evaluation": "evaluate",
    "create": "create",
    "synthesis": "create",
}


def desired_outcome_source(desired_outcome: str) -> str:
    """The line that names the only source of an objective."""
    return f"Desired outcome (the source — derive every objective from this):\n{desired_outcome}"


def time_budget_constraint(minutes: float) -> str:
    """The line that caps ambition at the episode's length."""
    return (
        "Time budget (constraint — only write objectives a listener can "
        f"reach in this time): {minutes:.1f} minutes"
    )


def bloom_level(raw: Any) -> BloomLevel | None:
    """Snap a model label onto the taxonomy, or refuse one that is not a level."""
    if raw is None:
        return None
    return _BLOOM_ALIASES.get(str(raw).strip().lower())


def formulated_goal(entry: Any) -> tuple[str, BloomLevel, str | None] | None:
    """A usable goal, or nothing when the text or Bloom level does not hold.

    ``analyze`` becomes ``analyse``. An empty text or an unknown level is
    dropped rather than stored as a topic heading.
    """
    if not isinstance(entry, dict):
        return None
    text = str(entry.get("text", "")).strip()
    level = bloom_level(entry.get("bloom_level"))
    if not text or level is None:
        return None
    derivation = str(entry.get("derivation") or "").strip() or None
    return text, level, derivation
