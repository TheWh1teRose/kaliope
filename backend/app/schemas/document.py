"""Document-side domain models (§4, §5.2, §5.8, §5.9).

The load-bearing idea is that no stage ever works on concatenated page text.
Every stage maps ``list[TextRun] -> list[TextRun]`` and each :class:`TextRun`
carries the x-boundary of every character it still contains, so a character
range in an assembled block always resolves back to a rectangle on a page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.zones import Zone

BBox = tuple[float, float, float, float]


class TextRun(BaseModel):
    """A provenance-carrying span of extracted text (§5.2).

    ``char_x`` holds ``len(text) + 1`` x-coordinates: the left edge of every
    character followed by the right edge of the last one. It is what makes a
    sub-run anchor resolvable to a real rectangle. It may be empty when the
    extractor could not supply per-character geometry; consumers then fall back
    to ``bbox`` and record reduced anchor precision rather than guessing.
    """

    page: int
    bbox: BBox
    text: str
    font_size: float
    font_name: str
    bold: bool
    in_filled_rect: bool
    raw_index: int
    sources: list[int] = Field(default_factory=list)
    char_x: list[float] = Field(default_factory=list)
    #: Set by the layout stage; runs are ordered by it before block assembly.
    column: int = 0
    order: int = 0
    #: Extractor-supplied grouping hints, used by block assembly as one signal
    #: among several. Never trusted on their own.
    line_index: int = 0
    para_index: int = 0
    #: Set by dehyphenation: the next run continues this word, so block assembly
    #: must concatenate without inserting a separator (§5.4.3).
    tight_join_next: bool = False

    def has_geometry(self) -> bool:
        return len(self.char_x) == len(self.text) + 1

    def sub_bbox(self, start: int, end: int) -> BBox:
        """Rectangle covering ``text[start:end]``.

        Falls back to the full run rectangle when per-character geometry is
        unavailable — a superset is a degraded answer, never a wrong one.
        """
        if not self.has_geometry() or start >= end:
            return self.bbox
        start = max(0, min(start, len(self.text)))
        end = max(start, min(end, len(self.text)))
        x0 = self.char_x[start]
        x1 = self.char_x[end]
        if x1 <= x0:
            return self.bbox
        return (x0, self.bbox[1], x1, self.bbox[3])


class RunSpan(BaseModel):
    """One entry of ``Block.char_map``: block characters ← one source run."""

    char_start: int
    char_end: int
    page: int
    bbox: BBox
    #: x boundaries for the contributing slice; ``char_end - char_start + 1``
    #: entries when geometry survived, empty otherwise.
    char_x: list[float] = Field(default_factory=list)

    def sub_bbox(self, start: int, end: int) -> BBox:
        """Rectangle for a sub-range expressed in *block* coordinates."""
        local_start = max(0, start - self.char_start)
        local_end = min(self.char_end - self.char_start, end - self.char_start)
        expected = self.char_end - self.char_start + 1
        if len(self.char_x) != expected or local_start >= local_end:
            return self.bbox
        x0 = self.char_x[local_start]
        x1 = self.char_x[local_end]
        if x1 <= x0:
            return self.bbox
        return (x0, self.bbox[1], x1, self.bbox[3])


class TableBlock(BaseModel):
    """A table kept as a grid (§5.7). Never flattened into a text stream."""

    rows: list[list[str]]
    header: list[str] | None = None
    n_cols: int

    def to_markdown(self) -> str:
        """LLM-facing rendering."""
        header = self.header or (self.rows[0] if self.rows else [])
        body = self.rows[1:] if (self.header is None and self.rows) else self.rows
        width = self.n_cols or len(header)

        def row(cells: list[str]) -> str:
            padded = list(cells) + [""] * (width - len(cells))
            cleaned = [c.replace("|", "\\|").replace("\n", " ").strip() for c in padded[:width]]
            return "| " + " | ".join(cleaned) + " |"

        lines = [row(header), "| " + " | ".join(["---"] * max(width, 1)) + " |"]
        lines.extend(row(r) for r in body)
        return "\n".join(lines)


class Block(BaseModel):
    """The atomic unit of source text (§4)."""

    id: str
    ordinal: int
    text: str
    page: int
    #: Union rectangle per page the block touches. A block can straddle a page
    #: break, so this is a list rather than a single box.
    bboxes: list[tuple[int, BBox]] = Field(default_factory=list)
    char_map: list[RunSpan] = Field(default_factory=list)
    zone: Zone = Zone.BODY
    zone_confidence: float = 0.0
    zone_uncertain: bool = False
    salience: float = 1.0
    section_id: str | None = None
    heading_level: int | None = None
    font_size: float = 0.0
    bold: bool = False
    in_filled_rect: bool = False
    table: TableBlock | None = None

    def word_count(self) -> int:
        return len(self.text.split())

    def llm_text(self) -> str:
        """What the model sees for this block."""
        if self.table is not None:
            return self.table.to_markdown()
        return self.text


class Section(BaseModel):
    id: str
    title: str
    level: int
    ordinal: int
    block_ids: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class LanguageDetection(BaseModel):
    language: str
    confidence: float
    #: Runner-up languages with their scores, for the ingestion report.
    alternatives: dict[str, float] = Field(default_factory=dict)


StructureSource = Literal["outline", "typographic", "flat"]
Confidence = Literal["high", "medium", "low"]


class IngestionReport(BaseModel):
    """§5.8 — the generic replacement for template detection."""

    language: str
    language_confidence: float = 0.0
    page_count: int
    extractable_words: int
    narratable_words: int
    visual_content_ratio: float
    text_density: float
    structure_source: StructureSource
    structure_confidence: Confidence
    section_count: int
    zone_distribution: dict[str, int] = Field(default_factory=dict)
    zone_uncertain_ratio: float = 0.0
    table_count: int = 0
    boilerplate_lines_removed: int = 0
    anchor_integrity: float = 0.0
    reading_order_confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    ingestion_confidence: Confidence = "low"
    #: Human-readable justification for ``ingestion_confidence``. Surfaced in the
    #: console so a low-confidence parse always explains itself.
    confidence_reasons: list[str] = Field(default_factory=list)
    #: Character conservation ledger (INV-3).
    chars_extracted: int = 0
    chars_in_blocks: int = 0
    chars_removed: dict[str, int] = Field(default_factory=dict)
    chars_added: dict[str, int] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """§5.9. Note there is no field naming a template, publisher or family."""

    document_id: str
    parse_version: int
    language: str
    title: str | None = None
    page_count: int
    page_sizes: list[tuple[float, float]] = Field(default_factory=list)
    sections: list[Section] | None = None
    blocks: list[Block] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    non_narratable_text: list[str] = Field(default_factory=list)
    boilerplate: list[str] = Field(default_factory=list)
    report: IngestionReport

    def block_by_id(self, block_id: str) -> Block | None:
        return self._index().get(block_id)

    def _index(self) -> dict[str, Block]:
        cached = getattr(self, "_block_index", None)
        if cached is None or len(cached) != len(self.blocks):
            cached = {b.id: b for b in self.blocks}
            object.__setattr__(self, "_block_index", cached)
        return cached

    def narratable_blocks(self) -> list[Block]:
        from app.schemas.zones import is_narratable

        return [b for b in self.blocks if is_narratable(b.zone)]

    def narratable_word_count(self) -> int:
        return sum(b.word_count() for b in self.narratable_blocks())


class Anchor(BaseModel):
    """§4. MUST always resolve to exact source characters and page rectangles."""

    document_id: str
    parse_version: int
    block_id: str
    char_start: int
    char_end: int

    @model_validator(mode="after")
    def _ordered(self) -> Anchor:
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError("anchor requires 0 <= char_start < char_end")
        return self


class AnchorRect(BaseModel):
    """One resolved highlight rectangle."""

    page: int
    bbox: BBox


class ResolvedAnchor(BaseModel):
    anchor: Anchor
    text: str
    rects: list[AnchorRect]
    #: ``False`` when the block existed but the range fell outside it.
    resolved: bool = True
