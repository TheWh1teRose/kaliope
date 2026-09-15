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
from app.schemas.pipeline import ContentBudget

#: A grounded episode must select from meaningfully more material than it emits.
DEFAULT_MIN_COMPRESSION = 2.5
#: Below this many supportable minutes the run fails rather than padding.
MIN_VIABLE_MINUTES = 3.0


class ContentBudgetInput(BaseModel):
    parsed: ParsedDocument
    target_minutes: int


class ContentBudgetNode:
    name = "content_budget"
    title = "Inhaltsbudget"
    version = "1.0"
    Input: type[BaseModel] = ContentBudgetInput
    Output: type[BaseModel] = ContentBudget
    produces = "budget"

    doc = NodeDoc(
        summary="Decides how many minutes the document can actually support, and refuses "
        "the run when the answer is 'not enough'.",
        detail=[
            "Counts the narratable words in the parse — headings, exercises, page furniture "
            "and reference lists do not count, because they will never be spoken.",
            "Divides by the speaking rate for the document's language, then by the minimum "
            "compression. That gives the longest honestly supportable episode: an episode "
            "must be able to *choose* from more material than it emits, or the model starts "
            "inventing filler to reach the target.",
            "If that ceiling is under three minutes the run fails here with a verdict of "
            "'insufficient' and an explanation naming the numbers — narratable words, image "
            "share of the page area, words per page. That failure is the point of this node.",
            "If the requested length is above the ceiling but the document is viable, the "
            "target is clamped down to the ceiling and the verdict becomes 'clamped'. The run "
            "continues, shorter than asked for.",
        ],
        inputs={
            "parsed": "Narratable word count, language and the ingestion report.",
            "target_minutes": "The length the run asked for.",
        },
        output="A ContentBudget: the effective target in minutes and words, the compression "
        "ratio, the verdict, and a sentence explaining how it was reached.",
        failure_modes=[
            "verdict 'insufficient' — the document supports under three minutes. Typical for "
            "slide decks, scans without OCR, and worksheets that are mostly answer space.",
            "min_compression set to zero or below — a configuration error, refused outright.",
        ],
        cost="No model call. Pure arithmetic over the parse, and always identical for the "
        "same document.",
    )

    params = [
        NodeParam(
            key="min_compression",
            label="Minimum compression",
            type="float",
            default=DEFAULT_MIN_COMPRESSION,
            minimum=1.0,
            maximum=20.0,
            description=(
                "How much more material the episode must be able to choose from than it "
                "emits. 2.5 means a 15-minute episode needs roughly 37 minutes of narratable "
                "material behind it. Raising it makes the system pickier and refuses thinner "
                "documents; lowering it lets thin documents through at the cost of padding."
            ),
        ),
    ]

    def run(self, inp: ContentBudgetInput, ctx: NodeContext) -> ContentBudget:
        min_compression = float(ctx.get("min_compression", DEFAULT_MIN_COMPRESSION))
        if min_compression <= 0:
            raise NodeError("min_compression must be greater than zero")

        narratable = inp.parsed.narratable_word_count()
        wpm = words_per_minute(inp.parsed.language)
        max_supportable = narratable / wpm / min_compression

        report = inp.parsed.report
        if max_supportable < MIN_VIABLE_MINUTES:
            explanation = (
                f"The document holds {narratable} narratable words. At {wpm} words per "
                f"minute and a minimum compression of {min_compression}×, that supports "
                f"at most {max_supportable:.1f} minutes of grounded narration — below the "
                f"{MIN_VIABLE_MINUTES:.0f}-minute floor. "
                f"{report.visual_content_ratio:.0%} of the page area is image content, "
                f"and text density is {report.text_density:.0f} words per page."
            )
            ctx.progress("insufficient narratable content")
            raise NodeError(explanation, verdict="insufficient")

        verdict = "ok"
        explanation = (
            f"{narratable} narratable words support up to {max_supportable:.1f} minutes "
            f"at {wpm} words per minute with {min_compression}× compression."
        )
        target = float(inp.target_minutes)
        if target > max_supportable:
            verdict = "clamped"
            explanation = (
                f"Requested {inp.target_minutes} minutes, but {narratable} narratable "
                f"words only support {max_supportable:.1f} minutes at {wpm} words per "
                f"minute with {min_compression}× compression. The target was clamped."
            )
            target = max_supportable
            ctx.progress(f"target clamped to {target:.1f} minutes")

        target_words = target * wpm
        compression = narratable / target_words if target_words else 0.0

        return ContentBudget(
            narratable_words=narratable,
            words_per_minute=wpm,
            min_compression=min_compression,
            max_supportable_minutes=round(max_supportable, 2),
            requested_minutes=inp.target_minutes,
            target_minutes=round(target, 2),
            compression_ratio=round(compression, 2),
            verdict=verdict,  # type: ignore[arg-type]
            explanation=explanation,
        )


register_node(ContentBudgetNode())
