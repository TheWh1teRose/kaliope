"""Universal invariants (§13.2).

Every test here is parameterised over the discovered corpus and must hold for
every document, including ones nobody has seen. No test names a file, and no
assertion encodes a count taken from a particular PDF.
"""

from __future__ import annotations

import hashlib

import pytest

from app.ingestion import extract as extract_stage
from app.ingestion import layout as layout_stage
from app.ingestion import normalize as normalize_stage
from app.ingestion import repetition as repetition_stage
from app.ingestion.anchors import verify_round_trip
from app.ingestion.report import character_conservation
from app.ingestion.structure import validate
from app.schemas.zones import ZONE_TABLE, Zone
from tests.conftest import CORPUS, CorpusDocument, parsed_document, requires_corpus

pytestmark = [pytest.mark.corpus, requires_corpus()]

#: INV-3 tolerance: nothing may vanish unexplained beyond this share.
CONSERVATION_TOLERANCE = 0.02
#: INV-4 sampled round-trip success rate.
ROUND_TRIP_FLOOR = 0.995


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv1_parsing_completes_with_blocks(document: CorpusDocument, parses: dict) -> None:
    """INV-1: parsing completes without exception and produces ≥1 block."""
    parsed = parses[document.name]
    assert parsed.blocks, "the parse produced no blocks"
    assert parsed.page_count >= 1


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv2_no_residual_hyphenation(document: CorpusDocument, parses: dict) -> None:
    """INV-2: no soft hyphen and no ``<letter>-\\n<letter>`` survives."""
    for block in parses[document.name].blocks:
        assert normalize_stage.SOFT_HYPHEN not in block.text, f"soft hyphen in {block.id}"
        assert not normalize_stage.has_residual_hyphenation(block.text), (
            f"line-break hyphenation survived in {block.id}"
        )


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv3_character_conservation(document: CorpusDocument, parses: dict) -> None:
    """INV-3: extracted characters equal block characters plus recorded changes."""
    report = parses[document.name].report
    gap = character_conservation(report)
    assert gap <= CONSERVATION_TOLERANCE, (
        f"{gap:.2%} of extracted characters are unaccounted for "
        f"(extracted={report.chars_extracted}, in_blocks={report.chars_in_blocks}, "
        f"removed={report.chars_removed}, added={report.chars_added})"
    )


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv4_anchor_round_trip(document: CorpusDocument, parses: dict) -> None:
    """INV-4: sampled anchors re-extract to the same string modulo whitespace."""
    parsed = parses[document.name]
    result = verify_round_trip(parsed, document.path, samples=200)
    if result.sampled == 0:
        pytest.skip("no block was long enough to sample an anchor from")
    assert result.integrity >= ROUND_TRIP_FLOOR, (
        f"anchor round-trip succeeded for {result.integrity:.2%} of {result.sampled} samples"
    )


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv5_zone_labelling_is_total(document: CorpusDocument, parses: dict) -> None:
    """INV-5: every block carries a taxonomy zone and a salience value."""
    for block in parses[document.name].blocks:
        assert isinstance(block.zone, Zone), f"{block.id} has a non-taxonomy zone"
        assert block.zone in ZONE_TABLE
        assert block.salience is not None
        assert block.salience >= 0.0


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv6_normalization_is_idempotent(document: CorpusDocument) -> None:
    """INV-6: ``normalize(normalize(x)) == normalize(x)``, geometry included."""
    extraction = extract_stage.extract(document.path)
    layout = layout_stage.analyze(extraction.runs, extraction.pages)
    kept = repetition_stage.analyze(layout.runs, extraction.pages).kept

    once = normalize_stage.normalize_runs(kept, "de").runs
    twice = normalize_stage.normalize_runs(once, "de").runs

    assert [r.text for r in once] == [r.text for r in twice]
    assert [r.char_x for r in once] == [r.char_x for r in twice]


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv7_parsing_is_deterministic(document: CorpusDocument) -> None:
    """INV-7: two parses of the same file produce identical hashes."""

    def digest(index: int) -> str:
        parsed = parsed_document(document)
        return hashlib.sha256(parsed.model_dump_json().encode("utf-8")).hexdigest()

    assert digest(0) == digest(1)


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv8_block_and_section_integrity(document: CorpusDocument, parses: dict) -> None:
    """INV-8: blocks are ordered, non-empty, and belong to at most one section."""
    parsed = parses[document.name]
    problems = validate(parsed.blocks, parsed.sections)
    assert not problems, f"structure integrity problems: {problems[:5]}"

    for index, block in enumerate(parsed.blocks):
        assert block.ordinal == index
        assert block.text.strip()

    if parsed.sections:
        known = {b.id for b in parsed.blocks}
        for section in parsed.sections:
            assert set(section.block_ids) <= known


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv9_report_is_populated_and_consistent(document: CorpusDocument, parses: dict) -> None:
    """INV-9: the report is complete and its confidence follows from its inputs."""
    from app.ingestion.report import assess

    report = parses[document.name].report
    assert report.language
    assert report.page_count >= 1
    assert report.structure_source in {"outline", "typographic", "flat"}
    assert report.structure_confidence in {"high", "medium", "low"}
    assert 0.0 <= report.anchor_integrity <= 1.0
    assert 0.0 <= report.zone_uncertain_ratio <= 1.0
    assert 0.0 <= report.visual_content_ratio <= 1.0
    assert report.confidence_reasons, "a confidence level must explain itself"

    recomputed = assess(report)
    assert recomputed.level == report.ingestion_confidence


@pytest.mark.parametrize("document", CORPUS, ids=str)
def test_inv10_no_unhandled_degradation(document: CorpusDocument, parses: dict) -> None:
    """INV-10: missing outline, tables, objectives or boilerplate still parses."""
    parsed = parses[document.name]
    # Whatever the document happens to lack, the consumer-facing shape is intact.
    assert parsed.objectives is not None
    assert parsed.non_narratable_text is not None
    assert parsed.boilerplate is not None
    assert parsed.blocks
    assert parsed.report is not None
    # A flat document reports no sections rather than an empty hierarchy that
    # downstream code would have to distinguish from a real one.
    if parsed.sections is not None:
        assert all(section.block_ids for section in parsed.sections)
