"""Episodes that build on each other: the plan's content contract, the earlier
outlines, the series-aware frame of the script, and the objective nodes in a series."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import CompletionRequest, LLMClient
from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.nodes.ai_critic import AiCriticNode, CriticInput
from app.pipeline.nodes.apply_notes import ApplyOutlineNotesInput, ApplyOutlineNotesNode
from app.pipeline.nodes.objective_outline import ObjectiveOutlineInput, ObjectiveOutlineNode
from app.pipeline.nodes.objective_select import ObjectiveSelectInput, ObjectiveSelectNode
from app.pipeline.nodes.objectives import ObjectivesInput, ObjectivesNode
from app.pipeline.nodes.outline import OutlineInput, OutlineNode
from app.pipeline.nodes.script import ScriptInput, beat_prompt_fields, series_section
from app.pipeline.nodes.series_plan import SeriesPlanInput, SeriesPlanNode, build_plan
from app.schemas.pipeline import (
    Beat,
    ContentBudget,
    EarlierOutlines,
    EpisodeBrief,
    EpisodeOutline,
    EpisodePlan,
    EpisodeSummary,
    LearningGoal,
    Note,
    Notes,
    Objective,
    Objectives,
    Outline,
    SelectedBlock,
    Selection,
    SeriesContext,
    SeriesPlan,
    SeriesRequest,
    TopicHandoff,
)
from tests.series_support import AUDIENCE, FORMAT, learning_document
from tests.support import StubProvider

DOC = learning_document()
IDS = [b.id for b in DOC.blocks]


def _budget(minutes: float = 51.85) -> ContentBudget:
    return ContentBudget(
        narratable_words=2800,
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=minutes,
        requested_minutes=5,
        target_minutes=5,
        compression_ratio=2.6,
        verdict="ok",
        explanation="",
    )


def _context(provider: StubProvider, tmp_path: Path) -> NodeContext:
    return NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=ArtifactStore(tmp_path),
        config={"model": "claude-opus-5"},
        logger=logging.getLogger("test"),
    )


def _raw(ids: list[str], **contract: Any) -> dict[str, Any]:
    return {
        "title": "T",
        "block_ids": ids,
        "goals": [{"text": "Ziel", "bloom_level": "understand"}],
        **contract,
    }


def _plan(*episodes: dict[str, Any]) -> SeriesPlan:
    return build_plan(
        {"title": "S", "through_line": "", "episodes": list(episodes)},
        parsed=DOC,
        budget=_budget(),
        minutes=5,
        count=len(episodes),
    )


def _brief(index: int = 2, *, contract: bool = True, count: int = 3) -> EpisodeBrief:
    episode = EpisodePlan(
        id=f"ep{index:02d}",
        index=index,
        title="Was sich verändert",
        role="Folgen",
        block_ids=IDS[8:12],
        recap_block_ids=[IDS[0]],
        goals=[
            LearningGoal(
                id=f"ep{index:02d}-g0",
                text="Folgen erklären",
                source="generated",
                bloom_level="understand",
                derivation="Aus dem Ergebnis.",
            )
        ],
        target_minutes=5,
        supportable_minutes=10.4,
        recap="Die Wärmedecke.",
        preview="Was wir tun können.",
    )
    if contract:
        episode.owns = ["Meeresspiegel"]
        episode.assumes = ["Treibhauseffekt"]
        episode.leaves_to = [TopicHandoff(topic="Klimaschutz", episode=3)]
    return EpisodeBrief(
        series_title="Klima",
        through_line="Vom Mechanismus zum Handeln.",
        episode_count=count,
        episode=episode,
    )


EARLIER = EarlierOutlines(
    outlines=[
        EpisodeOutline(
            index=1,
            title="Die Wärmedecke",
            beats=[("Was ist der Treibhauseffekt?", "Wärme bleibt in der Luft")],
        )
    ]
)


# ------------------------------------------------------------ the contract


def test_the_planner_asks_for_a_content_contract(tmp_path: Path) -> None:
    provider = StubProvider()
    plan = SeriesPlanNode().run(
        SeriesPlanInput(
            parsed=DOC,
            budget=_budget(),
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=SeriesRequest(episodes=3, minutes_per_episode=5),
        ),
        _context(provider, tmp_path),
    )
    request = provider.calls[-1]
    assert request.json_schema is not None
    assert request.system is not None
    episode = request.json_schema["properties"]["episodes"]["items"]
    assert {"owns", "assumes", "leaves_to"} <= set(episode["required"])
    assert "owns:" in request.system
    assert "assumes:" in request.system
    assert "leaves_to:" in request.system
    assert len(plan.episodes) == 3
    for index, episode in enumerate(plan.episodes, 1):
        assert episode.owns == [f"Thema {index}"]
        assert episode.assumes == ([f"Thema {index - 1}"] if index > 1 else [])
        assert episode.leaves_to == (
            [TopicHandoff(topic=f"Thema {index + 1}", episode=index + 1)] if index < 3 else []
        )


def test_the_contract_is_read_and_each_topic_has_one_home() -> None:
    plan = _plan(
        _raw(
            IDS[:7],
            owns=["Wetter", "Klima"],
            assumes=["Vorwissen"],
            leaves_to=[{"topic": "Treibhauseffekt", "episode": 2}],
        ),
        _raw(
            IDS[7:14],
            owns=["Treibhauseffekt", "klima"],
            assumes=["Wetter"],
            leaves_to=[
                {"topic": "Anpassung", "episode": 3},
                {"topic": "Wetter", "episode": 3},
                {"topic": "Wetter", "episode": 1},
            ],
        ),
        _raw(IDS[14:], owns=["Klimaschutz"], assumes=["Treibhauseffekt", "Anpassung"]),
    )
    first, second, third = plan.episodes
    assert first.owns == ["Wetter", "Klima"]
    # The first episode takes nothing as known.
    assert first.assumes == []
    assert first.leaves_to == [TopicHandoff(topic="Treibhauseffekt", episode=2)]
    # "klima" is owned by episode 1 already; episode 2 builds on it instead.
    assert second.owns == ["Treibhauseffekt"]
    assert second.assumes == ["Wetter", "klima"]
    # Nobody owned "Anpassung", so the named later episode does; "Wetter" is
    # episode 1's and cannot be left to anyone.
    assert second.leaves_to == [TopicHandoff(topic="Anpassung", episode=3)]
    assert third.owns == ["Klimaschutz", "Anpassung"]
    # An episode cannot take as known what it owns itself.
    assert third.assumes == ["Treibhauseffekt"]


def test_a_merge_moves_the_contract_with_the_passages() -> None:
    # One block is 140 words, about 2.6 minutes: the middle episode merges.
    plan = _plan(
        _raw(IDS[:9], owns=["A"], leaves_to=[{"topic": "B", "episode": 2}]),
        _raw(IDS[9:10], owns=["B"], leaves_to=[{"topic": "C", "episode": 3}]),
        _raw(IDS[10:], owns=["C"], assumes=["A", "B"]),
    )
    assert len(plan.episodes) == 2
    first, second = plan.episodes
    assert first.owns == ["A", "B"]
    assert first.leaves_to == [TopicHandoff(topic="C", episode=2)]
    assert second.owns == ["C"] and second.assumes == ["A", "B"]


def test_assumptions_require_an_owner_in_an_earlier_surviving_episode() -> None:
    plan = _plan(
        _raw(IDS[:10], owns=["A"]),
        _raw(["missing"], owns=["X"]),
        _raw(IDS[10:], owns=["C"], assumes=["A", "X", "unknown", "C"]),
    )
    assert len(plan.episodes) == 2
    assert plan.episodes[1].assumes == ["A"]


def test_a_plan_without_a_contract_loads_and_dumps_as_before() -> None:
    plan = _plan(_raw(IDS[:10]), _raw(IDS[10:]))
    episode = plan.episodes[1]
    assert not episode.has_contract()
    dumped = episode.model_dump(mode="json")
    assert not {"owns", "assumes", "leaves_to"} & set(dumped)

    # A stored plan from before the contract existed keeps its brief's hash.
    stored = plan.model_dump(mode="json")
    reloaded = SeriesPlan.model_validate(stored)
    assert reloaded.model_dump(mode="json") == stored
    brief = EpisodeBrief.from_plan(reloaded, 2)
    assert brief.episode.owns == [] and brief.episode.leaves_to == []
    assert hash_payload(brief.model_dump(mode="json")) == hash_payload(
        EpisodeBrief.from_plan(plan, 2).model_dump(mode="json")
    )


def test_a_contract_survives_the_round_trip() -> None:
    brief = _brief()
    again = EpisodeBrief.model_validate(brief.model_dump(mode="json"))
    assert again == brief


# ------------------------------------------------------- outline prompts


def _outline_message(
    provider: StubProvider, tmp_path: Path, brief: EpisodeBrief | None, earlier: Any = None
) -> str:
    selection = Selection(
        learning_goals=[LearningGoal(id="g0", text="Folgen erklären", source="generated")],
        selected_blocks=[SelectedBlock(block_id=b) for b in IDS[8:12]],
        rationale="",
    )
    OutlineNode().run(
        OutlineInput(
            parsed=DOC,
            selection=selection,
            budget=_budget(),
            format_spec=FORMAT,
            episode_brief=brief,
            earlier_outlines=earlier,
        ),
        _context(provider, tmp_path),
    )
    return provider.calls[-1].messages[-1].content


def test_the_outline_gets_the_contract_and_the_earlier_outlines(tmp_path: Path) -> None:
    message = _outline_message(StubProvider(), tmp_path, _brief(), EARLIER)
    assert "This episode owns these topics and is the one that explains them: Meeresspiegel." in (
        message
    )
    assert "do not teach them again: Treibhauseffekt." in message
    assert "Klimaschutz (episode 3)" in message
    assert "The earlier episodes of the series already ran in this order." in message
    assert "Episode 1: Die Wärmedecke" in message
    assert "  1. Was ist der Treibhauseffekt?: Wärme bleibt in der Luft" in message
    # The series block comes before the goals and passages.
    assert message.index("Episode 1: Die Wärmedecke") < message.index("Learning goals:")


def test_an_old_brief_without_earlier_outlines_sends_the_old_prompt(tmp_path: Path) -> None:
    message = _outline_message(StubProvider(), tmp_path, _brief(contract=False))
    assert "owns these topics" not in message
    assert "already ran in this order" not in message
    assert "This is episode 2 of 3" in message
    single = _outline_message(StubProvider(), tmp_path, None)
    assert "This is episode" not in single


# ---------------------------------------------------------- script frame


def _script_input(index: int, count: int = 3) -> ScriptInput:
    outline = Outline(
        beats=[
            Beat(id="beat000", title="Anfang", block_ids=[IDS[0]], word_budget=100),
            Beat(id="beat001", title="Mitte", block_ids=[IDS[1]], word_budget=100),
            Beat(id="beat002", title="Ende", block_ids=[IDS[2]], word_budget=100),
        ]
    )
    context = SeriesContext(
        series_title="Klima",
        through_line="Vom Mechanismus zum Handeln.",
        episode_index=index,
        episodes=[
            EpisodeSummary(
                index=i,
                title=f"Folge {i}",
                owns=[f"Thema {i}"],
                assumes=[f"Thema {i - 1}"] if i > 1 else [],
                leaves_to=[TopicHandoff(topic=f"Thema {i + 1}", episode=i + 1)]
                if i < count
                else [],
            )
            for i in range(1, count + 1)
        ],
        goals=["Folgen erklären", "Folgen einordnen"],
        recap="Die Wärmedecke." if index > 1 else None,
        preview="Was wir tun können." if index < count else None,
    )
    return ScriptInput(
        parsed=DOC,
        outline=outline,
        format_spec=FORMAT,
        audience_spec=AUDIENCE,
        series_context=context,
    )


def _frame(inp: ScriptInput, position: int) -> str:
    blocks = [DOC.blocks[position]]
    return beat_prompt_fields(inp, position, blocks, [])["opening_closing"]


def test_only_the_first_episode_introduces_the_subject() -> None:
    first = _frame(_script_input(1), 0)
    assert "It opens the series: introduce its subject." in first
    assert "Opening guidance: Name the topic." in first
    for index in (2, 3):
        opening = _frame(_script_input(index), 0)
        assert "Open with a short recap of the earlier episodes: Die Wärmedecke." in opening
        assert "Do not introduce the subject anew" in opening
        assert "Name the topic." not in opening


def test_middle_episodes_close_on_their_goals_and_the_preview() -> None:
    for index in (1, 2):
        closing = _frame(_script_input(index), 2)
        assert "Close this episode only" in closing
        assert "(Folgen erklären; Folgen einordnen)" in closing
        assert "point ahead to the next episode: Was wir tun können." in closing
        assert "Do not recap the whole series" in closing
        assert "Recap the through-line." not in closing


def test_only_the_last_episode_recaps_the_whole_through_line() -> None:
    closing = _frame(_script_input(3), 2)
    assert "It closes the series: recap the through-line of all episodes" in closing
    assert "(Vom Mechanismus zum Handeln.)" in closing
    assert "Closing guidance: Recap the through-line." in closing
    # The beats in between get neither.
    assert _frame(_script_input(2), 1) == ""


def test_a_single_run_keeps_the_format_frame() -> None:
    inp = _script_input(1).model_copy(update={"series_context": None})
    assert _frame(inp, 0) == "\nThis is the first beat. Opening guidance: Name the topic.\n"
    assert _frame(inp, 2) == (
        "\nThis is the final beat. Closing guidance: Recap the through-line.\n"
    )


def test_the_series_section_lists_every_episodes_topics() -> None:
    section = series_section(_script_input(2).series_context)  # type: ignore[arg-type]
    assert "     explains: Thema 2" in section
    assert "     takes as known: Thema 1" in section
    assert "     leaves to later episodes: Thema 3 (episode 3)" in section
    assert "Keep to the episodes' topics above" in section
    # The old rule that fought the format's opening is gone.
    assert "The opening guidance opens this episode" not in section


def test_a_series_section_without_a_contract_has_no_topic_rule() -> None:
    context = _script_input(2).series_context
    assert context is not None
    bare = context.model_copy(
        update={
            "episodes": [EpisodeSummary(index=e.index, title=e.title) for e in context.episodes]
        }
    )
    section = series_section(bare)
    assert "explains:" not in section and "Keep to the episodes' topics" not in section


# --------------------------------------------------------- objective nodes


def test_objectives_in_a_series_are_the_episode_goals(tmp_path: Path) -> None:
    provider = StubProvider()
    objectives = ObjectivesNode().run(
        ObjectivesInput(
            parsed=DOC, audience_spec=AUDIENCE, budget=_budget(), episode_brief=_brief()
        ),
        _context(provider, tmp_path),
    )
    assert provider.calls == [], "the plan already formulated the goals"
    assert [(o.id, o.text, o.bloom_level) for o in objectives.items] == [
        ("ep02-g0", "Folgen erklären", "understand")
    ]


def test_objectives_refuse_an_episode_without_goals(tmp_path: Path) -> None:
    brief = _brief()
    brief.episode.goals = []
    with pytest.raises(NodeError, match="keine verwertbaren Lernziele"):
        ObjectivesNode().run(
            ObjectivesInput(
                parsed=DOC, audience_spec=AUDIENCE, budget=_budget(), episode_brief=brief
            ),
            _context(StubProvider(), tmp_path),
        )


OBJECTIVES = Objectives(
    items=[Objective(id="ep02-g0", text="Folgen erklären", bloom_level="understand")],
    desired_outcome="",
    rationale="",
)


def test_objective_select_sees_only_the_episode(tmp_path: Path) -> None:
    provider = StubProvider()
    selection = ObjectiveSelectNode().run(
        ObjectiveSelectInput(
            parsed=DOC,
            budget=_budget(),
            audience_spec=AUDIENCE,
            objectives=OBJECTIVES,
            episode_brief=_brief(),
        ),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[-1].content
    offered = {line.split("]")[0][1:] for line in message.splitlines() if line.startswith("[b")}
    assert offered == {*IDS[8:12], IDS[0]}
    assert f"[{IDS[0]}] (weight 1.0) section: Wetter und Klima (recap from an earlier episode)" in (
        message
    )
    assert "This is episode 2 of 3" in message and "Meeresspiegel" in message
    assert set(selection.block_ids()) <= {*IDS[8:12], IDS[0]}


def test_objective_select_alone_still_sees_the_whole_document(tmp_path: Path) -> None:
    provider = StubProvider()
    ObjectiveSelectNode().run(
        ObjectiveSelectInput(
            parsed=DOC, budget=_budget(), audience_spec=AUDIENCE, objectives=OBJECTIVES
        ),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[-1].content
    assert len([line for line in message.splitlines() if line.startswith("[b")]) == len(IDS)
    assert "This is episode" not in message


def test_objective_outline_continues_from_the_earlier_outlines(tmp_path: Path) -> None:
    provider = StubProvider()
    selection = Selection(
        learning_goals=[],
        selected_blocks=[SelectedBlock(block_id=b, goal_ids=["ep02-g0"]) for b in IDS[8:12]],
        rationale="",
    )
    ObjectiveOutlineNode().run(
        ObjectiveOutlineInput(
            parsed=DOC,
            selection=selection,
            budget=_budget(),
            format_spec=FORMAT,
            objectives=OBJECTIVES,
            episode_brief=_brief(),
            earlier_outlines=EARLIER,
        ),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[-1].content
    assert "This is episode 2 of 3" in message
    assert "The first beat opens with a short recap of the earlier episodes: Die Wärmedecke." in (
        message
    )
    assert "Meeresspiegel" in message and "Klimaschutz (episode 3)" in message
    assert "Episode 1: Die Wärmedecke" in message
    assert message.index("Episode 1: Die Wärmedecke") < message.index("Listener objectives")


# ----------------------------------------------------- critic and revisions


def test_the_critic_knows_what_the_other_episodes_cover(tmp_path: Path) -> None:
    provider = StubProvider()
    outline = Outline(beats=[Beat(id="beat000", title="A", block_ids=[IDS[8]], word_budget=9)])
    AiCriticNode().run(
        CriticInput(outline=outline, episode_brief=_brief()), _context(provider, tmp_path)
    )
    message = provider.calls[-1].messages[-1].content
    assert "Do not ask for a topic of an earlier or a later episode" in message
    assert "do not teach them again: Treibhauseffekt." in message

    AiCriticNode().run(CriticInput(outline=outline), _context(provider, tmp_path))
    assert "episode" not in provider.calls[-1].messages[-1].content


def test_an_outline_note_cannot_pull_in_another_episodes_passages(tmp_path: Path) -> None:
    provider = StubProvider()
    outline = Outline(beats=[Beat(id="beat000", title="A", block_ids=[IDS[8]], word_budget=9)])
    notes = Notes(
        items=[
            Note(
                id="n1",
                text="Mehr Beispiele",
                target={"kind": "beat", "id": "beat000"},  # type: ignore[arg-type]
                source="human",
            )
        ],
        subject="outline",
    )
    ApplyOutlineNotesNode().run(
        ApplyOutlineNotesInput(notes=notes, outline=outline, parsed=DOC, episode_brief=_brief()),
        _context(provider, tmp_path),
    )
    message = provider.calls[-1].messages[-1].content
    allowed = message.split("Passage ids you may use: ", 1)[1].split(", ")
    assert set(allowed) == {*IDS[8:12], IDS[0]}


class PassageProvider(StubProvider):
    def __init__(self, ids: list[str]) -> None:
        super().__init__()
        self.ids = ids

    def _respond(self, request: CompletionRequest) -> Any:
        beat = {
            "title": "Revised",
            "block_ids": self.ids,
            "word_budget": 100,
            "objective_id": "ep02-g0",
        }
        return {"beats": [beat], **beat}


@pytest.mark.parametrize("objective", [False, True])
@pytest.mark.parametrize("series", [False, True])
def test_outline_producers_admit_only_episode_and_recap_passages(
    tmp_path: Path, objective: bool, series: bool
) -> None:
    provider = PassageProvider([IDS[8], IDS[0], IDS[15]])
    selection = Selection(
        learning_goals=[],
        selected_blocks=[SelectedBlock(block_id=bid) for bid in provider.ids],
        rationale="",
    )
    inputs = dict(
        parsed=DOC,
        selection=selection,
        budget=_budget(),
        format_spec=FORMAT,
        episode_brief=_brief() if series else None,
    )
    if objective:
        outline = ObjectiveOutlineNode().run(
            ObjectiveOutlineInput(**inputs, objectives=OBJECTIVES), _context(provider, tmp_path)
        )
    else:
        outline = OutlineNode().run(OutlineInput(**inputs), _context(provider, tmp_path))
    assert outline.beats[0].block_ids == ([IDS[8], IDS[0]] if series else provider.ids)
    message = provider.calls[-1].messages[-1].content
    assert (f"[{IDS[15]}]" in message) is not series


@pytest.mark.parametrize("series", [False, True])
@pytest.mark.parametrize("response", ["mixed", "foreign", "empty"])
def test_outline_revisions_filter_responses_and_fallback_passages(
    tmp_path: Path, series: bool, response: str
) -> None:
    ids = {"mixed": [IDS[0], IDS[15]], "foreign": [IDS[15]], "empty": []}[response]
    provider = PassageProvider(ids)
    outline = Outline(
        beats=[Beat(id="beat000", title="A", block_ids=[IDS[8], IDS[15]], word_budget=9)]
    )
    notes = Notes(
        items=[
            Note(
                id="n1",
                text="Mehr Beispiele",
                target={"kind": "beat", "id": "beat000"},
                source="human",
            )
        ],
        subject="outline",
    )
    revised = ApplyOutlineNotesNode().run(
        ApplyOutlineNotesInput(
            notes=notes, outline=outline, parsed=DOC, episode_brief=_brief() if series else None
        ),
        _context(provider, tmp_path),
    )
    expected = [IDS[0]] if response == "mixed" else [IDS[8]]
    if not series:
        expected = ids or outline.beats[0].block_ids
    assert revised.beats[0].block_ids == expected
