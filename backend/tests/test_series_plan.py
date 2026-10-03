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
from app.pipeline.nodes.content_budget import source_word_budget, supportable_minutes
from app.pipeline.nodes.series_plan import (
    LEFT_OUT_FOR_DIALOGUE,
    MAX_EPISODES,
    SeriesPlanInput,
    SeriesPlanNode,
    build_plan,
    episode_count,
)
from app.pipeline.objective_rule import (
    OBJECTIVE_FORMULATION_RULE,
    desired_outcome_source,
    time_budget_constraint,
)
from app.schemas.pipeline import (
    DEFAULT_DIALOGUE_EXPANSION,
    ContentBudget,
    LearningGoal,
    SeriesRequest,
)
from tests.series_support import AUDIENCE, FORMAT, learning_document
from tests.support import StubProvider

#: 20 blocks of 140 words: 2,800 words. At 135 wpm and 2.5× dialogue expansion, about 51.9 minutes.
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
    return {
        "title": "T",
        "block_ids": ids,
        "goals": [{"text": "Ziel", "bloom_level": "understand"}],
        **extra,
    }


def test_sample_word_counts_expand_a_read_through_and_suggest_episodes() -> None:
    expansion = DEFAULT_DIALOGUE_EXPANSION
    read_through = 2800 / 135
    supported = supportable_minutes(2800, 135, expansion)
    assert supported == pytest.approx(read_through * expansion)
    assert supported == pytest.approx(51.85, abs=0.01)
    assert source_word_budget(15, 135, expansion) == 810
    assert episode_count(supported, 15, None) == 3


def test_raising_the_episode_count_lowers_the_source_budget(tmp_path: Path) -> None:
    def message(episodes: int) -> str:
        provider = StubProvider()
        SeriesPlanNode().run(
            SeriesPlanInput(
                parsed=DOC,
                budget=_budget(51.85),
                format_spec=FORMAT,
                audience_spec=AUDIENCE,
                series_request=SeriesRequest(episodes=episodes, minutes_per_episode=15),
            ),
            _context(tmp_path, provider),
        )
        sent = provider.calls[0]
        assert sent.system is not None
        assert "room for dialogue, explanation and questions" in sent.system
        assert "grounded narration" not in sent.messages[0].content
        return sent.messages[0].content

    two = message(2)
    five = message(5)
    assert "about 810 words of source material" in two
    assert "about 560 words of source material" in five


def test_episode_goals_use_the_objectives_rule(tmp_path: Path) -> None:
    audience = AUDIENCE.model_copy(update={"desired_outcome": "Die Kipppunkte erklären können"})
    provider = StubProvider()
    SeriesPlanNode().run(
        SeriesPlanInput(
            parsed=DOC,
            budget=_budget(51.85),
            format_spec=FORMAT,
            audience_spec=audience,
            series_request=SeriesRequest(episodes=2, minutes_per_episode=15),
        ),
        _context(tmp_path, provider),
    )
    sent = provider.calls[0]
    assert sent.system is not None
    assert OBJECTIVE_FORMULATION_RULE in sent.system
    user = sent.messages[0].content
    assert desired_outcome_source("Die Kipppunkte erklären können") in user
    assert f"{time_budget_constraint(15)} per episode." in user

    plan = _plan(
        {
            "title": "S",
            "episodes": [
                _episode(
                    IDS[:10],
                    goals=[
                        {
                            "text": "Die Kipppunkte erklären",
                            "bloom_level": "analyze",
                            "derivation": "Aus dem Wunsch, Mechanismen zu erklären",
                        },
                        {"text": "  ", "bloom_level": "remember"},
                        {"text": "Nur ein Thema", "bloom_level": "not-a-level"},
                        "nur ein Satz",
                    ],
                )
            ],
        }
    )
    goal = plan.episodes[0].goals[0]
    assert [item.id for item in plan.episodes[0].goals] == ["ep01-g0"]
    assert goal.text == "Die Kipppunkte erklären"
    assert goal.bloom_level == "analyse"
    assert goal.derivation == "Aus dem Wunsch, Mechanismen zu erklären"
    assert goal.model_dump()["bloom_level"] == "analyse"
    assert all("Lernziele" not in warning for warning in plan.warnings)
    plain = LearningGoal(id="g0", text="Ziel", source="generated")
    assert "bloom_level" not in plain.model_dump()


