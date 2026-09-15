"""Capability thresholds (§13.3).

These are aggregate over the corpus, never per document. A single hard PDF is
allowed to score badly; the pipeline as a whole is not.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.ingestion.anchors import verify_round_trip
from app.ingestion.runs import normalization_key
from app.schemas.zones import Zone
from tests.conftest import CORPUS, requires_corpus

pytestmark = [pytest.mark.corpus, requires_corpus()]

#: CAP-1
STRUCTURE_SHARE = 0.90
#: CAP-2
BOILERPLATE_RECALL = 0.90
BOILERPLATE_PAGE_SHARE = 0.50
#: CAP-3
ZONE_MACRO_F1 = 0.80
LABEL_MINIMUM = 100
LABEL_DOCUMENTS = 4
LABEL_FAMILIES = 3
#: CAP-5
ANCHOR_MEAN = 0.99
ANCHOR_FLOOR = 0.95

LABELS_PATH = Path(__file__).parent / "corpus" / "zone_labels.json"


def test_cap1_structure_confidence(parses: dict) -> None:
    """CAP-1: ≥90% of documents reach structure_confidence ≥ medium."""
    levels = [p.report.structure_confidence for p in parses.values()]
    ok = sum(1 for level in levels if level in {"high", "medium"})
    share = ok / len(levels)
    assert share >= STRUCTURE_SHARE, (
        f"only {share:.0%} of {len(levels)} documents reached medium or better ({Counter(levels)})"
    )


def test_cap2_boilerplate_removal(parses: dict) -> None:
    """CAP-2: ≥90% of lines repeated on ≥50% of pages are removed."""
    total_expected = 0
    total_removed = 0

    for document in CORPUS:
        parsed = parses[document.name]
        if parsed.page_count < 3:
            continue

        # A line that still appears on half the pages after removal is one the
        # stage should have caught.
        survivors: Counter[str] = Counter()
        for block in parsed.blocks:
            key = normalization_key(block.text)
            if key:
                survivors[(key, block.page)] = 1  # type: ignore[index]

        pages_by_key: Counter[str] = Counter()
        seen: set[tuple[str, int]] = set()
        for block in parsed.blocks:
            key = normalization_key(block.text)
            if not key or (key, block.page) in seen:
                continue
            seen.add((key, block.page))
            pages_by_key[key] += 1

        threshold = parsed.page_count * BOILERPLATE_PAGE_SHARE
        leaked = [key for key, count in pages_by_key.items() if count >= threshold]
        removed = len({normalization_key(text) for text in parsed.boilerplate if text.strip()})

        total_expected += len(leaked) + removed
        total_removed += removed

    if total_expected == 0:
        pytest.skip("no corpus document contains a line repeated on ≥50% of its pages")

    recall = total_removed / total_expected
    assert recall >= BOILERPLATE_RECALL, (
        f"removed {total_removed} of {total_expected} repeated lines ({recall:.0%})"
    )


def test_cap3_zone_classifier_macro_f1(parses: dict) -> None:
    """CAP-3: macro-F1 ≥ 0.80 against the hand-labelled set.

    The labels live in ``tests/corpus/zone_labels.json`` as
    ``{document_name: {block_id: zone}}`` — the only manual labelling in the
    suite. They must be spread across documents and families, not concentrated
    in one, which is what makes the number mean anything.
    """
    if not LABELS_PATH.exists():
        pytest.skip(
            "no tests/corpus/zone_labels.json. Hand-label ≥100 blocks across ≥4 "
            "documents from ≥3 families to activate CAP-3."
        )

    labels: dict[str, dict[str, str]] = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    families = {d.family for d in CORPUS if d.name in labels}
    total_labels = sum(len(v) for v in labels.values())

    assert total_labels >= LABEL_MINIMUM, f"only {total_labels} labelled blocks"
    assert len(labels) >= LABEL_DOCUMENTS, f"labels cover only {len(labels)} document(s)"
    assert len(families) >= LABEL_FAMILIES, f"labels cover only {len(families)} family/families"

    truth: list[str] = []
    predicted: list[str] = []
    for document_name, block_labels in labels.items():
        parsed = parses.get(document_name)
        if parsed is None:
            continue
        index = {b.id: b for b in parsed.blocks}
        for block_id, zone in block_labels.items():
            block = index.get(block_id)
            if block is None:
                continue
            truth.append(zone)
            predicted.append(block.zone.value)

    assert truth, "no labelled block matched a parsed block id"
    score = macro_f1(truth, predicted)
    assert score >= ZONE_MACRO_F1, f"zone classifier macro-F1 is {score:.2f}"


def test_cap4_tables_preserved(parses: dict) -> None:
    """CAP-4: detected tables keep their grid and a consistent column count.

    The ≥80% target in §13.3 is against tables found by visual inspection, which
    the suite cannot do. What it can check without a human is that no detected
    table was silently degraded into a text blob.
    """
    checked = 0
    for parsed in parses.values():
        for block in parsed.blocks:
            if block.table is None:
                continue
            checked += 1
            assert block.table.n_cols >= 2
            assert block.table.rows
            assert all(len(row) <= block.table.n_cols for row in block.table.rows)
            assert block.zone is Zone.DATA_TABLE
    if checked == 0:
        pytest.skip("no corpus document contains a detectable table")


def test_cap5_anchor_integrity(parses: dict) -> None:
    """CAP-5: mean anchor integrity ≥ 0.99, with no document below 0.95."""
    scores: dict[str, float] = {}
    for document in CORPUS:
        parsed = parses[document.name]
        result = verify_round_trip(parsed, document.path, samples=200)
        if result.sampled:
            scores[document.name] = result.integrity

    if not scores:
        pytest.skip("no document produced samplable anchors")

    mean = sum(scores.values()) / len(scores)
    worst = min(scores.items(), key=lambda kv: kv[1])
    assert worst[1] >= ANCHOR_FLOOR, f"{worst[0]} scored {worst[1]:.2%}"
    assert mean >= ANCHOR_MEAN, f"mean anchor integrity is {mean:.2%} ({scores})"


def macro_f1(truth: list[str], predicted: list[str]) -> float:
    classes = sorted(set(truth) | set(predicted))
    scores: list[float] = []
    for label in classes:
        tp = sum(1 for t, p in zip(truth, predicted, strict=True) if t == label and p == label)
        fp = sum(1 for t, p in zip(truth, predicted, strict=True) if t != label and p == label)
        fn = sum(1 for t, p in zip(truth, predicted, strict=True) if t == label and p != label)
        if tp == 0 and fp == 0 and fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores) if scores else 0.0
