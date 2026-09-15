"""Anchor resolution and the round-trip check (§4, §5.2, INV-4).

An anchor is a character range in a block. Resolving it walks ``char_map``,
clips each overlapping run span to the requested range and turns it into a
rectangle. Re-extracting the PDF text inside those rectangles and normalizing it
must reproduce the anchored string — that is the round-trip, and it is measured
on every parse and reported as ``anchor_integrity``.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.extract import extract_text_in_rects
from app.ingestion.normalize import normalize_text
from app.schemas.document import (
    Anchor,
    AnchorRect,
    BBox,
    Block,
    ParsedDocument,
    ResolvedAnchor,
)

#: Rectangles on the same page whose vertical ranges overlap this much are
#: merged into one highlight.
_MERGE_OVERLAP = 0.5

_WS = re.compile(r"\s+")


def rects_for(block: Block, char_start: int, char_end: int) -> list[AnchorRect]:
    """Highlight rectangles covering ``block.text[char_start:char_end]``."""
    if char_end <= char_start:
        return []
    hits: list[AnchorRect] = []
    for span in block.char_map:
        if span.char_end <= char_start or span.char_start >= char_end:
            continue
        start = max(span.char_start, char_start)
        end = min(span.char_end, char_end)
        hits.append(AnchorRect(page=span.page, bbox=span.sub_bbox(start, end)))

    if not hits and block.bboxes:
        # No char_map (degraded extraction): the whole block is the best honest
        # answer. Never return nothing — a citation must always be showable.
        return [AnchorRect(page=page, bbox=bbox) for page, bbox in block.bboxes]

    return _merge(hits)


def _merge(rects: list[AnchorRect]) -> list[AnchorRect]:
    merged: list[AnchorRect] = []
    for rect in rects:
        target = None
        for existing in merged:
            if existing.page != rect.page:
                continue
            if _vertical_overlap(existing.bbox, rect.bbox) >= _MERGE_OVERLAP:
                target = existing
                break
        if target is None:
            merged.append(AnchorRect(page=rect.page, bbox=rect.bbox))
        else:
            target.bbox = (
                min(target.bbox[0], rect.bbox[0]),
                min(target.bbox[1], rect.bbox[1]),
                max(target.bbox[2], rect.bbox[2]),
                max(target.bbox[3], rect.bbox[3]),
            )
    return merged


def _vertical_overlap(a: BBox, b: BBox) -> float:
    top = max(a[1], b[1])
    bottom = min(a[3], b[3])
    if bottom <= top:
        return 0.0
    smaller = min(a[3] - a[1], b[3] - b[1])
    return (bottom - top) / smaller if smaller > 0 else 0.0


def resolve(parsed: ParsedDocument, anchor: Anchor) -> ResolvedAnchor:
    block = parsed.block_by_id(anchor.block_id)
    if block is None:
        return ResolvedAnchor(anchor=anchor, text="", rects=[], resolved=False)
    start = max(0, anchor.char_start)
    end = min(len(block.text), anchor.char_end)
    if end <= start:
        return ResolvedAnchor(anchor=anchor, text="", rects=[], resolved=False)
    return ResolvedAnchor(
        anchor=anchor,
        text=block.text[start:end],
        rects=rects_for(block, start, end),
        resolved=True,
    )


def anchor_is_valid(parsed: ParsedDocument, anchor: Anchor) -> tuple[bool, str | None]:
    """Gate G1's predicate: does the anchor point at real characters?"""
    if anchor.document_id != parsed.document_id:
        return False, "anchor references a different document"
    if anchor.parse_version != parsed.parse_version:
        return False, "anchor references a different parse version"
    block = parsed.block_by_id(anchor.block_id)
    if block is None:
        return False, f"block '{anchor.block_id}' does not exist"
    if anchor.char_start >= anchor.char_end:
        return False, "char_start must be smaller than char_end"
    if anchor.char_start < 0 or anchor.char_end > len(block.text):
        return False, (
            f"range [{anchor.char_start}, {anchor.char_end}) falls outside block "
            f"of length {len(block.text)}"
        )
    return True, None


# ------------------------------------------------------------ round-trip


@dataclass
class RoundTripResult:
    sampled: int
    matched: int
    #: Samples skipped because the block carried no per-character geometry.
    degraded: int = 0

    @property
    def integrity(self) -> float:
        return self.matched / self.sampled if self.sampled else 1.0


def _comparable(text: str, language: str) -> str:
    """Bring both sides of the round-trip onto the same footing.

    The stored text has been normalized; the re-extracted text has not. Applying
    the same character-level normalization and then dropping whitespace entirely
    is the honest comparison — it tolerates the line breaks that clipping
    reintroduces without tolerating a different word.
    """
    return _WS.sub("", normalize_text(text, language)).casefold()


def verify_round_trip(
    parsed: ParsedDocument,
    pdf_path: Path,
    *,
    samples: int = 200,
    seed: int = 20240811,
) -> RoundTripResult:
    """Sample ``(block, start, end)`` triples and re-extract them (INV-4)."""
    candidates = [b for b in parsed.blocks if len(b.text) >= 12 and b.char_map]
    if not candidates:
        return RoundTripResult(sampled=0, matched=0)

    rng = random.Random(seed)
    result = RoundTripResult(sampled=0, matched=0)

    for _ in range(samples):
        block = rng.choice(candidates)
        length = len(block.text)
        span = min(length, rng.randint(10, 80))
        start = rng.randint(0, max(0, length - span))
        end = min(length, start + span)
        expected = block.text[start:end]
        if not expected.strip():
            continue

        rects = rects_for(block, start, end)
        if not rects:
            result.sampled += 1
            continue

        actual = extract_text_in_rects(pdf_path, [(r.page, r.bbox) for r in rects])
        result.sampled += 1
        if _comparable(expected, parsed.language) in _comparable(actual, parsed.language):
            result.matched += 1

    return result
