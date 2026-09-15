"""Baseline flow acceptance criteria (§13.4, AC-BL-1…3)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.ingestion.pipeline import ParseOptions, parse
from app.ingestion.zones import ZoneCache, ZoneClassifier
from app.lang.resources import words_per_minute
from app.llm import registry
from app.llm.base import LLMClient
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import discover_flows
from app.pipeline.framework.runner import FlowRunner
from app.pipeline.gates.deterministic import LENGTH_TOLERANCE
from app.pipeline.nodes.ingest import DocumentRef
from app.schemas.document import Block, ParsedDocument
from app.schemas.zones import Zone
from app.worker import _flow_directory
from tests.support import StubProvider, write_learning_pdf


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def rich_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_learning_pdf(tmp_path_factory.mktemp("rich") / "rich.pdf")


@pytest.fixture(scope="module")
def thin_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_learning_pdf(tmp_path_factory.mktemp("thin") / "thin.pdf", thin=True)


def build_client() -> LLMClient:
    return LLMClient(resolve=registry.resolve_provider, cost_of=registry.cost_usd)


def classifier_for(client: LLMClient) -> ZoneClassifier:
    from app.config import get_settings

    return ZoneClassifier(
        client=client,
        model="claude-haiku-4-5",
        cache=ZoneCache(get_settings().data_dir / "zone_cache_tests"),
    )


def run_baseline(pdf: Path, document_id: str, minutes: int = 15, **seeds: object) -> object:
    from app.config import get_settings

    client = build_client()
    store = ArtifactStore(get_settings().artifacts_dir)
    flow = discover_flows(_flow_directory())["baseline_v0"]
    runner = FlowRunner(artifacts=store, llm=client, document_path=pdf)
    return runner.execute(
        flow,
        f"test-{document_id}",
        {
            "document_ref": DocumentRef(document_id=document_id, parse_version=1, sha256="x" * 64),
            "target_minutes": minutes,
            "format_spec": get_format("two_host_dialogue"),
            "audience_spec": DEFAULT_AUDIENCE,
            **seeds,
        },
        document_id=document_id,
    )


def test_ac_bl_1_script_lands_in_the_length_band(rich_pdf: Path, provider: StubProvider) -> None:
    """AC-BL-1: a 15-minute run produces a script in the G4 band, in the document's language."""
    result = run_baseline(rich_pdf, "ac-bl-1")
    assert result.status == "completed", result.error

    parsed = result.bag["parsed"]
    script = result.bag["script"]
    budget = result.bag["budget"]

    assert parsed.language == "de", "the detected language should drive the run"
    wpm = words_per_minute(parsed.language)
    target = budget.target_minutes * wpm
    low, high = target * (1 - LENGTH_TOLERANCE), target * (1 + LENGTH_TOLERANCE)
    assert low <= script.word_count() <= high, (
        f"{script.word_count()} words is outside {low:.0f}–{high:.0f}"
    )
    assert all(segment.text.strip() for segment in script.segments)


def test_ac_bl_2_insufficient_content_fails_with_an_explanation(thin_pdf: Path) -> None:
    """AC-BL-2: too little narratable content fails at content_budget with a reason."""
    result = run_baseline(thin_pdf, "ac-bl-2")

    assert result.status == "failed"
    assert result.verdict == "insufficient"
    assert result.error
    assert "narratable words" in result.error
    assert "image content" in result.error
    # The failing node is content_budget, and ingest's artifact survives.
    names = [node.name for node in result.manifest.nodes]
    assert names[-1] == "content_budget"
    assert result.manifest.nodes[0].artifact_hash


def test_ac_bl_3_goal_source_document(rich_pdf: Path) -> None:
    """AC-BL-3: goals are ``source="document"`` when the document states objectives."""
    parsed = parse_with_objectives(rich_pdf, has_objectives=True)
    selection = select_from(parsed)
    assert selection.learning_goals
    assert all(goal.source == "document" for goal in selection.learning_goals)


def test_ac_bl_3_goal_source_generated(rich_pdf: Path) -> None:
    """AC-BL-3: goals are ``source="generated"`` when it states none."""
    parsed = parse_with_objectives(rich_pdf, has_objectives=False)
    selection = select_from(parsed)
    assert selection.learning_goals
    assert all(goal.source == "generated" for goal in selection.learning_goals)


def test_content_budget_clamps_an_overlong_request(rich_pdf: Path) -> None:
    """§6.1: a target beyond what the material supports is clamped, not refused."""
    result = run_baseline(rich_pdf, "clamp", minutes=600)
    budget = result.bag.get("budget") if result.bag else None
    assert budget is not None
    assert budget.verdict == "clamped"
    assert budget.target_minutes < budget.requested_minutes
    assert "clamped" in budget.explanation


def test_outline_budgets_sum_within_tolerance(rich_pdf: Path) -> None:
    """§6.3: the beat budgets must land within ±10% of the target word count."""
    result = run_baseline(rich_pdf, "outline-budget")
    assert result.status == "completed", result.error
    outline = result.bag["outline"]
    budget = result.bag["budget"]
    deviation = abs(outline.total_budget() - budget.target_words) / budget.target_words
    assert deviation <= 0.10


# ---------------------------------------------------------------- helpers


def parse_with_objectives(pdf: Path, *, has_objectives: bool) -> ParsedDocument:
    """Parse, then set or clear objectives without touching the pipeline.

    §6.2's two paths are selected by whether the document states objectives, so
    the test controls exactly that and nothing else.
    """
    client = build_client()
    parsed = parse(
        pdf,
        ParseOptions(document_id="goals", parse_version=1, verify_anchors=False),
        classifier=classifier_for(client),
    )
    if has_objectives:
        target = next(b for b in parsed.blocks if b.zone is Zone.BODY)
        target.zone = Zone.OBJECTIVE
        parsed.objectives = [
            "Die Lernenden können die Regelkreise des Hormonsystems erklären.",
            "Die Lernenden können Hormone ihren Drüsen zuordnen.",
        ]
    else:
        parsed.objectives = []
        for block in parsed.blocks:
            if block.zone is Zone.OBJECTIVE:
                block.zone = Zone.BODY
    return parsed


def select_from(parsed: ParsedDocument) -> object:
    from app.pipeline.framework.node import NodeContext
    from app.pipeline.nodes.select import SelectInput, SelectNode
    from app.schemas.pipeline import ContentBudget

    client = build_client()
    from app.config import get_settings

    context = NodeContext(
        run_id="goals",
        llm=client,
        artifacts=ArtifactStore(get_settings().artifacts_dir),
        config={"model": "claude-opus-5"},
        logger=__import__("logging").getLogger("test"),
    )
    budget = ContentBudget(
        narratable_words=parsed.narratable_word_count(),
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=20.0,
        requested_minutes=15,
        target_minutes=15.0,
        compression_ratio=2.6,
        verdict="ok",
        explanation="test",
    )
    return SelectNode().run(
        SelectInput(parsed=parsed, budget=budget, audience_spec=DEFAULT_AUDIENCE), context
    )


def test_block_llm_text_prefers_the_table_grid() -> None:
    """§5.7: a table reaches the model as Markdown, never as a flattened stream."""
    from app.schemas.document import TableBlock

    block = Block(
        id="b1",
        ordinal=0,
        text="Hormon Zielorgan Insulin Leber",
        page=0,
        table=TableBlock(rows=[["Insulin", "Leber"]], header=["Hormon", "Zielorgan"], n_cols=2),
    )
    rendered = block.llm_text()
    assert rendered.startswith("| Hormon | Zielorgan |")
    assert "| Insulin | Leber |" in rendered
