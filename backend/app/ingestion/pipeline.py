"""The single generic ingestion pipeline (§5).

    extract → layout/reading order → repetition analysis → normalize
            → block assembly → structure inference → zone classification
            → table extraction → ingestion report

There are no branches on document identity anywhere in this module. Every stage
either succeeds, degrades explicitly, or records reduced confidence.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.ingestion import anchors
from app.ingestion import blocks as block_stage
from app.ingestion import extract as extract_stage
from app.ingestion import layout as layout_stage
from app.ingestion import normalize as normalize_stage
from app.ingestion import repetition as repetition_stage
from app.ingestion import report as report_stage
from app.ingestion import structure as structure_stage
from app.ingestion import tables as table_stage
from app.ingestion.runs import EditLedger
from app.ingestion.zones import ZoneClassifier
from app.ingestion.zones import apply as apply_zones
from app.lang.detect import detect_language
from app.lang.resources import UNKNOWN_LANGUAGE
from app.schemas.document import Block, IngestionReport, ParsedDocument, Section
from app.schemas.zones import Zone, is_context_only, is_narratable

logger = logging.getLogger(__name__)

#: Sampled anchor round-trips per parse (INV-4).
ANCHOR_SAMPLES = 200


@dataclass
class ParseOptions:
    document_id: str
    parse_version: int = 1
    #: Overrides the detected document language when the caller knows better.
    language_override: str | None = None
    anchor_samples: int = ANCHOR_SAMPLES
    #: Skip the round-trip check. Only for callers that will run it themselves.
    verify_anchors: bool = True


def parse(
    pdf_path: Path,
    options: ParseOptions,
    classifier: ZoneClassifier | None = None,
) -> ParsedDocument:
    ledger = EditLedger()
    warnings: list[str] = []

    # 1. extraction ------------------------------------------------------
    extraction = extract_stage.extract(pdf_path)
    ledger.remove("whitespace_only_runs", extraction.dropped_whitespace_chars)
    if not extraction.runs:
        warnings.append(
            "No extractable text was found. The document is probably a scan; this "
            "build does not perform OCR."
        )

    # 2. layout and reading order ---------------------------------------
    layout = layout_stage.analyze(extraction.runs, extraction.pages)
    if layout.raw_order_disagreement > 0.25:
        warnings.append(
            f"Geometric reading order disagreed with the extractor's own order for "
            f"{layout.raw_order_disagreement:.0%} of adjacent runs. The layout stage "
            "repaired the sequence; verify a few pages in the console."
        )

    # 3. repetition analysis --------------------------------------------
    repetition = repetition_stage.analyze(layout.runs, extraction.pages)
    ledger.remove("boilerplate", sum(len(r.text) for r in repetition.removed))
    warnings.extend(repetition.warnings)

    # 4. language detection ----------------------------------------------
    # Detected over every extracted run, not just the kept ones: language is a
    # property of the document, and a document whose body was entirely removed
    # as boilerplate would otherwise report an undetectable language on top of
    # the removal problem.
    sample = " ".join(r.text for r in layout.runs[:6000])
    detection = detect_language(sample)
    language = options.language_override or detection.language
    if detection.language == UNKNOWN_LANGUAGE and not options.language_override:
        warnings.append(
            "Document language could not be detected. Language-specific steps "
            "(speaking rate, readability, suspended-compound handling) fall back to "
            "documented defaults."
        )

    # 5. normalization ---------------------------------------------------
    normalized = normalize_stage.normalize_runs(repetition.kept, language)
    ledger.merge(normalized.ledger)

    # 6. tables ----------------------------------------------------------
    try:
        table_regions = table_stage.find_tables(pdf_path)
    except Exception:  # pragma: no cover - defensive
        logger.warning("table detection failed", exc_info=True)
        table_regions = []
        warnings.append("Table detection failed; tables were parsed as ordinary text.")

    # 7. block assembly --------------------------------------------------
    assembly = block_stage.assemble(normalized.runs, extraction.pages, table_regions)
    ledger.merge(assembly.ledger)
    document_blocks = assembly.blocks

    if not document_blocks:
        warnings.append("Block assembly produced no blocks.")

    if document_blocks and assembly.blocks_with_geometry / len(document_blocks) < 0.5:
        warnings.append(
            "Fewer than half of the blocks carry per-character geometry, so citation "
            "highlights fall back to whole-line rectangles."
        )

    # 8. structure inference ---------------------------------------------
    structure = structure_stage.infer(document_blocks, extraction.outline, len(extraction.pages))
    warnings.extend(structure_stage.validate(document_blocks, structure.sections))

    # 9. zone classification ---------------------------------------------
    page_sizes = [(p.width, p.height) for p in extraction.pages]
    classification = None
    if classifier is not None:
        classification = classifier.classify(
            document_blocks,
            structure.sections,
            _page_body_sizes(document_blocks),
            page_sizes,
        )
        apply_zones(document_blocks, classification)
        warnings.extend(classification.warnings)
    else:
        for block in document_blocks:
            block.zone = Zone.BODY
            block.zone_confidence = 0.0
            block.zone_uncertain = True
            block.salience = 1.0
        warnings.append(
            "Zone classification was skipped; all blocks default to body text and are "
            "flagged uncertain."
        )

    # 10. report ---------------------------------------------------------
    parsed = ParsedDocument(
        document_id=options.document_id,
        parse_version=options.parse_version,
        language=language,
        title=_title(extraction.metadata_title, document_blocks, structure.sections),
        page_count=len(extraction.pages),
        page_sizes=page_sizes,
        sections=structure.sections,
        blocks=document_blocks,
        objectives=_objectives(document_blocks),
        non_narratable_text=_non_narratable(document_blocks),
        boilerplate=repetition.boilerplate_texts,
        report=IngestionReport(
            language=language,
            language_confidence=detection.confidence,
            page_count=len(extraction.pages),
            extractable_words=sum(b.word_count() for b in document_blocks),
            narratable_words=sum(b.word_count() for b in document_blocks if is_narratable(b.zone)),
            visual_content_ratio=_mean([p.image_ratio for p in extraction.pages]),
            text_density=(
                sum(b.word_count() for b in document_blocks) / len(extraction.pages)
                if extraction.pages
                else 0.0
            ),
            structure_source=structure.source,
            structure_confidence=structure.confidence,
            section_count=len(structure.sections or []),
            zone_distribution=_zone_distribution(document_blocks),
            zone_uncertain_ratio=(
                sum(1 for b in document_blocks if b.zone_uncertain) / len(document_blocks)
                if document_blocks
                else 0.0
            ),
            table_count=assembly.table_blocks,
            boilerplate_lines_removed=len(repetition.removed),
            reading_order_confidence=layout.reading_order_confidence,
            warnings=warnings,
            chars_extracted=extraction.raw_char_count,
            chars_in_blocks=sum(len(b.text) for b in document_blocks),
            chars_removed=dict(ledger.removed),
            chars_added=dict(ledger.added),
        ),
    )

    # 11. anchor round-trip ----------------------------------------------
    if options.verify_anchors and document_blocks:
        round_trip = anchors.verify_round_trip(parsed, pdf_path, samples=options.anchor_samples)
        parsed.report.anchor_integrity = round(round_trip.integrity, 4)
    else:
        parsed.report.anchor_integrity = 1.0 if not document_blocks else 0.0

    conservation = report_stage.character_conservation(parsed.report)
    if conservation > 0.02:
        parsed.report.warnings.append(
            f"{conservation:.1%} of extracted characters are unaccounted for in the "
            "removal ledger; some text may have been lost without explanation."
        )

    report_stage.finalize(parsed.report)
    return parsed


# ---------------------------------------------------------------- helpers


def _page_body_sizes(document_blocks: list[Block]) -> dict[int, float]:
    """Modal font size per page, weighted by characters."""
    weights: dict[int, dict[float, int]] = defaultdict(lambda: defaultdict(int))
    for block in document_blocks:
        weights[block.page][round(block.font_size, 1)] += len(block.text)
    return {
        page: max(sizes.items(), key=lambda kv: kv[1])[0]
        for page, sizes in weights.items()
        if sizes
    }


def _zone_distribution(document_blocks: list[Block]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for block in document_blocks:
        counts[block.zone.value] += 1
    return dict(sorted(counts.items()))


def _objectives(document_blocks: list[Block]) -> list[str]:
    return [
        block.text.strip()
        for block in document_blocks
        if block.zone is Zone.OBJECTIVE and block.text.strip()
    ]


def _non_narratable(document_blocks: list[Block]) -> list[str]:
    """Text gate G3 must not find in the script.

    Headings are excluded: they are context-only rather than forbidden, and a
    script that names a section is doing the right thing.
    """
    return [
        block.text.strip()
        for block in document_blocks
        if not is_narratable(block.zone) and not is_context_only(block.zone) and block.text.strip()
    ]


def _title(
    metadata_title: str | None,
    document_blocks: list[Block],
    sections: list[Section] | None,
) -> str | None:
    if metadata_title:
        return metadata_title
    if sections:
        for section in sections:
            if section.title.strip():
                return section.title.strip()
    # Largest text on the first page is the best remaining guess.
    first_page = [b for b in document_blocks if b.page == 0 and b.word_count() <= 25]
    if not first_page:
        return None
    best = max(first_page, key=lambda b: (b.font_size, -b.ordinal))
    return best.text.strip() or None  # noqa: RET504


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
