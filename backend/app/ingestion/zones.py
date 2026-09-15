"""Zone classification (§5.6).

This is the component that previously encoded publisher knowledge. It is now a
model call over the fixed, publisher-neutral, language-neutral taxonomy in
``app/schemas/zones.py``. The prompt describes the *shape* of each zone — what
role the text plays in a learning document — and never names a publisher, a
course, or a heading string.

Two determinations are carried forward rather than re-guessed: a block the
structure stage identified as a heading is a ``heading``, and a block backed by
a detected table grid is a ``data_table``. Everything else goes to the model.

Classification is total (INV-5). A block the model does not return a label for
is a bug, not a state — it is filled with the conservative fallback and flagged.

Caching (§5.6) content-hashes the classifier payload, so parsing the same PDF
twice costs one classification and is deterministic (INV-7).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.llm.base import CompletionRequest, LLMClient, LLMError, Message
from app.schemas.document import Block, Section
from app.schemas.zones import (
    UNCERTAIN_FALLBACK_ZONE,
    ZONE_CONFIDENCE_THRESHOLD,
    ZONE_TABLE,
    Zone,
    salience_of,
)

logger = logging.getLogger(__name__)

#: Pages per model call (§5.6).
PAGES_PER_BATCH = 8
#: Characters of block text sent to the classifier.
TEXT_TRUNCATION = 320


@dataclass
class ClassificationResult:
    #: ``block_id -> (zone, confidence)``.
    labels: dict[str, tuple[Zone, float]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    model_calls: int = 0
    cache_hits: int = 0
    #: Blocks the model failed to return, filled with the fallback.
    missing: int = 0


class ZoneCache:
    """Content-addressed cache for classifier payloads."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> list[dict[str, Any]] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return list(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - corrupt cache entry
            logger.warning("discarding unreadable zone cache entry %s", path)
            return None

    def put(self, key: str, value: list[dict[str, Any]]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


_SYSTEM_PROMPT = """\
You label blocks of text extracted from a learning document so that a narration
system knows which blocks may be spoken aloud and which may not.

You are given one batch of blocks in reading order, with typographic and
positional features. Assign every block exactly one label from this fixed
taxonomy:

{taxonomy}

Guidance:
- Judge by the role the text plays for a reader, not by any wording you expect
  a particular publisher to use. The documents come from many sources and many
  languages; never assume a house style.
- `exercise` is text addressed to the learner as a task or question to answer.
  `body` is text that explains something to the learner. A rhetorical question
  inside an explanation is `body`.
- `answer_space` is a blank area, ruled lines, or an empty form for the learner
  to write in — recognisable from very short or empty text in a laid-out grid.
- `emphasis_callout` is a short passage the author visually marked as important,
  usually inside a drawn or shaded box.
- `metadata` is cover, imprint, copyright and legal text.
- `navigation` is a table of contents, an index, or page furniture.
- If a block is running prose and nothing distinguishes it, label it `body`.

Report a confidence between 0 and 1 for each block. Use a low confidence when
the features are genuinely ambiguous — a downstream reviewer sees those blocks
and corrects them. Do not inflate confidence.

Return one entry for every block index you were given. Omitting a block is an
error.
"""


def _taxonomy_block() -> str:
    lines = []
    for spec in ZONE_TABLE.values():
        narratable = "spoken aloud" if spec.narratable else "never spoken aloud"
        lines.append(f"- `{spec.zone.value}`: {spec.meaning}. ({narratable})")
    return "\n".join(lines)


_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_index": {"type": "integer"},
                    "zone": {"type": "string", "enum": [z.value for z in Zone]},
                    "confidence": {"type": "number"},
                },
                "required": ["block_index", "zone", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


class ZoneClassifier:
    def __init__(
        self,
        client: LLMClient | None,
        model: str,
        cache: ZoneCache | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.cache = cache

    def classify(
        self,
        blocks: list[Block],
        sections: list[Section] | None,
        page_body_size: dict[int, float],
        page_sizes: list[tuple[float, float]],
    ) -> ClassificationResult:
        result = ClassificationResult()
        section_titles = {s.id: s.title for s in (sections or [])}

        pending: list[Block] = []
        for block in blocks:
            carried = _carried_forward(block)
            if carried is not None:
                result.labels[block.id] = carried
            else:
                pending.append(block)

        if not pending:
            return result

        if self.client is None:
            for block in pending:
                result.labels[block.id] = (UNCERTAIN_FALLBACK_ZONE, 0.0)
            result.warnings.append(
                "Zone classification did not run (no LLM client configured). Every "
                "non-structural block was labelled 'body' and flagged as uncertain, "
                "so nothing was dropped, but non-narratable material was not "
                "identified. Configure an API key and re-parse for a real labelling."
            )
            return result

        for batch in _batch_by_pages(pending):
            payload = [
                _features(index, block, section_titles, page_body_size, page_sizes)
                for index, block in enumerate(batch)
            ]
            key = _payload_key(self.model, payload)
            cached = self.cache.get(key) if self.cache else None

            if cached is not None:
                result.cache_hits += 1
                entries = cached
            else:
                try:
                    entries = self._call(payload)
                except LLMError as exc:
                    logger.warning("zone classification batch failed: %s", exc)
                    result.warnings.append(
                        f"A zone classification batch failed ({exc}); its {len(batch)} "
                        "blocks were labelled 'body' and flagged as uncertain."
                    )
                    for block in batch:
                        result.labels[block.id] = (UNCERTAIN_FALLBACK_ZONE, 0.0)
                    continue
                result.model_calls += 1
                if self.cache:
                    self.cache.put(key, entries)

            by_index = {int(e["block_index"]): e for e in entries if "block_index" in e}
            for position, block in enumerate(batch):
                entry = by_index.get(position)
                if entry is None:
                    result.missing += 1
                    result.labels[block.id] = (UNCERTAIN_FALLBACK_ZONE, 0.0)
                    continue
                result.labels[block.id] = _coerce(entry)

        if result.missing:
            result.warnings.append(
                f"{result.missing} block(s) were not returned by the classifier and were "
                "labelled 'body' and flagged as uncertain."
            )
        return result

    def _call(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assert self.client is not None
        request = CompletionRequest(
            model=self.model,
            system=_SYSTEM_PROMPT.format(taxonomy=_taxonomy_block()),
            messages=[
                Message(
                    role="user",
                    content=(
                        "Label every block below.\n\n"
                        + json.dumps(payload, ensure_ascii=False, indent=1)
                    ),
                )
            ],
            max_tokens=8192,
            temperature=0.0,
            json_schema=_RESPONSE_SCHEMA,
            cache_system=True,
        )
        data = self.client.complete(request).json_payload()
        entries = data.get("labels", []) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise LLMError("classifier response did not contain a list of labels")
        return [e for e in entries if isinstance(e, dict)]


def apply(blocks: list[Block], result: ClassificationResult) -> None:
    """Write labels onto blocks, applying the uncertainty rule (§5.6)."""
    for block in blocks:
        zone, confidence = result.labels.get(block.id, (UNCERTAIN_FALLBACK_ZONE, 0.0))
        uncertain = confidence < ZONE_CONFIDENCE_THRESHOLD
        if uncertain:
            zone = UNCERTAIN_FALLBACK_ZONE
        block.zone = zone
        block.zone_confidence = round(float(confidence), 4)
        block.zone_uncertain = uncertain
        block.salience = salience_of(zone)


# ---------------------------------------------------------------- helpers


def _carried_forward(block: Block) -> tuple[Zone, float] | None:
    """Determinations already made by earlier stages, not re-guessed here."""
    if block.table is not None:
        return (Zone.DATA_TABLE, 1.0)
    if block.heading_level is not None:
        return (Zone.HEADING, 1.0)
    return None


def _batch_by_pages(blocks: list[Block]) -> list[list[Block]]:
    batches: list[list[Block]] = []
    current: list[Block] = []
    first_page = blocks[0].page if blocks else 0
    for block in blocks:
        if current and block.page - first_page >= PAGES_PER_BATCH:
            batches.append(current)
            current = []
            first_page = block.page
        current.append(block)
    if current:
        batches.append(current)
    return batches


def _features(
    index: int,
    block: Block,
    section_titles: dict[str, str],
    page_body_size: dict[int, float],
    page_sizes: list[tuple[float, float]],
) -> dict[str, Any]:
    body_size = page_body_size.get(block.page, 0.0) or 1.0
    width, height = page_sizes[block.page] if block.page < len(page_sizes) else (595.0, 842.0)
    bbox = block.bboxes[0][1] if block.bboxes else (0.0, 0.0, 0.0, 0.0)
    text = block.text if block.table is None else block.table.to_markdown()
    return {
        "block_index": index,
        "text": text[:TEXT_TRUNCATION],
        "truncated": len(text) > TEXT_TRUNCATION,
        "word_count": block.word_count(),
        "relative_font_size": round(block.font_size / body_size, 2),
        "bold": block.bold,
        "in_filled_rect": block.in_filled_rect,
        "page": block.page,
        "vertical_position": round((bbox[1] / height) if height else 0.0, 3),
        "horizontal_position": round((bbox[0] / width) if width else 0.0, 3),
        "width_ratio": round(((bbox[2] - bbox[0]) / width) if width else 0.0, 3),
        "section_title": section_titles.get(block.section_id or "", "") or None,
    }


def _payload_key(model: str, payload: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"model": model, "prompt_version": 1, "blocks": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce(entry: dict[str, Any]) -> tuple[Zone, float]:
    raw_zone = str(entry.get("zone", "")).strip().lower()
    try:
        zone = Zone(raw_zone)
    except ValueError:
        return (UNCERTAIN_FALLBACK_ZONE, 0.0)
    try:
        confidence = float(entry.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return (zone, max(0.0, min(1.0, confidence)))
