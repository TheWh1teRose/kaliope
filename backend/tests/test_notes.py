"""Notes: critic, human pause, and applying one note at a time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext, NodePause
from app.pipeline.framework.registry import Flow, FlowNode, bootstrap_nodes, get_node
from app.pipeline.framework.runner import FlowRunner
from app.pipeline.nodes.ai_critic import AiCriticNode, CriticInput
from app.pipeline.nodes.apply_notes import ApplyNotesInput, ApplyNotesNode, ApplyOutlineNotesInput
from app.pipeline.nodes.human_feedback import HumanFeedbackInput, HumanFeedbackNode
from app.pipeline.validation import validate_flow
from app.schemas.document import Anchor, Block, IngestionReport, ParsedDocument, RunSpan
from app.schemas.pipeline import (
    AudienceSpec,
    Beat,
    FormatSpec,
    Note,
    Notes,
    Outline,
    Script,
    Segment,
    SpeakerSpec,
)
from app.schemas.zones import Zone

BODY = "Der Hypothalamus steuert die Hypophyse und reguliert damit zahlreiche Drüsen."


class FixedProvider:
    name = "anthropic"

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[CompletionRequest] = []

    def available(self) -> bool:
        return True

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        body = self.payload(request) if callable(self.payload) else self.payload
        text = json.dumps(body, ensure_ascii=False)
        return Completion(
            text=text,
            model_id=request.model,
            usage=Usage(input_tokens=20, output_tokens=20),
            latency_ms=1,
        )


def make_parsed() -> ParsedDocument:
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
    return ParsedDocument(
        document_id="d",
        parse_version=1,
        language="de",
        page_count=1,
        page_sizes=[(595.0, 842.0)],
        blocks=[block],
        report=report,
    )


def make_outline() -> Outline:
    return Outline(
        beats=[
            Beat(
                id="beat000",
                title="Regelkreise",
                block_ids=["b000000"],
                word_budget=200,
                summary="Einstieg",
            )
        ]
    )


def make_script() -> Script:
    return Script(
        segments=[
            Segment(
                id="beat000-s0000",
                speaker="Moderator",
                text="Womit fangen wir an?",
                kind="pedagogy",
                beat_id="beat000",
            ),
            Segment(
                id="beat000-s0001",
                speaker="Expertin",
                text=BODY,
                kind="claim",
                anchors=[
                    Anchor(
                        document_id="d",
                        parse_version=1,
                        block_id="b000000",
                        char_start=0,
                        char_end=20,
                    )
                ],
                beat_id="beat000",
            ),
        ]
    )


def make_format() -> FormatSpec:
    return FormatSpec(
        id="two",
        name="Zwei",
        speakers=[
            SpeakerSpec(id="mod", name="Moderator", role="host"),
            SpeakerSpec(id="exp", name="Expertin", role="expert"),
        ],
        register="gesprächig",
        target_minutes=10,
    )


def make_audience() -> AudienceSpec:
    return AudienceSpec(description="Medizinstudierende")


def ctx(
    tmp_path: Path, provider: FixedProvider, config: dict[str, Any] | None = None
) -> NodeContext:
    client = LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0)
    return NodeContext(
        run_id="r",
        llm=client,
        artifacts=ArtifactStore(tmp_path / "art"),
        config=config or {},
        logger=__import__("logging").getLogger("test"),
    )


@pytest.fixture(autouse=True)
def _nodes() -> None:
    bootstrap_nodes()


def test_critic_writes_notes_on_beats(tmp_path: Path) -> None:
    provider = FixedProvider(
        {
            "notes": [
                {
                    "target_kind": "beat",
                    "target_id": "beat000",
                    "text": "Kürzer einsteigen.",
                    "criterion": "Sprache",
                }
            ]
        }
    )
    out = AiCriticNode().run(
        CriticInput(outline=make_outline(), script=make_script()),
        ctx(tmp_path, provider),
    )
    assert out.subject == "script"
    assert len(out.items) == 1
    assert out.items[0].target.id == "beat000"
    assert out.items[0].source == "ai_critic"


def test_critic_drops_unknown_targets(tmp_path: Path) -> None:
    provider = FixedProvider(
        {
            "notes": [
                {
                    "target_kind": "segment",
                    "target_id": "missing",
                    "text": "Geht nicht.",
                    "criterion": "x",
                }
            ]
        }
    )
    out = AiCriticNode().run(CriticInput(outline=make_outline()), ctx(tmp_path, provider))
    assert out.items == []


def test_human_feedback_pauses(tmp_path: Path) -> None:
    with pytest.raises(NodePause) as raised:
        HumanFeedbackNode().run(
            HumanFeedbackInput(outline=make_outline()),
            ctx(tmp_path, FixedProvider({}), {"instructions": "Bitte prüfen."}),
        )
    assert raised.value.payload["subject"] == "outline"
    assert raised.value.payload["instructions"] == "Bitte prüfen."


def test_apply_notes_revises_one_segment(tmp_path: Path) -> None:
    provider = FixedProvider(
        {
            "segments": [
                {
                    "speaker": "Moderator",
                    "text": "Kurz und klar.",
                    "kind": "pedagogy",
                    "citations": [],
                }
            ]
        }
    )
    notes = Notes(
        subject="script",
        items=[
            Note(
                id="n1",
                text="Kürzer.",
                target={"kind": "segment", "id": "beat000-s0000"},
                source="human",
            )
        ],
    )
    out = ApplyNotesNode().run(
        ApplyNotesInput(
            notes=notes,
            parsed=make_parsed(),
            script=make_script(),
            format_spec=make_format(),
            audience_spec=make_audience(),
            outline=make_outline(),
        ),
        ctx(tmp_path, provider),
    )
    assert out.segments[0].text == "Kurz und klar."
    assert any(segment.id == "beat000-s0001" for segment in out.segments)
    assert provider.calls  # one note, one call
    prompt = provider.calls[0].messages[-1].content
    context, rest = prompt.split("Current segments to replace:", 1)
    replace, _passages = rest.split("Passages you may draw facts from:", 1)
    assert "Surrounding beat" in context
    assert "Womit fangen wir an?" in context
    assert BODY in context
    assert "← replace this" in context
    assert "Womit fangen wir an?" in replace
    assert BODY not in replace


def test_apply_notes_empty_is_noop(tmp_path: Path) -> None:
    script = make_script()
    out = ApplyNotesNode().run(
        ApplyNotesInput(
            notes=Notes(items=[], subject="script"),
            parsed=make_parsed(),
            script=script,
            format_spec=make_format(),
            audience_spec=make_audience(),
        ),
        ctx(tmp_path, FixedProvider({"segments": []})),
    )
    assert out.segments == script.segments


def test_apply_outline_notes_revises_beat(tmp_path: Path) -> None:
    from app.pipeline.nodes.apply_notes import ApplyOutlineNotesNode

    provider = FixedProvider(
        {
            "title": "Neu",
            "summary": "Anders",
            "block_ids": ["b000000"],
            "word_budget": 180,
        }
    )
    notes = Notes(
        subject="outline",
        items=[
            Note(
                id="n1",
                text="Titel schärfen.",
                target={"kind": "beat", "id": "beat000"},
                source="human",
            )
        ],
    )
    out = ApplyOutlineNotesNode().run(
        ApplyOutlineNotesInput(notes=notes, outline=make_outline(), parsed=make_parsed()),
        ctx(tmp_path, provider),
    )
    assert out.beats[0].id == "beat000"
    assert out.beats[0].title == "Neu"


def test_validation_allows_optional_outline_on_critic() -> None:
    result = validate_flow(
        [{"node": "script", "config": {}}, {"node": "ai_critic", "config": {}}],
        [],
        as_pipeline=False,
        extra_seeds=["parsed", "outline", "format_spec", "audience_spec"],
    )
    assert result.valid, result.errors
    critic = next(node for node in result.nodes if node.node == "ai_critic")
    assert "script" in {wire.key for wire in critic.wiring if wire.source == "script"}


def test_runner_pauses_and_resumes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "art")
    provider = FixedProvider({"notes": []})
    client = LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0)
    runner = FlowRunner(artifacts=store, llm=client)
    flow = Flow(
        id="notes",
        version="1",
        nodes=[FlowNode(node="human_feedback"), FlowNode(node="apply_notes")],
    )
    seeds = {
        "outline": make_outline(),
        "script": make_script(),
        "parsed": make_parsed(),
        "format_spec": make_format(),
        "audience_spec": make_audience(),
    }
    paused = runner.execute(flow, "r1", seeds)
    assert paused.status == "paused"
    assert paused.paused_at == "human_feedback"

    notes = Notes(
        subject="script",
        items=[
            Note(
                id="n1",
                text="Kürzer.",
                target={"kind": "segment", "id": "beat000-s0000"},
                source="human",
            )
        ],
    )
    stored = store.put("notes", notes)
    provider.payload = {
        "segments": [
            {
                "speaker": "Moderator",
                "text": "Neu.",
                "kind": "pedagogy",
                "citations": [],
            }
        ]
    }
    resumed = runner.execute(flow, "r1", seeds, resume_outputs={"human_feedback": stored.hash})
    assert resumed.status == "completed"
    assert resumed.bag["notes"].items[0].text == "Kürzer."
    assert resumed.bag["script"].segments[0].text == "Neu."


def test_nodes_are_registered() -> None:
    for name in ("ai_critic", "human_feedback", "apply_notes", "apply_outline_notes"):
        node = get_node(name)
        assert node.produces in {"notes", "script", "outline"}
