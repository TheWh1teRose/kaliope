"""A synthetic learning document and a baseline flow over it, for the series tests.

The document is built in code rather than parsed from a PDF, so its ids are
fixed and every prompt and cache key derived from it is reproducible. That is
what lets ``test_series_golden`` pin today's baseline prompts byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.llm.base import LLMClient
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, FlowNode, bootstrap_nodes
from app.pipeline.framework.runner import FlowRunner, RunResult
from app.schemas.document import (
    Block,
    IngestionReport,
    ParsedDocument,
    RunSpan,
    Section,
)
from app.schemas.pipeline import AudienceSpec, FormatSpec, SpeakerSpec
from app.schemas.zones import Zone
from tests.support import StubProvider

SECTIONS = [
    ("Wetter und Klima", "Wetter beschreibt den Zustand der Atmosphäre an einem Ort"),
    ("Der Treibhauseffekt", "Treibhausgase lassen Sonnenlicht durch und halten Wärme zurück"),
    (
        "Folgen der Erwärmung",
        "Der Meeresspiegel steigt, weil Eis schmilzt und Wasser sich ausdehnt",
    ),
    ("Klimaschutz", "Klimaschutz senkt die Emissionen, Anpassung mindert die Folgen"),
]
BLOCKS_PER_SECTION = 5
#: Words per block; 4 × 5 × 140 = 2,800 narratable words ≈ 8.3 supportable minutes.
WORDS_PER_BLOCK = 140


def _text(section: int, block: int) -> str:
    title, sentence = SECTIONS[section]
    words = f"{sentence} und das gilt in Abschnitt {section + 1}.{block + 1} ganz besonders. "
    repeated = (words * (WORDS_PER_BLOCK // len(words.split()) + 1)).split()
    return " ".join(repeated[:WORDS_PER_BLOCK])


def learning_document(document_id: str = "doc-series") -> ParsedDocument:
    blocks: list[Block] = []
    sections: list[Section] = []
    ordinal = 0
    for s, (title, _sentence) in enumerate(SECTIONS):
        section_id = f"sec{s}"
        ids: list[str] = []
        for b in range(BLOCKS_PER_SECTION):
            text = _text(s, b)
            box = (60.0, 100.0 + b * 120, 500.0, 200.0 + b * 120)
            block_id = f"b{ordinal:06d}"
            blocks.append(
                Block(
                    id=block_id,
                    ordinal=ordinal,
                    text=text,
                    page=s,
                    bboxes=[(s, box)],
                    char_map=[RunSpan(char_start=0, char_end=len(text), page=s, bbox=box)],
                    zone=Zone.BODY,
                    zone_confidence=0.95,
                    salience=1.0,
                    section_id=section_id,
                )
            )
            ids.append(block_id)
            ordinal += 1
        sections.append(
            Section(id=section_id, title=title, level=1, ordinal=s, block_ids=ids, page_start=s)
        )
    words = sum(b.word_count() for b in blocks)
    return ParsedDocument(
        document_id=document_id,
        parse_version=1,
        language="de",
        page_count=len(SECTIONS),
        page_sizes=[(595.0, 842.0)] * len(SECTIONS),
        blocks=blocks,
        sections=sections,
        report=IngestionReport(
            language="de",
            page_count=len(SECTIONS),
            extractable_words=words,
            narratable_words=words,
            visual_content_ratio=0.0,
            text_density=float(words) / len(SECTIONS),
            structure_source="typographic",
            structure_confidence="high",
            section_count=len(SECTIONS),
            zone_uncertain_ratio=0.0,
            anchor_integrity=1.0,
            reading_order_confidence=1.0,
            ingestion_confidence="high",
            confidence_reasons=["ok"],
        ),
    )


FORMAT = FormatSpec(
    id="two",
    name="Zwei",
    speakers=[
        SpeakerSpec(id="mod", name="Moderator", role="host"),
        SpeakerSpec(id="exp", name="Expertin", role="expert"),
    ],
    register="formal",
    target_minutes=5,
    opening="Name the topic.",
    closing="Recap the through-line.",
)
AUDIENCE = AudienceSpec(description="Klasse 10")

BASELINE_NODES = [
    FlowNode(node="content_budget", config={"min_compression": 2.5}),
    FlowNode(node="select", config={"model": "claude-opus-5"}),
    FlowNode(node="outline", config={"model": "claude-opus-5"}),
    FlowNode(node="script", config={"model": "claude-opus-5", "temperature": 0.7}),
]


def run_baseline(store_root: Path, provider: StubProvider, *, target_minutes: int = 5) -> RunResult:
    """The baseline nodes after ``ingest``, over the synthetic document."""
    bootstrap_nodes()
    flow = Flow(id="golden", version="1", nodes=BASELINE_NODES)
    runner = FlowRunner(
        artifacts=ArtifactStore(store_root),
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
    )
    logging.getLogger("kalliope").setLevel(logging.WARNING)
    return runner.execute(
        flow,
        "golden-run",
        {
            "parsed": learning_document(),
            "target_minutes": target_minutes,
            "format_spec": FORMAT,
            "audience_spec": AUDIENCE,
        },
    )


def fingerprint(provider: StubProvider, result: RunResult, store_root: Path) -> dict[str, Any]:
    """Every prompt the run sent, every node's cache key and every beat key, as hashes."""

    def digest(value: Any) -> str:
        body = json.dumps(value, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    return {
        "calls": [
            digest(
                {
                    "system": call.system,
                    "messages": [
                        {"role": m.role, "content": m.content, "cache_breaks": m.cache_breaks}
                        for m in call.messages
                    ],
                }
            )
            for call in provider.calls
        ],
        "cache_keys": {record.name: record.cache_key for record in result.manifest.nodes},
        "step_keys": sorted(path.stem for path in (store_root / "steps").rglob("*.ref")),
    }
