"""Table extraction (§5.7).

Tables are kept as grids. Flattening one into a text stream loses the column
association, and any anchor into the flattened text then mis-attributes cells —
the citation would point at a number in the wrong column.

The runs inside a detected table are still what backs the block's ``char_map``,
so an anchor into a table resolves to real rectangles on the page. The grid
lives alongside as ``Block.table`` and is what the model is shown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf as fitz

from app.schemas.document import BBox, TableBlock

logger = logging.getLogger(__name__)

#: A grid smaller than this is a layout artefact, not a table.
_MIN_ROWS = 2
_MIN_COLS = 2
#: Share of cells that must carry text for the grid to be content-bearing.
_MIN_FILLED_CELL_SHARE = 0.35


@dataclass
class TableRegion:
    page: int
    bbox: BBox
    table: TableBlock


def find_tables(path: Path) -> list[TableRegion]:
    doc = fitz.open(path)
    try:
        regions: list[TableRegion] = []
        for page_number in range(doc.page_count):
            page = doc.load_page(page_number)
            regions.extend(_page_tables(page, page_number))
        return regions
    finally:
        doc.close()


def _page_tables(page: Any, page_number: int) -> list[TableRegion]:
    try:
        found = page.find_tables()
    except Exception:  # pragma: no cover - malformed pages
        logger.debug("find_tables failed on page %s", page_number, exc_info=True)
        return []

    regions: list[TableRegion] = []
    for table in getattr(found, "tables", []):
        try:
            raw_rows = table.extract()
        except Exception:  # pragma: no cover
            logger.debug("table.extract failed on page %s", page_number, exc_info=True)
            continue
        block = _to_block(raw_rows, table)
        if block is None:
            continue
        raw = list(table.bbox)
        bbox: BBox = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        regions.append(TableRegion(page=page_number, bbox=bbox, table=block))

    return _drop_nested(regions)


def _to_block(raw_rows: list[list[Any]], table: Any) -> TableBlock | None:
    rows = [[("" if cell is None else str(cell)).strip() for cell in row] for row in raw_rows]
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) < _MIN_ROWS:
        return None

    n_cols = max(len(r) for r in rows)
    if n_cols < _MIN_COLS:
        return None

    total_cells = len(rows) * n_cols
    filled = sum(1 for row in rows for cell in row if cell)
    if total_cells == 0 or filled / total_cells < _MIN_FILLED_CELL_SHARE:
        return None

    header: list[str] | None = None
    header_names = getattr(getattr(table, "header", None), "names", None)
    if header_names:
        candidate = [("" if n is None else str(n)).strip() for n in header_names]
        if any(candidate):
            header = candidate
            if rows and _same_row(rows[0], candidate):
                rows = rows[1:]
    if header is None and rows:
        header = rows[0]
        rows = rows[1:]

    if not rows:
        return None

    return TableBlock(rows=rows, header=header, n_cols=n_cols)


def _same_row(row: list[str], other: list[str]) -> bool:
    return [c.strip() for c in row] == [c.strip() for c in other]


def _drop_nested(regions: list[TableRegion]) -> list[TableRegion]:
    """Keep the outermost region when the detector reports overlapping grids."""
    kept: list[TableRegion] = []
    for region in sorted(regions, key=lambda r: -_area(r.bbox)):
        if any(r.page == region.page and _contains(r.bbox, region.bbox) for r in kept):
            continue
        kept.append(region)
    return sorted(kept, key=lambda r: (r.page, r.bbox[1], r.bbox[0]))


def _area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer[0] - 1 <= inner[0]
        and outer[1] - 1 <= inner[1]
        and outer[2] + 1 >= inner[2]
        and outer[3] + 1 >= inner[3]
    )


def region_for(regions: list[TableRegion], page: int, bbox: BBox) -> TableRegion | None:
    """The table region containing a run's centre, if any."""
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for region in regions:
        if region.page != page:
            continue
        if region.bbox[0] <= cx <= region.bbox[2] and region.bbox[1] <= cy <= region.bbox[3]:
            return region
    return None
