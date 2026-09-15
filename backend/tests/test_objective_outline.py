"""The ``objective_outline`` node: beats planned against listener objectives."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.formats import TWO_HOST_DIALOGUE
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import bootstrap_nodes
from app.pipeline.nodes.objective_outline import ObjectiveOutlineInput, ObjectiveOutlineNode
from app.pipeline.validation import validate_flow
from app.schemas.document import Block, IngestionReport, ParsedDocument, RunSpan
from app.schemas.pipeline import (
    ContentBudget,
    LearningGoal,
    Objective,
    Objectives,
    SelectedBlock,
    Selection,
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


def make_selection(*, goal_ids: list[str] | None = None) -> Selection:
    return Selection(
        learning_goals=[],
        selected_blocks=[
            SelectedBlock(
                block_id="b000000",
                salience=1.0,
                reason="trägt das Hörziel",
                goal_ids=goal_ids or ["o0"],
            )
        ],
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
    selection: Selection | None = None,
    parsed: ParsedDocument | None = None,
) -> tuple[Any, FixedProvider]:
    provider = FixedProvider(payload)
    client = LLMClient(resolve=lambda _model: provider, cost_of=lambda _m, _u: 0.0)
    context = NodeContext(
        run_id="objective-outline-test",
        llm=client,
        artifacts=None,  # type: ignore[arg-type]
        config={},
        logger=__import__("logging").getLogger("test"),
    )
    result = ObjectiveOutlineNode().run(
        ObjectiveOutlineInput(
            parsed=parsed or make_parsed(),
            selection=selection or make_selection(),
            budget=make_budget(),
            format_spec=TWO_HOST_DIALOGUE,
            objectives=objectives or make_objectives(),
        ),
        context,
    )
    return result, provider


def test_plans_beats_against_objectives() -> None:
    result, provider = run_node(
        {
            "beats": [
                {
                    "title": "Regelkreise",
                    "summary": "Was der Hypothalamus steuert",
                    "block_ids": ["b000000"],
                    "word_budget": 2025,
                    "objective_id": "o0",
                }
            ]
        }
    )

    assert len(result.beats) == 1
    beat = result.beats[0]
    assert beat.title == "Regelkreise"
    assert beat.block_ids == ["b000000"]
    assert beat.goal_id == "o0"
    assert beat.summary == "Was der Hypothalamus steuert"
    assert beat.word_budget == 2025

    prompt = provider.calls[0].messages[0].content
    assert "Listener objectives" in prompt
    assert "o0 [understand]" in prompt
    assert "(serves o0)" in prompt
    assert "Die Lernenden können die Regelkreise erklären." not in prompt
    assert "learning goal" not in prompt.lower()


def test_accepts_goal_id_as_an_alias() -> None:
    result, _ = run_node(
        {
            "beats": [
                {
                    "title": "Regelkreise",
                    "block_ids": ["b000000"],
                    "word_budget": 2025,
                    "goal_id": "o0",
                }
            ]
        }
    )
    assert result.beats[0].goal_id == "o0"


def test_strips_unknown_objective_refs() -> None:
    result, _ = run_node(
        {
            "beats": [
                {
                    "title": "Regelkreise",
                    "block_ids": ["b000000"],
                    "word_budget": 2025,
                    "objective_id": "missing",
                }
            ]
        }
    )
    assert result.beats[0].goal_id is None


def test_refuses_without_objectives() -> None:
    empty = Objectives(items=[], desired_outcome="x", rationale="")
    with pytest.raises(NodeError, match="no listener objectives"):
        run_node(
            {
                "beats": [
                    {
                        "title": "x",
                        "block_ids": ["b000000"],
                        "word_budget": 10,
                        "objective_id": "o0",
                    }
                ]
            },
            objectives=empty,
        )


def test_refuses_when_selection_ids_are_unknown() -> None:
    selection = Selection(
        learning_goals=[],
        selected_blocks=[SelectedBlock(block_id="does-not-exist", goal_ids=["o0"])],
        rationale="",
    )
    with pytest.raises(NodeError, match="no block ids that exist"):
        run_node(
            {
                "beats": [
                    {
                        "title": "x",
                        "block_ids": ["does-not-exist"],
                        "word_budget": 10,
                        "objective_id": "o0",
                    }
                ]
            },
            selection=selection,
        )


def test_refuses_beats_with_no_usable_block_ids() -> None:
    with pytest.raises(NodeError, match="no beats with usable block ids"):
        run_node(
            {
                "beats": [
                    {
                        "title": "halluziniert",
                        "block_ids": ["does-not-exist"],
                        "word_budget": 10,
                        "objective_id": "o0",
                    }
                ]
            }
        )


def test_ignores_learning_goals_on_the_selection() -> None:
    selection = Selection(
        learning_goals=[LearningGoal(id="g0", text="Ein Dokumentziel", source="document")],
        selected_blocks=[SelectedBlock(block_id="b000000", goal_ids=["o0"])],
        rationale="",
    )
    result, provider = run_node(
        {
            "beats": [
                {
                    "title": "Regelkreise",
                    "block_ids": ["b000000"],
                    "word_budget": 2025,
                    "objective_id": "o0",
                }
            ]
        },
        selection=selection,
    )
    assert result.beats[0].goal_id == "o0"
    prompt = provider.calls[0].messages[0].content
    assert "g0" not in prompt
    assert "Ein Dokumentziel" not in prompt


def test_rescales_budgets_that_miss_the_target() -> None:
    result, _ = run_node(
        {
            "beats": [
                {
                    "title": "Kurz",
                    "block_ids": ["b000000"],
                    "word_budget": 10,
                    "objective_id": "o0",
                }
            ]
        }
    )
    assert result.total_budget() == 2025
    assert result.beats[0].word_budget == 2025


def test_replaces_outline_in_a_flow() -> None:
    bootstrap_nodes()
    result = validate_flow(
        [
            {"node": "ingest", "config": {}},
            {"node": "content_budget", "config": {}},
            {"node": "objectives", "config": {}},
            {"node": "objective_select", "config": {}},
            {"node": "objective_outline", "config": {}},
            {"node": "script", "config": {}},
        ],
        ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"],
    )
    assert result.valid, result.errors
    wiring = {c.node: c for c in result.nodes}
    keys = {w.key: w for w in wiring["objective_outline"].wiring}
    assert keys["objectives"].source == "objectives"
    assert keys["selection"].source == "objective_select"
    assert keys["parsed"].source == "ingest"
    assert keys["budget"].source == "content_budget"
    assert keys["format_spec"].from_seed is True
    assert wiring["objective_outline"].produces == "outline"
    script_wire = next(w for w in wiring["script"].wiring if w.key == "outline")
    assert script_wire.source == "objective_outline"
