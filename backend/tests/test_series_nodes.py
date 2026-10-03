"""The episode inputs of the existing nodes, the series schema migration and G5 per episode."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa

from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine
from app.llm.base import LLMClient
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext
from app.pipeline.gates.base import GateContext
from app.pipeline.gates.deterministic import G5ObjectiveCoverage
from app.pipeline.nodes.content_budget import ContentBudgetInput, ContentBudgetNode
from app.pipeline.nodes.outline import OutlineInput, OutlineNode
from app.pipeline.nodes.select import SelectInput, SelectNode
from app.schemas.pipeline import (
    Beat,
    EpisodeBrief,
    EpisodePlan,
    LearningGoal,
    Outline,
    Script,
    Selection,
)
from tests.series_support import AUDIENCE, FORMAT, learning_document
from tests.support import StubProvider

DOC = learning_document()
IDS = [b.id for b in DOC.blocks]


def _brief(index: int = 2, *, objective_refs: list[str] | None = None) -> EpisodeBrief:
    return EpisodeBrief(
        series_title="Klima",
        through_line="Vom Mechanismus zum Handeln.",
        episode_count=3,
        episode=EpisodePlan(
            id=f"ep{index:02d}",
            index=index,
            title="Was sich verändert",
            role="Folgen",
            block_ids=IDS[8:18],
            recap_block_ids=[IDS[0]],
            goals=[LearningGoal(id="ep02-g0", text="Folgen erklären", source="generated")],
            objective_refs=objective_refs or [],
            target_minutes=3,
            supportable_minutes=2.07,
            recap="Die Wärmedecke.",
            preview="Was wir tun können.",
        ),
    )


def _context(provider: StubProvider, tmp_path: Path) -> NodeContext:
    return NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=ArtifactStore(tmp_path),
        config={"model": "claude-opus-5"},
        logger=logging.getLogger("test"),
    )


def test_an_episode_budget_counts_only_its_own_passages(tmp_path: Path) -> None:
    whole = ContentBudgetNode().run(
        ContentBudgetInput(parsed=DOC, target_minutes=3), _context(StubProvider(), tmp_path)
    )
    episode = ContentBudgetNode().run(
        ContentBudgetInput(parsed=DOC, target_minutes=15, episode_brief=_brief()),
        _context(StubProvider(), tmp_path),
    )
    assert whole.narratable_words == 2800
    assert episode.narratable_words == 1400
    assert episode.verdict == "clamped" and episode.target_minutes == 4.15


def test_select_sees_only_the_episode_and_keeps_the_planned_goals(tmp_path: Path) -> None:
    provider = StubProvider()
    budget = ContentBudgetNode().run(
        ContentBudgetInput(parsed=DOC, target_minutes=8), _context(provider, tmp_path)
    )
    selection = SelectNode().run(
        SelectInput(parsed=DOC, budget=budget, audience_spec=AUDIENCE, episode_brief=_brief()),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[0].content
    offered = [line.split("]")[0][1:] for line in message.splitlines() if line.startswith("[b")]
    assert offered == [IDS[0], *IDS[8:18]]
    assert f"[{IDS[0]}] (weight 1.0) section: Wetter und Klima (recap from an earlier episode)" in (
        message
    )
    assert "This is episode 2 of 3 of the series" in message
    assert "- ep02-g0: Folgen erklären" in message
    assert [g.id for g in selection.learning_goals] == ["ep02-g0"]


def test_outline_learns_where_the_episode_stands(tmp_path: Path) -> None:
    provider = StubProvider()
    budget = ContentBudgetNode().run(
        ContentBudgetInput(parsed=DOC, target_minutes=8), _context(provider, tmp_path)
    )
    selection = Selection(
        learning_goals=[LearningGoal(id="ep02-g0", text="Folgen erklären", source="generated")],
        selected_blocks=[{"block_id": b} for b in IDS[10:15]],  # type: ignore[misc]
        rationale="",
    )
    OutlineNode().run(
        OutlineInput(
            parsed=DOC,
            selection=selection,
            budget=budget,
            format_spec=FORMAT,
            episode_brief=_brief(),
        ),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[0].content
    assert "This is episode 2 of 3" in message
    assert "opens with a short recap of the earlier episodes: Die Wärmedecke." in message
    assert "points ahead to the next episode: Was wir tun können." in message


def test_g5_scores_only_the_objectives_the_plan_gave_the_episode() -> None:
    parsed = DOC.model_copy(
        update={"objectives": ["Treibhauseffekt erklären können", "Klimaschutzmaßnahmen bewerten"]}
    )
    outline = Outline(
        beats=[
            Beat(id="beat000", title="Treibhauseffekt erklären", block_ids=IDS[:2], word_budget=9)
        ]
    )
    selection = Selection(learning_goals=[], selected_blocks=[], rationale="")
    budget = ContentBudgetNode().run(
        ContentBudgetInput(parsed=DOC, target_minutes=3),
        _context(StubProvider(), Path("/tmp")),
    )

    def report(brief: EpisodeBrief | None) -> str:
        return (
            G5ObjectiveCoverage()
            .check(
                GateContext(
                    parsed=parsed,
                    script=Script(segments=[]),
                    outline=outline,
                    selection=selection,
                    budget=budget,
                    format_spec=FORMAT,
                    episode_brief=brief,
                )
            )
            .status
        )

    assert report(None) == "warn", "the whole document's second objective is not served"
    assert report(_brief(objective_refs=["Treibhauseffekt erklären können"])) == "pass"
    assert report(_brief(objective_refs=[])) == "skipped"


@contextmanager
def _data_dir(path: Path) -> Iterator[None]:
    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=path)  # type: ignore[arg-type]
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        yield
    finally:
        set_settings(previous)
        reset_engine()


def test_the_migration_adds_series_and_keeps_existing_runs(tmp_path: Path) -> None:
    from alembic import command
    from app.migrations import _alembic_config

    with _data_dir(tmp_path / "migrate"):
        config = _alembic_config()
        command.upgrade(config, "56833927ac0b")
        with get_engine().begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO documents (id, filename, sha256, uploaded_at, parse_version,"
                    " parse_status, title_edited) VALUES ('d1', 'a.pdf', 'x', '2026-10-01',"
                    " 1, 'parsed', 0)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO runs (id, document_id, flow_id, flow_version, flow_revision,"
                    " config_json, format_spec_json, audience_spec_json, status, created_at,"
                    " total_cost_usd) VALUES ('r1', 'd1', 'baseline_v0', '1.0', 0, '{}', '{}',"
                    " '{}', 'completed', '2026-10-01', 0.5)"
                )
            )

        command.upgrade(config, "head")
        with get_engine().connect() as connection:
            inspector = sa.inspect(connection)
            assert "series" in inspector.get_table_names()
            row = connection.execute(
                sa.text("SELECT series_id, episode_index, context_hash, status FROM runs")
            ).one()
        assert tuple(row) == (None, None, None, "completed")

        command.downgrade(config, "56833927ac0b")
        with get_engine().connect() as connection:
            inspector = sa.inspect(connection)
            assert "series" not in inspector.get_table_names()
            columns = {c["name"] for c in inspector.get_columns("runs")}
            assert not columns & {"series_id", "episode_index", "context_hash"}
            assert connection.execute(sa.text("SELECT id FROM runs")).scalar() == "r1"

        command.upgrade(config, "head")
