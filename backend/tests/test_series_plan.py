"""The series planner: the model proposes the split, the node makes it hold."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.nodes.series_plan import (
    MAX_EPISODES,
    SeriesPlanInput,
    SeriesPlanNode,
    build_plan,
    episode_count,
)
from app.schemas.pipeline import ContentBudget, SeriesRequest
from tests.series_support import AUDIENCE, FORMAT, learning_document
from tests.support import StubProvider

#: 20 blocks of 140 words: 2,800 words, 8.3 supportable minutes at 135 wpm, 2.5×.
DOC = learning_document()
IDS = [b.id for b in DOC.blocks]


def _budget(minutes: float = 8.3) -> ContentBudget:
    return ContentBudget(
        narratable_words=2800,
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=minutes,
        requested_minutes=8,
        target_minutes=8,
        compression_ratio=2.6,
        verdict="ok",
        explanation="",
    )


def _plan(
    data: dict[str, Any],
    minutes: int = 4,
    count: int | None = None,
    budget: Any = None,
) -> Any:
    if count is None:
        count = len([e for e in data.get("episodes", []) if isinstance(e, dict)])
    return build_plan(
        data,
        parsed=DOC,
        budget=budget or _budget(),
        minutes=minutes,
        count=count,
    )


def _episode(ids: list[str], **extra: Any) -> dict[str, Any]:
    return {"title": "T", "block_ids": ids, "goals": ["Ziel"], **extra}


def test_count_follows_the_budget_unless_the_run_asked_for_one() -> None:
    assert episode_count(46.2, 15, None) == 3
    assert episode_count(20.0, 15, None) == 2, "a series has at least two episodes"
    assert episode_count(500.0, 5, None) == MAX_EPISODES
    assert episode_count(46.2, 15, 5) == 5
    assert episode_count(46.2, 15, 20) == MAX_EPISODES


def test_unknown_ids_are_dropped_and_each_block_keeps_its_first_home() -> None:
    plan = _plan(
        {
            "title": "S",
            "episodes": [
                _episode(["nope", *IDS[:10]]),
                _episode([IDS[9], *IDS[10:]]),
            ],
        }
    )
    assert [e.block_ids for e in plan.episodes] == [IDS[:10], IDS[10:]]
    assert [e.id for e in plan.episodes] == ["ep01", "ep02"]
    assert [e.index for e in plan.episodes] == [1, 2]


def test_blocks_the_model_forgot_join_the_episode_around_them() -> None:
    plan = _plan(
        {
            "title": "S",
            "episodes": [_episode(IDS[2:8]), _episode(IDS[10:17])],
            "unassigned": [{"block_id": IDS[19], "reason": "Wiederholung"}],
        }
    )
    first, second = plan.episodes
    assert first.block_ids == IDS[:10], "leading and in-between orphans join the earlier home"
    assert second.block_ids == IDS[10:19]
    assert [(u.block_id, u.reason) for u in plan.unassigned] == [(IDS[19], "Wiederholung")]


def test_an_episode_below_three_minutes_merges_into_its_neighbour() -> None:
    plan = _plan(
        {
            "title": "S",
            "episodes": [_episode(IDS[:9]), _episode(IDS[9:18]), _episode(IDS[18:])],
        }
    )
    # The last episode has 280 words (0.8 min): it merges into the second.
    assert len(plan.episodes) == 2
    assert plan.episodes[1].block_ids == IDS[9:]
    assert plan.budget.verdict == "reduced"


def test_extra_viable_episodes_stay_when_fewer_were_asked() -> None:
    cuts = [0, 4, 8, 11, 14, 17, 20]
    groups = [IDS[cuts[i] : cuts[i + 1]] for i in range(6)]
    budget = _budget().model_copy(update={"min_compression": 1.0})
    plan = _plan(
        {"title": "S", "episodes": [_episode(group) for group in groups]},
        minutes=3,
        count=4,
        budget=budget,
    )
    assert len(plan.episodes) == 6
    assert plan.budget.requested_episodes == 4
    assert plan.budget.verdict == "ok"
    assert sorted(block_id for episode in plan.episodes for block_id in episode.block_ids) == IDS


def test_fewer_episodes_than_asked_are_kept() -> None:
    plan = _plan(
        {"title": "S", "episodes": [_episode(IDS[:10]), _episode(IDS[10:])]},
        count=4,
    )
    assert len(plan.episodes) == 2
    assert plan.budget.requested_episodes == 4


def test_an_episode_that_cannot_carry_its_length_is_shortened() -> None:
    plan = _plan({"title": "S", "episodes": [_episode(IDS[:10]), _episode(IDS[10:])]}, minutes=5)
    for episode in plan.episodes:
        assert episode.supportable_minutes == pytest.approx(4.15, abs=0.01)
        assert episode.target_minutes == episode.supportable_minutes
    assert plan.budget.verdict == "clamped"


def test_recap_ids_must_come_from_earlier_episodes_and_goals_get_ids() -> None:
    plan = _plan(
        {
            "title": "S",
            "episodes": [
                _episode(IDS[:10], recap_block_ids=[IDS[0]], preview="Weiter"),
                _episode(
                    IDS[10:],
                    recap_block_ids=[IDS[0], IDS[1], IDS[2], IDS[3], IDS[15]],
                    recap="Zurück",
                    preview="ignoriert",
                ),
            ],
            "terms": [{"term": "Kipppunkt", "first_episode": 9}],
        }
    )
    first, second = plan.episodes
    assert first.recap_block_ids == [] and first.recap is None and first.preview == "Weiter"
    assert second.recap_block_ids == IDS[:3]
    assert second.recap == "Zurück" and second.preview is None, "the last episode has no preview"
    assert [g.id for g in first.goals] == ["ep01-g0"]
    assert plan.terms[0].first_episode == 2


def test_no_usable_episode_fails_the_node() -> None:
    with pytest.raises(NodeError):
        _plan({"title": "S", "episodes": [_episode(["x", "y"])]})


def _context(tmp_path: Path, provider: StubProvider) -> NodeContext:
    return NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=ArtifactStore(tmp_path),
        config={"model": "claude-opus-5"},
        logger=logging.getLogger("test"),
    )


def test_the_node_plans_the_series_from_one_call(tmp_path: Path) -> None:
    provider = StubProvider()
    plan = SeriesPlanNode().run(
        SeriesPlanInput(
            parsed=DOC,
            budget=_budget(),
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=SeriesRequest(minutes_per_episode=4, hint="Klimaschutz zuletzt"),
        ),
        _context(tmp_path, provider),
    )
    assert len(provider.calls) == 1
    message = provider.calls[0].messages[0].content
    assert "Plan 2 episodes of about 4 minutes each." in message
    assert "Guidance on the split: Klimaschutz zuletzt" in message
    assert "## Der Treibhauseffekt" in message
    assert len(plan.episodes) == 2
    assert sorted(b for e in plan.episodes for b in e.block_ids) == IDS
    assert plan.title == "Eine Serie" and plan.through_line


def test_a_document_too_thin_for_two_episodes_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NodeError) as raised:
        SeriesPlanNode().run(
            SeriesPlanInput(
                parsed=DOC,
                budget=_budget(5.0),
                format_spec=FORMAT,
                audience_spec=AUDIENCE,
                series_request=SeriesRequest(minutes_per_episode=10),
            ),
            _context(tmp_path, StubProvider()),
        )
    assert raised.value.verdict == "insufficient"


class _ScriptedPlan:
    """Returns a fixed number of viable episodes on each call, ignoring the prompt."""

    def __init__(self, counts: list[int]) -> None:
        self.counts = counts
        self.calls: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        count = self.counts[len(self.calls) - 1]
        size = len(IDS) // count
        groups = [IDS[index * size : (index + 1) * size] for index in range(count - 1)]
        groups.append(IDS[(count - 1) * size :])
        payload = {
            "title": "S",
            "through_line": "Bogen",
            "episodes": [_episode(group) for group in groups],
        }
        return Completion(
            text=json.dumps(payload),
            model_id=request.model,
            usage=Usage(input_tokens=1, output_tokens=1),
            latency_ms=1,
        )


def _viable_budget() -> ContentBudget:
    return _budget(24).model_copy(update={"min_compression": 1.0})


def _ask(tmp_path: Path, counts: list[int]) -> tuple[Any, _ScriptedPlan]:
    provider = _ScriptedPlan(counts)
    plan = SeriesPlanNode().run(
        SeriesPlanInput(
            parsed=DOC,
            budget=_viable_budget(),
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=SeriesRequest(episodes=4, minutes_per_episode=3),
        ),
        _context(tmp_path, provider),  # type: ignore[arg-type]
    )
    return plan, provider


def test_a_wrong_count_is_retried_once_naming_the_asked_count(tmp_path: Path) -> None:
    plan, provider = _ask(tmp_path, [6, 4])
    assert len(provider.calls) == 2
    first = provider.calls[0].messages[0].content
    second = provider.calls[1].messages[0].content
    assert "Plan exactly 4 episodes." not in first
    assert "Plan exactly 4 episodes." in second
    assert len(plan.episodes) == 4
    assert plan.budget.requested_episodes == 4


def test_a_count_that_still_differs_is_accepted(tmp_path: Path) -> None:
    plan, provider = _ask(tmp_path, [2, 2])
    assert len(provider.calls) == 2
    assert len(plan.episodes) == 2
    assert plan.budget.requested_episodes == 4
    assert sorted(block_id for episode in plan.episodes for block_id in episode.block_ids) == IDS
