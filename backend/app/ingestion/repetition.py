"""Statistical boilerplate detection (§5.4.1).

Everything here is derived from the document at runtime. There are no literal
strings: a line is boilerplate because it repeats at the same place on enough
pages, and a number is a page number because its value tracks the page index.
The removed runs are kept so gate G6 can check that none of them leaked into
the script.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.ingestion.extract import PageGeometry
from app.ingestion.runs import normalization_key
from app.schemas.document import TextRun

#: A cluster must cover at least this share of pages to count as boilerplate.
_MIN_PAGE_SHARE = 0.30
#: …and at least this many pages, so a 3-page document cannot self-destruct.
_MIN_PAGES = 3
#: Position quantisation for cluster identity, as a fraction of page size.
_POSITION_BINS = 20
#: Band at the top and bottom of the page searched for page numbers.
_EDGE_BAND = 0.10
#: Pearson correlation above which a number series is accepted as page numbers.
_PAGE_NUMBER_CORRELATION = 0.9
#: Vertical band within which a repeated run counts as page furniture.
_FURNITURE_BAND = 0.12
#: Horizontal band for repeated marginalia.
_SIDE_BAND = 0.08
#: Removing more than this share of the document's characters as boilerplate is
#: never right — a document that repeats that much is repeating its content, and
#: deleting it would destroy the parse. The stage abandons removal and says so.
_MAX_REMOVAL_SHARE = 0.25


@dataclass
class RepetitionResult:
    kept: list[TextRun]
    removed: list[TextRun]
    #: Distinct texts removed, for gate G6.
    boilerplate_texts: list[str] = field(default_factory=list)
    page_number_runs: int = 0
    repeated_clusters: int = 0
    warnings: list[str] = field(default_factory=list)


def analyze(runs: list[TextRun], pages: list[PageGeometry]) -> RepetitionResult:
    page_count = max(len(pages), 1)
    geometry = {p.number: p for p in pages}

    removed_ids: set[int] = set()
    boilerplate: list[str] = []

    clusters: dict[tuple[str, int, int], list[TextRun]] = defaultdict(list)
    for run in runs:
        page = geometry.get(run.page)
        if page is None:
            continue
        key = (
            normalization_key(run.text),
            _bin(run.bbox[0], page.width),
            _bin(run.bbox[1], page.height),
        )
        if not key[0]:
            continue
        clusters[(key[0], key[1], key[2])].append(run)

    threshold = max(_MIN_PAGES, int(round(page_count * _MIN_PAGE_SHARE)))
    repeated = 0
    for members in clusters.values():
        distinct_pages = {m.page for m in members}
        if len(distinct_pages) < threshold:
            continue
        if not any(_is_page_furniture(m, geometry.get(m.page)) for m in members):
            # Repeated, but shaped like prose rather than a running head. Real
            # boilerplate is short or lives in the margins; treating a repeated
            # paragraph as furniture would delete content.
            continue
        repeated += 1
        for member in members:
            removed_ids.add(member.raw_index)
        boilerplate.append(members[0].text.strip())

    total_chars = sum(len(r.text) for r in runs)
    removed_chars = sum(len(r.text) for r in runs if r.raw_index in removed_ids)
    warnings: list[str] = []
    if total_chars and removed_chars / total_chars > _MAX_REMOVAL_SHARE:
        warnings.append(
            f"Repetition analysis matched {removed_chars / total_chars:.0%} of the "
            "document's characters, which is far more than page furniture can account "
            "for. Boilerplate removal was abandoned for this document; repeated lines "
            "may appear in the script."
        )
        removed_ids.clear()
        boilerplate.clear()
        repeated = 0

    page_number_ids = _detect_page_numbers(runs, geometry)
    removed_ids |= page_number_ids

    kept = [r for r in runs if r.raw_index not in removed_ids]
    removed = [r for r in runs if r.raw_index in removed_ids]

    return RepetitionResult(
        kept=kept,
        removed=removed,
        boilerplate_texts=sorted({t for t in boilerplate if t}),
        page_number_runs=len(page_number_ids),
        repeated_clusters=repeated,
        warnings=warnings,
    )


def _is_page_furniture(run: TextRun, page: PageGeometry | None) -> bool:
    """Position test for page furniture, derived from the page geometry alone.

    Furniture lives in the margins: running heads and feet in the top and bottom
    bands, marginalia in the outer side bands. Text repeating in the middle of
    the text area is repeated *content* — deleting it would destroy the parse,
    so it is left alone and the ingestion report carries the observation instead.
    """
    if page is None or page.height <= 0 or page.width <= 0:
        return False
    centre_y = (run.bbox[1] + run.bbox[3]) / 2 / page.height
    if centre_y <= _FURNITURE_BAND or centre_y >= 1.0 - _FURNITURE_BAND:
        return True
    centre_x = (run.bbox[0] + run.bbox[2]) / 2 / page.width
    return centre_x <= _SIDE_BAND or centre_x >= 1.0 - _SIDE_BAND


def _bin(value: float, extent: float) -> int:
    if extent <= 0:
        return 0
    return int(min(_POSITION_BINS - 1, max(0, value / extent * _POSITION_BINS)))


def _detect_page_numbers(runs: list[TextRun], geometry: dict[int, PageGeometry]) -> set[int]:
    """Runs in the edge bands whose integer value correlates with the page index."""
    candidates: list[tuple[TextRun, int]] = []
    for run in runs:
        page = geometry.get(run.page)
        if page is None or page.height <= 0:
            continue
        centre = (run.bbox[1] + run.bbox[3]) / 2 / page.height
        if _EDGE_BAND < centre < 1.0 - _EDGE_BAND:
            continue
        value = _as_integer(run.text)
        if value is None:
            continue
        candidates.append((run, value))

    if len(candidates) < _MIN_PAGES:
        return set()

    # Group by vertical band so a header number series and a footer number series
    # are evaluated separately.
    bands: dict[int, list[tuple[TextRun, int]]] = defaultdict(list)
    for run, value in candidates:
        page = geometry[run.page]
        top = (run.bbox[1] + run.bbox[3]) / 2 < page.height / 2
        bands[0 if top else 1].append((run, value))

    accepted: set[int] = set()
    for members in bands.values():
        if len(members) < _MIN_PAGES:
            continue
        xs = [float(run.page) for run, _ in members]
        ys = [float(value) for _, value in members]
        if _pearson(xs, ys) >= _PAGE_NUMBER_CORRELATION:
            accepted |= {run.raw_index for run, _ in members}
    return accepted


def _as_integer(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits or len(digits) > 4:
        return None
    # Reject anything carrying letters: a numbered heading is not a page number.
    if any(ch.isalpha() for ch in text):
        return None
    try:
        return int(digits)
    except ValueError:  # pragma: no cover - digits are guaranteed numeric
        return None


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return 0.0
    return float(cov / (var_x**0.5 * var_y**0.5))
