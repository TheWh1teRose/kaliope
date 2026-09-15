"""The ``objective_select`` node: passages chosen against listener objectives."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import bootstrap_nodes
from app.pipeline.nodes.objective_select import ObjectiveSelectInput, ObjectiveSelectNode
from app.pipeline.validation import validate_flow
from app.schemas.document import Block, IngestionReport, ParsedDocument, RunSpan
from app.schemas.pipeline import (
    AudienceSpec,
    ContentBudget,
    Objective,
    Objectives,
)
from app.schemas.zones import Zone

BODY = (
    "Der Hypothalamus steuert die Hypophyse und reguliert damit zahlreiche periphere "
    "Drüsen im Körper des Menschen."
)


class FixedProvider:
    name = "anthropic"

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[CompletionRequest] = []

    def available(self) -> bool:
        return True

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        body = json.dumps(self.payload, ensure_ascii=False)
        return Completion(
            text=body,
            model_id=request.model,
            usage=Usage(input_tokens=50, output_tokens=40),
            latency_ms=1,
        )


def make_parsed(**overrides: object) -> ParsedDocument:
    block = Block(
        id="b000000",
        ordinal=0,
        text=BODY,
        page=0,
        bboxes=[(0, (60.0, 100.0, 500.0, 120.0))],
        char_map=[
            RunSpan(char_start=0, char_end=len(BODY), page=0, bbox=(60.0, 100.0, 500.0, 120.0))
        ],
        zone=Zone.BODY,
        zone_confidence=0.95,
        salience=1.0,
    )
    report = IngestionReport(
        language="de",
        page_count=1,
        extractable_words=len(BODY.split()),
        narratable_words=len(BODY.split()),
        visual_content_ratio=0.0,
        text_density=200.0,
        structure_source="typographic",
        structure_confidence="high",
        section_count=1,
        zone_uncertain_ratio=0.0,
        anchor_integrity=1.0,
        reading_order_confidence=1.0,
        ingestion_confidence="high",
        confidence_reasons=["ok"],
    )
    base: dict[str, object] = {
        "document_id": "d",
        "parse_version": 1,
        "language": "de",
        "page_count": 1,
        "blocks": [block],
        "objectives": ["Die Lernenden können die Regelkreise erklären."],
        "non_narratable_text": [],
        "boilerplate": [],
        "report": report,
    }
    base.update(overrides)
    return ParsedDocument.model_validate(base)


def make_objectives(*texts: str) -> Objectives:
    items = [
        Objective(id=f"o{index}", text=text, bloom_level="understand")
        for index, text in enumerate(texts or ("Die Hörerin kann Regelkreise erklären.",))
    ]
    return Objectives(
        items=items,
        desired_outcome="Den Stoff anschließend erklären können.",
        rationale="test",
    )


def make_budget() -> ContentBudget:
    return ContentBudget(
        narratable_words=200,
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=20.0,
        requested_minutes=15,
        target_minutes=15.0,
        compression_ratio=2.6,
        verdict="ok",
        explanation="test",
    )


def run_node(
    payload: Any,
    *,
    objectives: Objectives | None = None,
    parsed: ParsedDocument | None = None,
) -> tuple[Any, FixedProvider]:
    provider = FixedProvider(payload)
    client = LLMClient(resolve=lambda _model: provider, cost_of=lambda _m, _u: 0.0)
    context = NodeContext(
        run_id="objective-select-test",
        llm=client,
        artifacts=None,  # type: ignore[arg-type]
        config={},
        logger=__import__("logging").getLogger("test"),
    )
    result = ObjectiveSelectNode().run(
        ObjectiveSelectInput(
            parsed=parsed or make_parsed(),
            budget=make_budget(),
            audience_spec=AudienceSpec(
                description="Lernende",
                desired_outcome="Den Stoff anschließend erklären können.",
            ),
            objectives=objectives or make_objectives(),
        ),
        context,
    )
    return result, provider


def test_selects_against_objectives_and_keeps_the_rationale() -> None:
    result, provider = run_node(
        {
            "selected_blocks": [
                {
                    "block_id": "b000000",
                    "reason": "erklärt den Regelkreis",
                    "objective_ids": ["o0", "missing"],
                }
            ],
            "rationale": "Nur die Passage, die das Hörziel trägt.",
        }
    )

    assert result.rationale == "Nur die Passage, die das Hörziel trägt."
    assert result.learning_goals == []
    assert [b.block_id for b in result.selected_blocks] == ["b000000"]
    assert result.selected_blocks[0].goal_ids == ["o0"]
    assert result.selected_blocks[0].reason == "erklärt den Regelkreis"

    prompt = provider.calls[0].messages[0].content
    assert "Listener objectives" in prompt
    assert "o0 [understand]" in prompt
    assert "Die Lernenden können die Regelkreise erklären." not in prompt
    assert "learning goal" not in prompt.lower()


def test_refuses_without_objectives() -> None:
    empty = Objectives(items=[], desired_outcome="x", rationale="")
    with pytest.raises(NodeError, match="no listener objectives"):
        run_node({"selected_blocks": [], "rationale": ""}, objectives=empty)


def test_refuses_unknown_block_ids() -> None:
    with pytest.raises(NodeError, match="no usable block ids"):
        run_node(
            {
                "selected_blocks": [
                    {"block_id": "does-not-exist", "objective_ids": ["o0"]},
                ],
                "rationale": "hallucinated",
            }
        )


def test_drops_duplicate_ids_and_unknown_objective_refs() -> None:
    result, _ = run_node(
        {
            "selected_blocks": [
                {"block_id": "b000000", "objective_ids": ["o0", "o99"]},
                {"block_id": "b000000", "objective_ids": ["o0"]},
            ],
            "rationale": "einmal reicht",
        }
    )
    assert len(result.selected_blocks) == 1
    assert result.selected_blocks[0].goal_ids == ["o0"]


def test_document_objectives_are_not_copied_into_the_selection() -> None:
    parsed = make_parsed(objectives=["Ein Dokumentziel, das nicht erscheinen darf."])
    result, provider = run_node(
        {
            "selected_blocks": [{"block_id": "b000000", "objective_ids": ["o0"]}],
            "rationale": "ok",
        },
        parsed=parsed,
    )
    assert result.learning_goals == []
    assert "Ein Dokumentziel" not in provider.calls[0].messages[0].content
    assert "Ein Dokumentziel" not in result.rationale


def test_replaces_select_in_a_flow() -> None:
    bootstrap_nodes()
    result = validate_flow(
        [
            {"node": "ingest", "config": {}},
            {"node": "content_budget", "config": {}},
            {"node": "objectives", "config": {}},
            {"node": "objective_select", "config": {}},
            {"node": "outline", "config": {}},
            {"node": "script", "config": {}},
        ],
        ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"],
    )
    assert result.valid, result.errors
    wiring = {c.node: c for c in result.nodes}
    keys = {w.key: w for w in wiring["objective_select"].wiring}
    assert keys["objectives"].source == "objectives"
    assert keys["parsed"].source == "ingest"
    assert keys["budget"].source == "content_budget"
    selection_wire = next(w for w in wiring["outline"].wiring if w.key == "selection")
    assert selection_wire.source == "objective_select"
