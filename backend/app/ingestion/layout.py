"""Column inference and reading order (§5.3).

Column count is inferred per page from the x-projection of the runs: a vertical
band that no run covers, wide enough and tall enough, is a gutter. Multi-column,
single-column and free-form slide layouts all go through this one routine.

Reading order is then geometric — per band, per column, top to bottom, left to
right — never the extractor's emission order. That is the repair §5.3 asks for:
presentation exports routinely emit a heading after the body it introduces, and
sorting by geometry fixes it without knowing anything about the document.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.extract import PageGeometry
from app.schemas.document import TextRun

#: Minimum gutter width as a fraction of page width.
_MIN_GUTTER_RATIO = 0.025
#: A gutter must be clear over at least this fraction of the text's vertical extent.
_MIN_GUTTER_HEIGHT_RATIO = 0.55
#: Each side of a gutter must hold at least this share of the page's runs.
_MIN_SIDE_SHARE = 0.15
#: A run this wide relative to the text area spans columns rather than sitting in one.
_SPANNING_WIDTH_RATIO = 0.62
#: Vertical overlap above which two runs are considered to be on the same line.
_SAME_LINE_OVERLAP = 0.5

_X_BINS = 240


@dataclass
class PageLayout:
    page: int
    column_count: int
    #: x boundaries delimiting the columns, length ``column_count + 1``.
    boundaries: list[float]
    confidence: float


@dataclass
class LayoutResult:
    runs: list[TextRun]
    pages: list[PageLayout]
    reading_order_confidence: float
    #: Share of adjacent pairs whose geometric order contradicts the extractor's
    #: emission order. Diagnostic only — a high value means the repair did work,
    #: not that the result is wrong.
    raw_order_disagreement: float


def analyze(runs: list[TextRun], pages: list[PageGeometry]) -> LayoutResult:
    by_page: dict[int, list[TextRun]] = {}
    for run in runs:
        by_page.setdefault(run.page, []).append(run)

    ordered: list[TextRun] = []
    layouts: list[PageLayout] = []
    confidences: list[float] = []
    disagreements: list[float] = []

    for geometry in pages:
        page_runs = by_page.get(geometry.number, [])
        if not page_runs:
            layouts.append(
                PageLayout(page=geometry.number, column_count=1, boundaries=[], confidence=1.0)
            )
            continue
        layout = _infer_columns(page_runs, geometry)
        sequence = _reading_order(page_runs, layout, geometry)
        confidences.append(_order_confidence(sequence, layout))
        disagreements.append(_disagreement(sequence))
        layouts.append(layout)
        ordered.extend(sequence)

    for index, run in enumerate(ordered):
        run.order = index

    return LayoutResult(
        runs=ordered,
        pages=layouts,
        reading_order_confidence=round(_mean(confidences, default=1.0), 4),
        raw_order_disagreement=round(_mean(disagreements, default=0.0), 4),
    )


# ---------------------------------------------------------------- columns


def _infer_columns(runs: list[TextRun], geometry: PageGeometry) -> PageLayout:
    single = PageLayout(page=geometry.number, column_count=1, boundaries=[], confidence=1.0)
    if len(runs) < 8:
        return single

    width = geometry.width or max((r.bbox[2] for r in runs), default=1.0)
    if width <= 0:
        return single

    left = min(r.bbox[0] for r in runs)
    right = max(r.bbox[2] for r in runs)
    top = min(r.bbox[1] for r in runs)
    bottom = max(r.bbox[3] for r in runs)
    text_width = max(right - left, 1.0)
    text_height = max(bottom - top, 1.0)

    bin_width = text_width / _X_BINS
    # For each x bin, the vertical extent actually covered by runs. A gutter is a
    # band of bins whose coverage is near zero over most of the page height.
    covered_height = [0.0] * _X_BINS
    intervals: list[list[tuple[float, float]]] = [[] for _ in range(_X_BINS)]
    for run in runs:
        if _is_spanning(run, text_width, left):
            continue
        start = _bin_index(run.bbox[0], left, bin_width)
        end = _bin_index(run.bbox[2], left, bin_width)
        for b in range(start, end + 1):
            intervals[b].append((run.bbox[1], run.bbox[3]))
    for b in range(_X_BINS):
        covered_height[b] = _union_length(intervals[b])

    threshold = text_height * (1.0 - _MIN_GUTTER_HEIGHT_RATIO)
    min_gutter_bins = max(2, int(_MIN_GUTTER_RATIO * width / bin_width))

    gutters: list[tuple[int, int]] = []
    gutter_start: int | None = None
    for b in range(_X_BINS):
        if covered_height[b] <= threshold:
            gutter_start = b if gutter_start is None else gutter_start
        elif gutter_start is not None:
            if b - gutter_start >= min_gutter_bins:
                gutters.append((gutter_start, b))
            gutter_start = None
    if gutter_start is not None and _X_BINS - gutter_start >= min_gutter_bins:
        gutters.append((gutter_start, _X_BINS))

    # Ignore gutters that only shave the outer margins.
    inner = [
        (a, b)
        for a, b in gutters
        if a > min_gutter_bins * 0.5 and b < _X_BINS - min_gutter_bins * 0.5
    ]
    if not inner:
        return single

    boundaries = [left]
    accepted: list[tuple[int, int]] = []
    for a, b in inner:
        centre = left + (a + b) / 2 * bin_width
        if _balanced(runs, boundaries[-1], centre, right):
            boundaries.append(centre)
            accepted.append((a, b))
    boundaries.append(right)

    if len(boundaries) <= 2:
        return single

    widest = max(((b - a) * bin_width for a, b in accepted), default=0.0)
    confidence = min(1.0, 0.6 + 0.4 * min(widest / (_MIN_GUTTER_RATIO * width * 2), 1.0))
    return PageLayout(
        page=geometry.number,
        column_count=len(boundaries) - 1,
        boundaries=boundaries,
        confidence=round(confidence, 4),
    )


def _balanced(runs: list[TextRun], low: float, split: float, high: float) -> bool:
    total = len(runs)
    left_side = sum(1 for r in runs if low <= _centre_x(r) < split)
    right_side = sum(1 for r in runs if split <= _centre_x(r) <= high)
    return left_side >= total * _MIN_SIDE_SHARE and right_side >= total * _MIN_SIDE_SHARE


def _bin_index(x: float, left: float, bin_width: float) -> int:
    return max(0, min(_X_BINS - 1, int((x - left) / bin_width)))


def _union_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start > cur_end:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    return total + (cur_end - cur_start)


def _is_spanning(run: TextRun, text_width: float, left: float) -> bool:
    return (run.bbox[2] - run.bbox[0]) >= _SPANNING_WIDTH_RATIO * text_width


def _centre_x(run: TextRun) -> float:
    return (run.bbox[0] + run.bbox[2]) / 2


# ---------------------------------------------------------- reading order


def _column_of(run: TextRun, layout: PageLayout) -> int:
    if layout.column_count <= 1:
        return 0
    centre = _centre_x(run)
    for i in range(layout.column_count):
        if layout.boundaries[i] <= centre < layout.boundaries[i + 1]:
            return i
    return layout.column_count - 1


def _crosses_gutter(run: TextRun, layout: PageLayout) -> bool:
    if layout.column_count <= 1:
        return False
    return any(run.bbox[0] < boundary < run.bbox[2] for boundary in layout.boundaries[1:-1])


def _reading_order(
    runs: list[TextRun], layout: PageLayout, geometry: PageGeometry
) -> list[TextRun]:
    spanning: list[TextRun] = []
    columnar: list[TextRun] = []
    text_width = max((r.bbox[2] for r in runs), default=1.0) - min(
        (r.bbox[0] for r in runs), default=0.0
    )
    for run in runs:
        if _crosses_gutter(run, layout) or (
            layout.column_count > 1 and _is_spanning(run, max(text_width, 1.0), 0.0)
        ):
            run.column = -1
            spanning.append(run)
        else:
            run.column = _column_of(run, layout)
            columnar.append(run)

    spanning.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    columnar.sort(key=lambda r: (r.column, r.bbox[1], r.bbox[0]))

    if not spanning:
        return _merge_lines(columnar)

    # Full-width runs split the page into bands; each band is read column by
    # column before the next full-width run is emitted.
    result: list[TextRun] = []
    remaining = columnar
    for banner in spanning:
        cutoff = banner.bbox[1]
        above = [r for r in remaining if _centre_y(r) < cutoff]
        remaining = [r for r in remaining if _centre_y(r) >= cutoff]
        result.extend(_merge_lines(above))
        result.append(banner)
    result.extend(_merge_lines(remaining))
    return result


def _centre_y(run: TextRun) -> float:
    return (run.bbox[1] + run.bbox[3]) / 2


def _merge_lines(runs: list[TextRun]) -> list[TextRun]:
    """Group runs into visual lines, then order lines top-down and runs left-right."""
    if not runs:
        return []
    ordered = sorted(runs, key=lambda r: (r.column, r.bbox[1], r.bbox[0]))
    out: list[TextRun] = []
    line: list[TextRun] = [ordered[0]]
    for run in ordered[1:]:
        previous = line[-1]
        same_column = run.column == previous.column
        if same_column and _vertical_overlap(run, previous) >= _SAME_LINE_OVERLAP:
            line.append(run)
        else:
            out.extend(sorted(line, key=lambda r: r.bbox[0]))
            line = [run]
    out.extend(sorted(line, key=lambda r: r.bbox[0]))
    return out


def _vertical_overlap(a: TextRun, b: TextRun) -> float:
    top = max(a.bbox[1], b.bbox[1])
    bottom = min(a.bbox[3], b.bbox[3])
    if bottom <= top:
        return 0.0
    smaller = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    return (bottom - top) / smaller if smaller > 0 else 0.0


# ------------------------------------------------------------- confidence


def _order_confidence(sequence: list[TextRun], layout: PageLayout) -> float:
    """Share of adjacent pairs whose geometric relation is strictly decidable."""
    if len(sequence) < 2:
        return 1.0
    decidable = 0
    for a, b in zip(sequence, sequence[1:], strict=False):
        if a.column != b.column:
            decidable += 1
            continue
        if _vertical_overlap(a, b) >= _SAME_LINE_OVERLAP:
            if b.bbox[0] >= a.bbox[0]:
                decidable += 1
            continue
        if b.bbox[1] >= a.bbox[1] - 1.0:
            decidable += 1
    share = decidable / (len(sequence) - 1)
    return round(min(1.0, share * layout.confidence), 4)


def _disagreement(sequence: list[TextRun]) -> float:
    if len(sequence) < 2:
        return 0.0
    inversions = sum(
        1 for a, b in zip(sequence, sequence[1:], strict=False) if b.raw_index < a.raw_index
    )
    return inversions / (len(sequence) - 1)


def _mean(values: list[float], *, default: float) -> float:
    return sum(values) / len(values) if values else default
