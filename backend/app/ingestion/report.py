"""The ingestion report (§5.8).

``ingestion_confidence`` is a score over four measured properties — structure,
anchor integrity, zone certainty and text density — with a small contribution
from reading order. Two conditions override it to ``low`` outright, because
they make the output unusable rather than merely uncertain: anchors that do not
round-trip, and no narratable text at all.

Every lost point produces a sentence in ``confidence_reasons``, so a document
that reports ``low`` always explains why. That is what replaces "unrecognised
template" with a measurement meaningful on a first encounter with any PDF.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.document import Confidence, IngestionReport

#: Thresholds. Each is a property of the *parse*, never of a document family.
_ANCHOR_GOOD = 0.99
_ANCHOR_ACCEPTABLE = 0.95
_UNCERTAIN_GOOD = 0.15
_UNCERTAIN_ACCEPTABLE = 0.35
_DENSITY_GOOD = 150.0
_DENSITY_ACCEPTABLE = 60.0
_READING_ORDER_GOOD = 0.90
#: A document whose pages are mostly image is reported, not rejected — §6.1 is
#: where an image-dominant document fails honestly.
_VISUAL_HEAVY = 0.60

_HIGH_SCORE = 7
_MEDIUM_SCORE = 4


@dataclass
class ConfidenceAssessment:
    level: Confidence
    score: int
    reasons: list[str]


def assess(report: IngestionReport) -> ConfidenceAssessment:
    reasons: list[str] = []
    score = 0

    if report.structure_confidence == "high":
        score += 2
    elif report.structure_confidence == "medium":
        score += 1
        reasons.append(
            "Section structure was inferred from typography rather than an embedded "
            "outline, so section boundaries may be approximate."
        )
    else:
        reasons.append(
            "No section structure could be recovered; the document is treated as flat. "
            "Selection and outlining still work, but they cannot follow the author's "
            "own organisation."
        )

    if report.anchor_integrity >= _ANCHOR_GOOD:
        score += 2
    elif report.anchor_integrity >= _ANCHOR_ACCEPTABLE:
        score += 1
        reasons.append(
            f"Anchor round-trip succeeded for {report.anchor_integrity:.1%} of sampled "
            "spans; a small share of citations may highlight an imprecise rectangle."
        )
    else:
        reasons.append(
            f"Anchor round-trip succeeded for only {report.anchor_integrity:.1%} of "
            "sampled spans. Citations cannot be trusted to point at the right text."
        )

    if report.zone_uncertain_ratio <= _UNCERTAIN_GOOD:
        score += 2
    elif report.zone_uncertain_ratio <= _UNCERTAIN_ACCEPTABLE:
        score += 1
        reasons.append(
            f"{report.zone_uncertain_ratio:.0%} of blocks were classified with low "
            "confidence and defaulted to body text; review them in the console."
        )
    else:
        reasons.append(
            f"{report.zone_uncertain_ratio:.0%} of blocks were classified with low "
            "confidence. Exercises, forms and references may be narrated as content."
        )

    if report.text_density >= _DENSITY_GOOD:
        score += 2
    elif report.text_density >= _DENSITY_ACCEPTABLE:
        score += 1
        reasons.append(
            f"Text density is {report.text_density:.0f} words per page, which is thin "
            "for a learning document."
        )
    else:
        reasons.append(
            f"Text density is only {report.text_density:.0f} words per page. There may "
            "not be enough extractable prose to narrate."
        )

    if report.reading_order_confidence >= _READING_ORDER_GOOD:
        score += 1
    else:
        reasons.append(
            f"Reading order confidence is {report.reading_order_confidence:.0%}; the "
            "page layout was ambiguous in places and block order may be wrong."
        )

    if report.visual_content_ratio >= _VISUAL_HEAVY:
        reasons.append(
            f"{report.visual_content_ratio:.0%} of the page area is image content. Any "
            "meaning carried by diagrams is not available to the pipeline."
        )

    level: Confidence
    if report.anchor_integrity < _ANCHOR_ACCEPTABLE:
        level = "low"
    elif report.narratable_words == 0:
        level = "low"
        reasons.append("No narratable text was found at all.")
    elif score >= _HIGH_SCORE:
        level = "high"
    elif score >= _MEDIUM_SCORE:
        level = "medium"
    else:
        level = "low"

    if level == "high" and not reasons:
        reasons.append("All measured ingestion properties are within their good ranges.")

    return ConfidenceAssessment(level=level, score=score, reasons=reasons)


def finalize(report: IngestionReport) -> IngestionReport:
    """Fill in ``ingestion_confidence`` and its explanation."""
    assessment = assess(report)
    report.ingestion_confidence = assessment.level
    report.confidence_reasons = assessment.reasons
    return report


def character_conservation(report: IngestionReport) -> float:
    """Unexplained share of extracted characters (INV-3).

    Returns the absolute difference between what was extracted and what the
    ledger accounts for, as a fraction of the extracted total.
    """
    if report.chars_extracted <= 0:
        return 0.0
    removed = sum(report.chars_removed.values())
    added = sum(report.chars_added.values())
    accounted = report.chars_in_blocks + removed - added
    return abs(report.chars_extracted - accounted) / report.chars_extracted
