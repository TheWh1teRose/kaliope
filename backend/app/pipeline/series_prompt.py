"""Prompt lines a series episode's nodes share: its content contract and the
running orders of the episodes before it.

Each helper returns no lines when there is nothing to say, so a plan made
before the contract existed, or the first episode, sends the prompt it sent
before.
"""

from __future__ import annotations

from app.schemas.pipeline import EarlierOutlines, EpisodePlan


def contract_lines(episode: EpisodePlan) -> list[str]:
    """What the episode explains, what it takes as known, what it leaves to later ones."""
    lines: list[str] = []
    if episode.owns:
        lines.append(
            "This episode owns these topics and is the one that explains them: "
            + "; ".join(episode.owns)
            + "."
        )
    if episode.assumes:
        lines.append(
            "Earlier episodes already explained these; recall them in a sentence at most, "
            "do not teach them again: " + "; ".join(episode.assumes) + "."
        )
    if episode.leaves_to:
        lines.append(
            "Later episodes explain these; mention them at most, do not explain them here: "
            + "; ".join(f"{h.topic} (episode {h.episode})" for h in episode.leaves_to)
            + "."
        )
    return lines


def earlier_outline_lines(earlier: EarlierOutlines | None) -> list[str]:
    """The running orders of the earlier episodes, and how this one continues them."""
    if earlier is None or not earlier.outlines:
        return []
    lines = [
        "The earlier episodes of the series already ran in this order. Continue from "
        "where they stopped: no beat teaches again what a beat below already taught; "
        "refer back to it instead, and build on it."
    ]
    for outline in earlier.outlines:
        lines.append(f"Episode {outline.index}: {outline.title}")
        lines.extend(
            f"  {position + 1}. {title}" + (f": {summary}" if summary else "")
            for position, (title, summary) in enumerate(outline.beats)
        )
    return lines
