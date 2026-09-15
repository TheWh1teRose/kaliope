"""Structure inference (§5.5).

Three tiers, attempted in order, each recording how much it is worth trusting:
the embedded PDF outline, typographic clustering, and a flat fallback. Nothing
downstream may require a hierarchy to exist — a flat document is a first-class
outcome, not an error (§5.1).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.ingestion.runs import normalization_key
from app.schemas.document import Block, Confidence, Section, StructureSource

#: Share of outline titles that must be locatable on their stated page for the
#: outline to be considered internally consistent.
_OUTLINE_MATCH_SHARE = 0.6
#: A heading is short. Measured in words, which is language-neutral.
_MAX_HEADING_WORDS = 14
#: Font size must exceed the body size by this factor to signal a heading.
_HEADING_SIZE_FACTOR = 1.12
#: Documents longer than this get page-range chunks instead of one flat section.
_CHUNK_THRESHOLD_PAGES = 40
_CHUNK_PAGES = 10


@dataclass
class StructureResult:
    sections: list[Section] | None
    source: StructureSource
    confidence: Confidence
    #: Diagnostics for the ingestion report.
    detail: str = ""


def infer(
    blocks: list[Block],
    outline: list[tuple[int, str, int]],
    page_count: int,
) -> StructureResult:
    if not blocks:
        return StructureResult(sections=None, source="flat", confidence="low", detail="no blocks")

    from_outline = _from_outline(blocks, outline, page_count)
    if from_outline is not None:
        return from_outline

    from_typography = _from_typography(blocks)
    if from_typography is not None:
        return from_typography

    return _flat(blocks, page_count)


# ------------------------------------------------------------- tier 1


def _from_outline(
    blocks: list[Block], outline: list[tuple[int, str, int]], page_count: int
) -> StructureResult | None:
    entries = [
        (level, title, page)
        for level, title, page in outline
        if 0 <= page < page_count and title.strip()
    ]
    if len(entries) < 2:
        return None
    if any(page < entries[i - 1][2] for i, (_, _, page) in enumerate(entries) if i):
        return None

    keys_by_page: dict[int, list[tuple[str, Block]]] = defaultdict(list)
    for block in blocks:
        keys_by_page[block.page].append((normalization_key(block.text), block))

    matches: list[tuple[int, str, Block]] = []
    unmatched = 0
    for level, title, page in entries:
        located = _locate_title(title, keys_by_page, page)
        if located is None:
            unmatched += 1
            continue
        matches.append((level, title, located))

    if not matches or len(matches) / len(entries) < _OUTLINE_MATCH_SHARE:
        return None

    matches.sort(key=lambda m: m[2].ordinal)
    for level, _, block in matches:
        block.heading_level = level
    sections = _sections_from_starts(
        blocks,
        [(level, title, block.ordinal) for level, title, block in matches],
    )

    confidence: Confidence = "high" if unmatched == 0 else "medium"
    return StructureResult(
        sections=sections,
        source="outline",
        confidence=confidence,
        detail=f"{len(matches)}/{len(entries)} outline entries located in the text",
    )


def _locate_title(
    title: str, keys_by_page: dict[int, list[tuple[str, Block]]], page: int
) -> Block | None:
    key = normalization_key(title)
    if not key:
        return None
    # An outline destination is routinely one page off; accept the neighbours.
    for candidate_page in (page, page + 1, page - 1):
        for block_key, block in keys_by_page.get(candidate_page, []):
            if block_key == key or (len(key) >= 6 and key in block_key):
                return block
    return None


# ------------------------------------------------------------- tier 2


def _from_typography(blocks: list[Block]) -> StructureResult | None:
    body_size = _body_font_size(blocks)
    if body_size <= 0:
        return None

    candidates: list[tuple[int, Block, float]] = []
    for index, block in enumerate(blocks):
        if block.table is not None:
            continue
        words = block.word_count()
        if words == 0 or words > _MAX_HEADING_WORDS:
            continue
        larger = block.font_size >= body_size * _HEADING_SIZE_FACTOR
        emphasised = block.bold and block.font_size >= body_size * 0.98
        if not (larger or emphasised):
            continue
        if not _followed_by_body(blocks, index, body_size):
            continue
        candidates.append((index, block, block.font_size))

    if len(candidates) < 2:
        return None

    # Distinct heading sizes, largest first, become levels 1..n.
    sizes = sorted({round(size, 1) for _, _, size in candidates}, reverse=True)
    level_of = {size: level + 1 for level, size in enumerate(sizes)}

    starts: list[tuple[int, str, int]] = []
    for index, block, size in candidates:
        level = level_of[round(size, 1)]
        block.heading_level = level
        starts.append((level, block.text.strip(), index))

    sections = _sections_from_starts(blocks, starts)
    coverage = sum(len(s.block_ids) for s in sections) / max(len(blocks), 1)
    confidence: Confidence = "medium" if coverage >= 0.5 else "low"
    return StructureResult(
        sections=sections,
        source="typographic",
        confidence=confidence,
        detail=(
            f"{len(candidates)} heading candidates over body size {body_size:.1f}; "
            f"{coverage:.0%} of blocks covered"
        ),
    )


def _body_font_size(blocks: list[Block]) -> float:
    """Modal font size weighted by characters — the body text (§5.5)."""
    weights: dict[float, int] = defaultdict(int)
    for block in blocks:
        weights[round(block.font_size, 1)] += len(block.text)
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _followed_by_body(blocks: list[Block], index: int, body_size: float) -> bool:
    for following in blocks[index + 1 : index + 4]:
        if following.word_count() > _MAX_HEADING_WORDS:
            return True
        if abs(following.font_size - body_size) / max(body_size, 1.0) <= 0.05:
            return True
    return False


# ------------------------------------------------------------- tier 3


def _flat(blocks: list[Block], page_count: int) -> StructureResult:
    if page_count <= _CHUNK_THRESHOLD_PAGES:
        section = Section(
            id="s000",
            title="",
            level=1,
            ordinal=0,
            block_ids=[b.id for b in blocks],
            page_start=blocks[0].page,
            page_end=blocks[-1].page,
        )
        return StructureResult(
            sections=[section],
            source="flat",
            confidence="low",
            detail="no outline and no typographic heading pattern; single implicit section",
        )

    sections: list[Section] = []
    for ordinal, start_page in enumerate(range(0, page_count, _CHUNK_PAGES)):
        end_page = min(start_page + _CHUNK_PAGES - 1, page_count - 1)
        members = [b for b in blocks if start_page <= b.page <= end_page]
        if not members:
            continue
        sections.append(
            Section(
                id=f"s{ordinal:03d}",
                title="",
                level=1,
                ordinal=len(sections),
                block_ids=[b.id for b in members],
                page_start=start_page,
                page_end=end_page,
            )
        )
    return StructureResult(
        sections=sections or None,
        source="flat",
        confidence="low",
        detail=f"no recoverable structure; chunked into {len(sections)} page ranges",
    )


# ------------------------------------------------------------- shared


def _sections_from_starts(blocks: list[Block], starts: list[tuple[int, str, int]]) -> list[Section]:
    """Build sections from ``(level, title, start_ordinal)`` triples.

    Blocks before the first heading belong to no section — they are cover or
    front matter, and forcing them into section 1 would misattribute them.
    """
    starts = sorted(starts, key=lambda s: s[2])
    sections: list[Section] = []
    stack: list[Section] = []

    for position, (level, title, start_index) in enumerate(starts):
        end_index = starts[position + 1][2] if position + 1 < len(starts) else len(blocks)
        members = blocks[start_index:end_index]
        section = Section(
            id=f"s{position:03d}",
            title=title,
            level=level,
            ordinal=position,
            block_ids=[b.id for b in members],
            page_start=members[0].page if members else None,
            page_end=members[-1].page if members else None,
        )
        while stack and stack[-1].level >= level:
            stack.pop()
        section.parent_id = stack[-1].id if stack else None
        stack.append(section)
        sections.append(section)
        for block in members:
            block.section_id = section.id

    return sections


def validate(blocks: list[Block], sections: list[Section] | None) -> list[str]:
    """INV-8 checks, returned as warnings so the report can carry them."""
    problems: list[str] = []
    seen: set[str] = set()
    for index, block in enumerate(blocks):
        if not block.text.strip():
            problems.append(f"block {block.id} is empty")
        if block.id in seen:
            problems.append(f"duplicate block id {block.id}")
        seen.add(block.id)
        if block.ordinal != index:
            problems.append(f"block {block.id} is out of order")

    if sections is None:
        return problems

    known = {b.id for b in blocks}
    assigned: dict[str, str] = {}
    for section in sections:
        for block_id in section.block_ids:
            if block_id not in known:
                problems.append(f"section {section.id} references unknown block {block_id}")
            if block_id in assigned:
                problems.append(
                    f"block {block_id} belongs to both {assigned[block_id]} and {section.id}"
                )
            assigned[block_id] = section.id
    return problems
