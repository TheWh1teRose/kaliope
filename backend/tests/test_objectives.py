"""The ``objectives`` node: listener changes derived from the desired outcome."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import bootstrap_nodes
from app.pipeline.framework.spec import node_params
from app.pipeline.nodes.objectives import ObjectivesInput, ObjectivesNode, _bloom_level
from app.pipeline.validation import validate_flow
from app.schemas.document import Block, IngestionReport, ParsedDocument, RunSpan
from app.schemas.pipeline import AudienceSpec, ContentBudget
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
        "objectives": [],
        "non_narratable_text": [],
        "boilerplate": [],
        "report": report,
    }
    base.update(overrides)
    return ParsedDocument.model_validate(base)


def make_budget(*, target_minutes: float = 8.0) -> ContentBudget:
    return ContentBudget(
        narratable_words=2000,
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=20.0,
        requested_minutes=int(target_minutes),
        target_minutes=target_minutes,
        compression_ratio=2.6,
        verdict="ok",
        explanation="test",
    )


def run_node(
    payload: Any,
    *,
    desired_outcome: str | None = "Den Stoff anschließend erklären können.",
    config: dict[str, Any] | None = None,
    budget: ContentBudget | None = None,
) -> tuple[Any, FixedProvider]:
    provider = FixedProvider(payload)
    client = LLMClient(resolve=lambda _model: provider, cost_of=lambda _m, _u: 0.0)
    context = NodeContext(
        run_id="objectives-test",
        llm=client,
        artifacts=None,  # type: ignore[arg-type]
        config=config or {},
        logger=__import__("logging").getLogger("test"),
    )
    audience = AudienceSpec(
        description="Lernende, die den Stoff zum ersten Mal hören.",
        desired_outcome=desired_outcome,
    )
    result = ObjectivesNode().run(
        ObjectivesInput(
            parsed=make_parsed(),
            audience_spec=audience,
            budget=budget or make_budget(),
        ),
        context,
    )
    return result, provider


def test_bloom_aliases_snap_onto_the_taxonomy() -> None:
    assert _bloom_level("analyze") == "analyse"
    assert _bloom_level("Analyse") == "analyse"
    assert _bloom_level("knowledge") == "remember"
    assert _bloom_level("synthesis") == "create"
    assert _bloom_level("not-a-level") is None
    assert _bloom_level(None) is None


def test_refuses_when_the_desired_outcome_is_missing() -> None:
    with pytest.raises(NodeError, match="no desired outcome"):
        run_node({"objectives": [], "rationale": ""}, desired_outcome=None)
    with pytest.raises(NodeError, match="no desired outcome"):
        run_node({"objectives": [], "rationale": ""}, desired_outcome="   ")


def test_derives_objectives_from_the_desired_outcome() -> None:
    result, provider = run_node(
        {
            "objectives": [
                {
                    "id": "o0",
                    "text": "Die Hörerin kann die Regelkreise erklären.",
                    "bloom_level": "understand",
                    "derivation": "Erklären ist das gewünschte Ergebnis.",
                },
                {
                    "text": "Die Hörerin kann Hormone ihren Drüsen zuordnen.",
                    "bloom_level": "remember",
                },
            ],
            "rationale": "Vom gewünschten Ergebnis abgeleitet.",
        }
    )

    assert result.desired_outcome == "Den Stoff anschließend erklären können."
    assert result.rationale == "Vom gewünschten Ergebnis abgeleitet."
    assert [o.bloom_level for o in result.items] == ["understand", "remember"]
    assert result.items[0].id == "o0"
    assert result.items[1].id == "o1"
    assert result.items[0].derivation == "Erklären ist das gewünschte Ergebnis."

    prompt = provider.calls[0].messages[0].content
    assert "Desired outcome" in prompt
    assert "Den Stoff anschließend erklären können." in prompt
    assert "Time budget" in prompt
    assert "8.0 minutes" in prompt
    assert BODY[:40] in prompt


def test_snaps_american_analyze_and_drops_unknown_levels() -> None:
    result, _ = run_node(
        {
            "objectives": [
                {"text": "Teile eines Regelkreises unterscheiden.", "bloom_level": "analyze"},
                {"text": "Sollte verworfen werden.", "bloom_level": "master"},
                {"text": "", "bloom_level": "understand"},
                {"text": "Eine Therapie bewerten.", "bloom_level": "evaluate"},
            ],
            "rationale": "",
        }
    )
    assert [(o.text, o.bloom_level) for o in result.items] == [
        ("Teile eines Regelkreises unterscheiden.", "analyse"),
        ("Eine Therapie bewerten.", "evaluate"),
    ]


def test_caps_at_max_objectives() -> None:
    result, _ = run_node(
        {
            "objectives": [{"text": f"Ziel {i}", "bloom_level": "understand"} for i in range(8)],
            "rationale": "",
        },
        config={"max_objectives": 3},
    )
    assert len(result.items) == 3
    assert [o.text for o in result.items] == ["Ziel 0", "Ziel 1", "Ziel 2"]


def test_fails_when_nothing_usable_survives() -> None:
    with pytest.raises(NodeError, match="no usable listener objectives"):
        run_node(
            {
                "objectives": [
                    {"text": "", "bloom_level": "understand"},
                    {"text": "Kein Level", "bloom_level": "wizard"},
                ],
                "rationale": "",
            }
        )


def test_the_desired_outcome_is_the_source_in_the_prompt() -> None:
    _, provider = run_node(
        {
            "objectives": [
                {"text": "Erklären können.", "bloom_level": "understand"},
            ],
            "rationale": "",
        }
    )
    user = provider.calls[0].messages[0].content
    source_at = user.index("Desired outcome")
    time_at = user.index("Time budget")
    constraint_at = user.index("Narratable material")
    assert source_at < time_at < constraint_at
    assert "the source" in user.split("Desired outcome", 1)[1][:80].lower()
    assert "constraint" in user.split("Time budget", 1)[1][:80].lower()


def test_can_be_wired_after_ingest() -> None:
    bootstrap_nodes()
    result = validate_flow(
        [
            {"node": "ingest", "config": {}},
            {"node": "content_budget", "config": {}},
            {"node": "objectives", "config": {}},
            {"node": "select", "config": {}},
            {"node": "outline", "config": {}},
            {"node": "script", "config": {}},
        ],
        ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"],
    )
    assert result.valid, result.errors
    wiring = {c.node: c for c in result.nodes}
    keys = {w.key: w for w in wiring["objectives"].wiring}
    assert keys["parsed"].source == "ingest"
    assert keys["budget"].source == "content_budget"
    assert keys["audience_spec"].from_seed is True


def test_system_prompt_is_an_editable_parameter() -> None:
    prompt = next(p for p in node_params(ObjectivesNode()) if p.key == "system_prompt")
    assert prompt.type == "prompt"
    assert "Bloom" in str(prompt.default)
    assert "desired outcome" in str(prompt.default).lower()
    assert "time budget" in str(prompt.default).lower()
