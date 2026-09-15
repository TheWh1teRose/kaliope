"""Block assembly and the ``char_map`` (§4, §5.2).

Runs are first grouped into visual lines, then lines into blocks. Every break
rule is geometric or typographic — a gap larger than the page's own line
rhythm, a change of font size, a change of column. Nothing here knows what the
document is about.

The result carries ``char_map``: for every character of ``Block.text``, the run
it came from and that run's x-boundaries. Anchor resolution (§4) is then a
lookup, not a search, which is what makes INV-4 achievable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.extract import PageGeometry
from app.ingestion.runs import EditLedger
from app.ingestion.tables import TableRegion, region_for
from app.schemas.document import BBox, Block, RunSpan, TableBlock, TextRun

#: Font-size change beyond this fraction starts a new block.
_FONT_SIZE_BREAK = 0.15
#: Vertical gap beyond this multiple of the page's median line pitch is a break.
_GAP_BREAK = 1.6
#: A line ending short of the column's right edge by more than this fraction
#: terminates a paragraph.
_RAGGED_END = 0.18
#: Indentation beyond this fraction of the column width starts a paragraph.
_INDENT_BREAK = 0.04

_LIST_MARKER = re.compile(
    r"^\s*(?:[-•·◦▪‣*‧⁃]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|[ivxIVX]{1,4}[.)])\s+"
)


@dataclass
class _Line:
    runs: list[TextRun]
    table: TableRegion | None

    @property
    def page(self) -> int:
        return self.runs[0].page

    @property
    def column(self) -> int:
        return self.runs[0].column

    @property
    def x0(self) -> float:
        return min(r.bbox[0] for r in self.runs)

    @property
    def x1(self) -> float:
        return max(r.bbox[2] for r in self.runs)

    @property
    def y0(self) -> float:
        return min(r.bbox[1] for r in self.runs)

    @property
    def y1(self) -> float:
        return max(r.bbox[3] for r in self.runs)

    @property
    def font_size(self) -> float:
        sizes = sorted(r.font_size for r in self.runs)
        return sizes[len(sizes) // 2]

    @property
    def bold(self) -> bool:
        bold_chars = sum(len(r.text) for r in self.runs if r.bold)
        total = sum(len(r.text) for r in self.runs) or 1
        return bold_chars / total >= 0.6

    @property
    def in_filled_rect(self) -> bool:
        return any(r.in_filled_rect for r in self.runs)

    @property
    def text(self) -> str:
        return " ".join(r.text for r in self.runs)


@dataclass
class Assembly:
    blocks: list[Block]
    ledger: EditLedger = field(default_factory=EditLedger)
    #: Number of blocks that ended up backed by a table grid.
    table_blocks: int = 0
    #: Blocks whose runs all carried per-character geometry.
    blocks_with_geometry: int = 0


def assemble(
    runs: list[TextRun],
    pages: list[PageGeometry],
    tables: list[TableRegion] | None = None,
) -> Assembly:
    tables = tables or []
    ledger = EditLedger()
    lines = _group_lines(runs, tables)
    pitch = _median_pitch(lines)
    groups = _group_blocks(lines, pitch, pages)

    blocks: list[Block] = []
    table_blocks = 0
    with_geometry = 0

    for ordinal, group in enumerate(groups):
        block = _build_block(ordinal, group, ledger)
        if block is None:
            continue
        if block.table is not None:
            table_blocks += 1
        if block.char_map and all(
            len(span.char_x) == span.char_end - span.char_start + 1 for span in block.char_map
        ):
            with_geometry += 1
        blocks.append(block)

    for index, block in enumerate(blocks):
        block.ordinal = index

    # Exact separator accounting for INV-3: whatever the block texts contain
    # beyond the sum of their runs was inserted here, and whatever is missing
    # was trimmed here.
    run_chars = sum(len(r.text) for r in runs)
    block_chars = sum(len(b.text) for b in blocks)
    delta = block_chars - run_chars
    if delta > 0:
        ledger.add("block_separators", delta)
    elif delta < 0:
        ledger.remove("block_trim", -delta)

    return Assembly(
        blocks=blocks,
        ledger=ledger,
        table_blocks=table_blocks,
        blocks_with_geometry=with_geometry,
    )


# --------------------------------------------------------------- grouping


def _group_lines(runs: list[TextRun], tables: list[TableRegion]) -> list[_Line]:
    lines: list[_Line] = []
    current: list[TextRun] = []
    current_table: TableRegion | None = None

    for run in runs:
        table = region_for(tables, run.page, run.bbox)
        if not current:
            current, current_table = [run], table
            continue
        previous = current[-1]
        same_line = (
            run.page == previous.page
            and run.column == previous.column
            and table is current_table
            and _overlap(run.bbox, previous.bbox) >= 0.5
        )
        if same_line:
            current.append(run)
        else:
            lines.append(_Line(runs=current, table=current_table))
            current, current_table = [run], table

    if current:
        lines.append(_Line(runs=current, table=current_table))
    return lines


def _overlap(a: BBox, b: BBox) -> float:
    top = max(a[1], b[1])
    bottom = min(a[3], b[3])
    if bottom <= top:
        return 0.0
    smaller = min(a[3] - a[1], b[3] - b[1])
    return (bottom - top) / smaller if smaller > 0 else 0.0


def _median_pitch(lines: list[_Line]) -> float:
    gaps: list[float] = []
    for previous, current in zip(lines, lines[1:], strict=False):
        if previous.page != current.page or previous.column != current.column:
            continue
        gap = current.y0 - previous.y0
        if 0 < gap < 200:
            gaps.append(gap)
    if not gaps:
        return 14.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def _group_blocks(lines: list[_Line], pitch: float, pages: list[PageGeometry]) -> list[list[_Line]]:
    geometry = {p.number: p for p in pages}
    groups: list[list[_Line]] = []
    current: list[_Line] = []

    for line in lines:
        if not current:
            current = [line]
            continue
        if _breaks(current, line, pitch, geometry):
            groups.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        groups.append(current)
    return groups


def _breaks(
    group: list[_Line], line: _Line, pitch: float, geometry: dict[int, PageGeometry]
) -> bool:
    previous = group[-1]

    if line.table is not previous.table:
        return True
    if line.table is not None:
        # Everything inside one detected grid belongs to one block.
        return False
    if line.page != previous.page or line.column != previous.column:
        return True
    if previous.in_filled_rect != line.in_filled_rect:
        return True
    if previous.bold != line.bold:
        return True

    base = max(previous.font_size, line.font_size, 1.0)
    if abs(previous.font_size - line.font_size) / base > _FONT_SIZE_BREAK:
        return True

    gap = line.y0 - previous.y1
    if gap > _GAP_BREAK * max(pitch - (previous.y1 - previous.y0), 1.0):
        return True

    if _LIST_MARKER.match(line.text):
        return True

    width = _group_width(group, geometry.get(line.page))
    if width > 0:
        if (line.x0 - _group_left(group)) / width > _INDENT_BREAK:
            return True
        right_edge = max(_group_right(group), line.x1)
        if (right_edge - previous.x1) / width > _RAGGED_END:
            return True

    return False


def _group_left(group: list[_Line]) -> float:
    return min(line.x0 for line in group)


def _group_right(group: list[_Line]) -> float:
    return max(line.x1 for line in group)


def _group_width(group: list[_Line], page: PageGeometry | None) -> float:
    width = _group_right(group) - _group_left(group)
    if width > 0:
        return width
    return page.width if page else 0.0


# ------------------------------------------------------------- assembling


def _build_block(ordinal: int, group: list[_Line], ledger: EditLedger) -> Block | None:
    pieces: list[str] = []
    char_map: list[RunSpan] = []
    cursor = 0
    previous_run: TextRun | None = None

    for line in group:
        for run in line.runs:
            if previous_run is not None:
                separator = "" if previous_run.tight_join_next else " "
                if separator:
                    pieces.append(separator)
                    cursor += len(separator)
            start = cursor
            pieces.append(run.text)
            cursor += len(run.text)
            char_map.append(
                RunSpan(
                    char_start=start,
                    char_end=cursor,
                    page=run.page,
                    bbox=run.bbox,
                    char_x=list(run.char_x) if run.has_geometry() else [],
                )
            )
            previous_run = run

    text = "".join(pieces).strip()
    if not text:
        return None

    # ``strip`` can only remove leading/trailing separator characters, which are
    # never part of a run span, so the map stays aligned. Guard it anyway.
    leading = len("".join(pieces)) - len("".join(pieces).lstrip())
    if leading:
        char_map = [
            span.model_copy(
                update={
                    "char_start": span.char_start - leading,
                    "char_end": span.char_end - leading,
                }
            )
            for span in char_map
        ]
        char_map = [s for s in char_map if s.char_end > 0]

    first = group[0]
    table_block: TableBlock | None = group[0].table.table if group[0].table else None

    return Block(
        id=f"b{ordinal:06d}",
        ordinal=ordinal,
        text=text,
        page=first.page,
        bboxes=_page_bboxes(group),
        char_map=char_map,
        font_size=first.font_size,
        bold=any(line.bold for line in group),
        in_filled_rect=any(line.in_filled_rect for line in group),
        table=table_block,
    )


def _page_bboxes(group: list[_Line]) -> list[tuple[int, BBox]]:
    by_page: dict[int, list[float]] = {}
    for line in group:
        box = by_page.setdefault(line.page, [line.x0, line.y0, line.x1, line.y1])
        box[0] = min(box[0], line.x0)
        box[1] = min(box[1], line.y0)
        box[2] = max(box[2], line.x1)
        box[3] = max(box[3], line.y1)
    return [(page, (b[0], b[1], b[2], b[3])) for page, b in sorted(by_page.items())]
