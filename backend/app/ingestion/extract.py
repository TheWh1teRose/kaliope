"""PyMuPDF extraction into :class:`TextRun` objects (§5.2).

``rawdict`` is used rather than ``dict`` because it carries a bounding box for
every individual character. Those boxes become ``TextRun.char_x``, which is what
lets an anchor that starts mid-line resolve to a rectangle that starts mid-line.
Without them the whole anchor mechanism degrades to line granularity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz

from app.schemas.document import BBox, TextRun

logger = logging.getLogger(__name__)

#: PyMuPDF span flag bit for a bold font face.
_FLAG_BOLD = 1 << 4

#: A drawing counts as a filled area only above this size, so that underlines
#: and table rules are not mistaken for highlight boxes.
_MIN_FILLED_AREA = 400.0


@dataclass
class PageGeometry:
    number: int
    width: float
    height: float
    image_area: float = 0.0
    filled_rects: list[BBox] = field(default_factory=list)

    @property
    def area(self) -> float:
        return max(self.width * self.height, 1.0)

    @property
    def image_ratio(self) -> float:
        return min(self.image_area / self.area, 1.0)


@dataclass
class Extraction:
    runs: list[TextRun]
    pages: list[PageGeometry]
    #: PDF outline as ``(level, title, page_index)``. Empty when absent.
    outline: list[tuple[int, str, int]]
    metadata_title: str | None
    #: Characters seen at extraction time, before any normalization (INV-3).
    raw_char_count: int
    #: Characters dropped as whitespace-only runs during extraction.
    dropped_whitespace_chars: int


def extract(path: Path) -> Extraction:
    doc = fitz.open(path)
    try:
        return _extract_doc(doc)
    finally:
        doc.close()


def _extract_doc(doc: Any) -> Extraction:
    runs: list[TextRun] = []
    pages: list[PageGeometry] = []
    raw_chars = 0
    dropped = 0
    raw_index = 0

    for page_number in range(doc.page_count):
        page = doc.load_page(page_number)
        rect = page.rect
        geometry = PageGeometry(number=page_number, width=rect.width, height=rect.height)
        geometry.filled_rects = _filled_rects(page)

        raw = page.get_text("rawdict")
        for para_index, block in enumerate(raw.get("blocks", [])):
            if block.get("type") == 1:
                geometry.image_area += _bbox_area(block.get("bbox"))
                continue
            for line_index, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    run, char_count, dropped_here = _span_to_run(
                        span,
                        page_number=page_number,
                        raw_index=raw_index,
                        line_index=line_index,
                        para_index=para_index,
                        filled=geometry.filled_rects,
                    )
                    raw_chars += char_count
                    dropped += dropped_here
                    if run is not None:
                        runs.append(run)
                        raw_index += 1

        pages.append(geometry)

    return Extraction(
        runs=runs,
        pages=pages,
        outline=_outline(doc),
        metadata_title=_metadata_title(doc),
        raw_char_count=raw_chars,
        dropped_whitespace_chars=dropped,
    )


def _span_to_run(
    span: dict[str, Any],
    *,
    page_number: int,
    raw_index: int,
    line_index: int,
    para_index: int,
    filled: list[BBox],
) -> tuple[TextRun | None, int, int]:
    chars = span.get("chars") or []
    text = "".join(c.get("c", "") for c in chars)
    if not text:
        text = span.get("text", "")
        chars = []
    char_count = len(text)
    if not text.strip():
        return None, char_count, char_count

    char_x = _char_boundaries(chars, span.get("bbox"))
    if len(char_x) != len(text) + 1:
        char_x = []

    bbox = _as_bbox(span.get("bbox"))
    font_name = str(span.get("font", ""))
    flags = int(span.get("flags", 0))
    bold = bool(flags & _FLAG_BOLD) or "bold" in font_name.lower() or "black" in font_name.lower()

    run = TextRun(
        page=page_number,
        bbox=bbox,
        text=text,
        font_size=round(float(span.get("size", 0.0)), 2),
        font_name=font_name,
        bold=bold,
        in_filled_rect=_inside_any(bbox, filled),
        raw_index=raw_index,
        sources=[raw_index],
        char_x=char_x,
        line_index=line_index,
        para_index=para_index,
    )
    return run, char_count, 0


def _char_boundaries(chars: list[dict[str, Any]], span_bbox: Any) -> list[float]:
    """Left edge of every character followed by the right edge of the last.

    Returns an empty list when the sequence is not left-to-right monotonic —
    rotated or right-to-left text, where a linear x mapping would be wrong. The
    consumer then falls back to whole-run rectangles and the ingestion report
    records the reduced precision.
    """
    if not chars:
        return []
    boundaries: list[float] = []
    last_right = None
    for char in chars:
        box = char.get("bbox")
        if box is None or len(box) != 4:
            return []
        x0, x1 = float(box[0]), float(box[2])
        if x1 < x0:
            return []
        if last_right is not None and x0 + 0.5 < boundaries[-1]:
            return []
        boundaries.append(x0)
        last_right = x1
    boundaries.append(float(last_right if last_right is not None else boundaries[-1]))
    for i in range(1, len(boundaries)):
        if boundaries[i] < boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1]
    return boundaries


def _filled_rects(page: Any) -> list[BBox]:
    """Rectangles the author drew as a filled/shaded box (§5.2 ``in_filled_rect``)."""
    rects: list[BBox] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover - defensive; malformed content streams
        logger.debug("get_drawings failed on page %s", page.number, exc_info=True)
        return rects
    for drawing in drawings:
        if drawing.get("fill") is None:
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        if _bbox_area(bbox) < _MIN_FILLED_AREA:
            continue
        # A page-sized fill is a background, not an emphasis box.
        if _bbox_area(bbox) > 0.9 * float(page.rect.width) * float(page.rect.height):
            continue
        rects.append(bbox)
    return rects


def _inside_any(bbox: BBox, rects: list[BBox]) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)


def _bbox_area(bbox: Any) -> float:
    if bbox is None or len(bbox) != 4:
        return 0.0
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _as_bbox(value: Any) -> BBox:
    if value is None or len(value) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _outline(doc: Any) -> list[tuple[int, str, int]]:
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # pragma: no cover - some files carry a broken outline
        logger.debug("get_toc failed", exc_info=True)
        return []
    entries: list[tuple[int, str, int]] = []
    for item in toc or []:
        if len(item) < 3:
            continue
        level, title, page = int(item[0]), str(item[1]).strip(), int(item[2])
        if not title:
            continue
        entries.append((level, title, page - 1))
    return entries


def _metadata_title(doc: Any) -> str | None:
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    return title or None


def render_page_png(path: Path, page_number: int, dpi: int = 130) -> bytes:
    """Render one page for the review console's source pane."""
    doc = fitz.open(path)
    try:
        page = doc.load_page(page_number)
        pixmap = page.get_pixmap(dpi=dpi)
        return bytes(pixmap.tobytes("png"))
    finally:
        doc.close()


def page_size(path: Path, page_number: int) -> tuple[float, float]:
    doc = fitz.open(path)
    try:
        rect = doc.load_page(page_number).rect
        return float(rect.width), float(rect.height)
    finally:
        doc.close()


def extract_text_in_rects(path: Path, rects: list[tuple[int, BBox]]) -> str:
    """Re-extract the text inside page rectangles. Used by the INV-4 round-trip."""
    doc = fitz.open(path)
    try:
        parts: list[str] = []
        for page_number, bbox in rects:
            page = doc.load_page(page_number)
            clip = fitz.Rect(*bbox)
            # A hairline pad absorbs float rounding at the glyph edges; without
            # it the first or last character is intermittently clipped away.
            clip = fitz.Rect(clip.x0 - 0.6, clip.y0 - 0.6, clip.x1 + 0.6, clip.y1 + 0.6)
            parts.append(page.get_text("text", clip=clip))
        return " ".join(parts)
    finally:
        doc.close()