def test_bare_string_goals_keep_the_episode_and_warn() -> None:
    # 15 minutes at 135 wpm and 2.5× is 810 words. Five blocks are 700, under that cap.
    plan = _plan(
        {
            "title": "S",
            "episodes": [
                _episode(IDS[:5], goals=["Erklären", "Anwenden"]),
                _episode(
                    IDS[5:10],
                    goals=[
                        {"text": "Erklären", "bloom_level": "not-a-level"},
                        {"text": "  ", "bloom_level": "remember"},
                    ],
                ),
                _episode(IDS[10:15], goals=[]),
                _episode(IDS[15:]),
            ],
        },
        minutes=15,
        budget=_budget(51.85),
    )
    warnings = [
        "Folge 1 hat keine verwertbaren Lernziele, bitte neu planen",
        "Folge 2 hat keine verwertbaren Lernziele, bitte neu planen",
        "Folge 3 hat keine verwertbaren Lernziele, bitte neu planen",
    ]
    assert [episode.block_ids for episode in plan.episodes] == [
        IDS[:5],
        IDS[5:10],
        IDS[10:15],
        IDS[15:],
    ]
    assert [episode.goals for episode in plan.episodes[:3]] == [[], [], []]
    assert plan.episodes[3].goals
    assert plan.warnings == warnings
    assert plan.model_dump(mode="json")["warnings"] == warnings


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


def test_an_over_budget_assignment_is_kept_and_named() -> None:
    # 15 minutes at 135 wpm and 2.5× is 810 words. Six blocks are 840, five are 700.
    plan = _plan(
        {
            "title": "S",
            "episodes": [_episode(IDS[:6]), _episode(IDS[6:11])],
        },
        minutes=15,
        budget=_budget(51.85),
    )
    assert [episode.block_ids for episode in plan.episodes] == [IDS[:6], IDS[6:11]]
    left_out = [item.block_id for item in plan.unassigned]
    assert left_out == IDS[11:]
    assert all(item.reason == LEFT_OUT_FOR_DIALOGUE for item in plan.unassigned)
    warning = "Folge 1 übersteigt das Quellbudget um 30 Wörter, mehr Folgen wählen"
    assert plan.warnings == [warning]
    # The series endpoint returns this dump as the plan in the API response.
    assert plan.model_dump(mode="json")["warnings"] == [warning]


@pytest.mark.parametrize("expansion", [0, -1])
def test_a_non_positive_dialogue_expansion_is_rejected(expansion: float) -> None:
    budget = _budget().model_copy(update={"dialogue_expansion": expansion})
    with pytest.raises(ValueError, match="greater than zero"):
        _plan(
            {"title": "S", "episodes": [_episode(IDS[:2]), _episode(IDS[2:4])]},
            budget=budget,
        )


def test_forgotten_passages_join_only_while_the_episode_has_source_room() -> None:
    # Six minutes × 135 wpm / 2.5 = 324 source words: two blocks fit, a third does not.
    plan = _plan(
        {
            "title": "S",
            "episodes": [_episode(IDS[1:2]), _episode(IDS[10:11])],
        },
        minutes=6,
    )
    first, second = plan.episodes
    assert first.block_ids == IDS[:2]
    assert second.block_ids == IDS[10:12]
    left_out = [u.block_id for u in plan.unassigned]
    assert IDS[2] in left_out and IDS[12] in left_out
    assert all(u.reason == LEFT_OUT_FOR_DIALOGUE for u in plan.unassigned)


def test_an_episode_below_three_minutes_merges_into_its_neighbour() -> None:
    plan = _plan(
        {
            "title": "S",
            "episodes": [_episode(IDS[:9]), _episode(IDS[9:19]), _episode(IDS[19:])],
        }
    )
    # One block is 140 words: 140 / 135 × 2.5 ≈ 2.6 minutes, under the floor.
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


def test_an_episode_keeps_its_length_when_its_passages_support_less() -> None:
    plan = _plan({"title": "S", "episodes": [_episode(IDS[:10]), _episode(IDS[10:])]}, minutes=40)
    for episode in plan.episodes:
        assert episode.supportable_minutes == pytest.approx(
            supportable_minutes(1400, 135, DEFAULT_DIALOGUE_EXPANSION), abs=0.01
        )
        assert episode.target_minutes == 40
    assert plan.budget.verdict == "ok"


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
    assert first.goals[0].bloom_level == "understand"
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
