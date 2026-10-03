"""The ``content_budget`` node (§6.1).

This is what makes an image-dominant or thin document fail honestly instead of
producing invented filler, and it works entirely from measured properties. No
model call, no document identity.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.lang.resources import words_per_minute
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import DEFAULT_DIALOGUE_EXPANSION, ContentBudget, EpisodeBrief

#: Recorded on the budget for existing flows. The ceiling no longer divides by it.
DEFAULT_MIN_COMPRESSION = 2.5
#: Below this many supportable minutes the run fails rather than padding.
MIN_VIABLE_MINUTES = 3.0


def supportable_minutes(narratable_words: int, words_per_minute: int, expansion: float) -> float:
    """Podcast minutes the words can carry, including dialogue.

    A plain read-through is ``narratable_words / words_per_minute``. The
    expansion stretches that so the episode can stop and explain.
    """
    if words_per_minute <= 0 or expansion <= 0:
        raise ValueError("speaking rate and dialogue expansion must be greater than zero")
    return narratable_words / words_per_minute * expansion


def source_word_budget(minutes: float, words_per_minute: int, expansion: float) -> int:
    """Source words one episode should receive so its length has room for dialogue."""
    if words_per_minute <= 0 or expansion <= 0:
        raise ValueError("speaking rate and dialogue expansion must be greater than zero")
    return max(1, int(round(minutes * words_per_minute / expansion)))


class ContentBudgetInput(BaseModel):
    parsed: ParsedDocument
    target_minutes: int
    #: Set for one episode of a series: only its own passages count.
    episode_brief: EpisodeBrief | None = None


class ContentBudgetNode:
    name = "content_budget"
    title = "Inhaltsbudget"
    version = "1.1"
    Input: type[BaseModel] = ContentBudgetInput
    Output: type[BaseModel] = ContentBudget
    produces = "budget"

    doc = NodeDoc(
        summary="Decides how many minutes the document can actually support, and refuses "
        "the run when the answer is 'not enough'.",
        detail=[
            "Counts the narratable words in the parse — headings, exercises, page furniture "
            "and reference lists do not count, because they will never be spoken. In a "
            "series episode only that episode's own passages are counted.",
            "A plain read-through is that word count divided by the speaking rate for the "
            "document's language. Dialogue expansion multiplies it (default 2.5), so the "
            "podcast has room for questions, explanation and back-and-forth instead of "
            "reading the document down. A 15-minute episode is sized from about 810 German "
            "source words, not from 2,025.",
            "If that ceiling is under three minutes the run fails here with a verdict of "
            "'insufficient' and an explanation naming the numbers — narratable words, image "
            "share of the page area, words per page. That failure is the point of this node.",
            "For a single episode, a requested length above the ceiling is clamped down and "
            "the verdict becomes 'clamped'. A series episode that clears three minutes keeps "
            "the requested length, so raising the episode count spreads the source instead "
            "of shortening every episode.",
        ],
        inputs={
            "parsed": "Narratable word count, language and the ingestion report.",
            "target_minutes": "The length the run asked for.",
            "episode_brief": "Only in a series: the episode's share of the plan. Then only "
            "the episode's own passages count towards the budget.",
        },
        output="A ContentBudget: the effective target in minutes and words, the compression "
        "ratio, the verdict, and a sentence explaining how it was reached.",
        failure_modes=[
            "verdict 'insufficient' — the document supports under three minutes. Typical for "
            "slide decks, scans without OCR, and worksheets that are mostly answer space.",
            "min_compression or dialogue_expansion set to zero or below — a configuration "
            "error, refused outright.",
        ],
        cost="No model call. Pure arithmetic over the parse, and always identical for the "
        "same document.",
    )

    params = [
        NodeParam(
            key="dialogue_expansion",
            label="Dialogue expansion",
            type="float",
            default=DEFAULT_DIALOGUE_EXPANSION,
            minimum=1.0,
            maximum=6.0,
            description=(
                "How many times longer the podcast may run than a plain read-through of the "
                "narratable words. 2.5 means 1,000 German words (about 7.4 minutes read "
                "aloud at 135 words per minute) support about 18.5 minutes of podcast, and "
                "a 15-minute episode takes about 810 source words."
            ),
        ),
        NodeParam(
            key="min_compression",
            label="Minimum compression",
            type="float",
            default=DEFAULT_MIN_COMPRESSION,
            minimum=1.0,
            maximum=20.0,
            description=(
                "Kept so existing flows stay valid, and recorded on the budget. The "
                "supportable length uses dialogue expansion; raising this no longer "
                "shortens the episode."
            ),
        ),
    ]

    def run(self, inp: ContentBudgetInput, ctx: NodeContext) -> ContentBudget:
        min_compression = float(ctx.get("min_compression", DEFAULT_MIN_COMPRESSION))
        if min_compression <= 0:
            raise NodeError("min_compression must be greater than zero")
        expansion = float(ctx.get("dialogue_expansion", DEFAULT_DIALOGUE_EXPANSION))
        if expansion <= 0:
            raise NodeError("dialogue_expansion must be greater than zero")

        narratable = inp.parsed.narratable_word_count()
        brief = inp.episode_brief
        if brief is not None:
            own = set(brief.episode.block_ids)
            narratable = sum(
                block.word_count() for block in inp.parsed.narratable_blocks() if block.id in own
            )
        wpm = words_per_minute(inp.parsed.language)
        max_supportable = supportable_minutes(narratable, wpm, expansion)

        report = inp.parsed.report
        if max_supportable < MIN_VIABLE_MINUTES:
            explanation = (
                f"The document holds {narratable} narratable words. At {wpm} words per "
                f"minute and a {expansion:g}× dialogue expansion, that supports "
                f"at most {max_supportable:.1f} minutes of podcast — below the "
                f"{MIN_VIABLE_MINUTES:.0f}-minute floor. "
                f"{report.visual_content_ratio:.0%} of the page area is image content, "
                f"and text density is {report.text_density:.0f} words per page."
            )
            ctx.progress("insufficient narratable content")
            raise NodeError(explanation, verdict="insufficient")

        verdict = "ok"
        explanation = (
            f"{narratable} narratable words support up to {max_supportable:.1f} minutes "
            f"at {wpm} words per minute with a {expansion:g}× dialogue expansion."
        )
        target = float(inp.target_minutes)
        if target > max_supportable and brief is None:
            verdict = "clamped"
            explanation = (
                f"Requested {inp.target_minutes} minutes, but {narratable} narratable "
                f"words only support {max_supportable:.1f} minutes at {wpm} words per "
                f"minute with a {expansion:g}× dialogue expansion. The target was clamped."
            )
            target = max_supportable
            ctx.progress(f"target clamped to {target:.1f} minutes")
        elif target > max_supportable:
            explanation = (
                f"{narratable} narratable words support up to {max_supportable:.1f} minutes "
                f"at {wpm} words per minute with a {expansion:g}× dialogue expansion. "
                f"The episode keeps the requested {inp.target_minutes} minutes so questions, "
                f"explanation and dialogue have room."
            )

        target_words = target * wpm
        compression = narratable / target_words if target_words else 0.0

        return ContentBudget(
            narratable_words=narratable,
            words_per_minute=wpm,
            min_compression=min_compression,
            dialogue_expansion=expansion,
            max_supportable_minutes=round(max_supportable, 2),
            requested_minutes=inp.target_minutes,
            target_minutes=round(target, 2),
            compression_ratio=round(compression, 2),
            verdict=verdict,  # type: ignore[arg-type]
            explanation=explanation,
        )


register_node(ContentBudgetNode())
