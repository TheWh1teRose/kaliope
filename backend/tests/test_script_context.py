"""The script node writes each beat with the whole episode in view.

Each beat's call carries the running order, the text of every earlier beat and
how the previous beat ended. Beats are cached one by one under chained keys,
so changing a beat rewrites it and the beats after it, and nothing before it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import CompletionRequest, LLMClient
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext
from app.pipeline.nodes.script import ENDING_CHARS, ScriptInput, ScriptNode
from app.schemas.document import Block, IngestionReport, ParsedDocument, RunSpan
from app.schemas.pipeline import AudienceSpec, Beat, FormatSpec, Outline, Script, SpeakerSpec
from app.schemas.zones import Zone
from tests.support import StubProvider

PASSAGES = [
    "Chlorophyll in den Chloroplasten nimmt vor allem rotes und blaues Licht auf.",
    "Mit der Lichtenergie wird Wasser gespalten, dabei wird Sauerstoff frei.",
    "Im Calvin-Zyklus wird aus Kohlenstoffdioxid Traubenzucker aufgebaut.",
]
TITLES = ["Chlorophyll fängt Licht", "Die Lichtreaktion", "Der Calvin-Zyklus"]


class CountingStub(StubProvider):
    """The shared stub, failing once a given number of beat calls succeeded."""

    def __init__(self, fail_after: int | None = None) -> None:
        super().__init__()
        self.fail_after = fail_after

    def complete(self, request: CompletionRequest) -> Any:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("provider went away")
        return super().complete(request)


def _block(index: int, text: str) -> Block:
    box = (60.0, 100.0 + index * 30, 500.0, 120.0 + index * 30)
    return Block(
        id=f"b{index:06d}",
        ordinal=index,
        text=text,
        page=0,
        bboxes=[(0, box)],
        char_map=[RunSpan(char_start=0, char_end=len(text), page=0, bbox=box)],
        zone=Zone.BODY,
        zone_confidence=0.95,
        salience=1.0,
    )


def _input(outline: Outline | None = None) -> ScriptInput:
    words = sum(len(text.split()) for text in PASSAGES)
    parsed = ParsedDocument(
        document_id="d",
        parse_version=1,
        language="de",
        page_count=1,
        page_sizes=[(595.0, 842.0)],
        blocks=[_block(i, text) for i, text in enumerate(PASSAGES)],
        report=IngestionReport(
            language="de",
            page_count=1,
            extractable_words=words,
            narratable_words=words,
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
        ),
    )
    return ScriptInput(
        parsed=parsed,
        outline=outline or _outline(),
        format_spec=FormatSpec(
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
        ),
        audience_spec=AudienceSpec(description="Klasse 7"),
    )


def _outline(**budgets: int) -> Outline:
    return Outline(
        beats=[
            Beat(
                id=f"beat{i:03d}",
                title=title,
                block_ids=[f"b{i:06d}"],
                word_budget=budgets.get(f"beat{i:03d}", 60),
                summary=f"Worum es in {title} geht.",
            )
            for i, title in enumerate(TITLES)
        ]
    )


def _run(
    store: ArtifactStore, provider: StubProvider, inp: ScriptInput, *, force: bool = False
) -> Script:
    context = NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=store,
        config={"model": "claude-opus-5"},
        logger=logging.getLogger("test"),
        force=force,
    )
    return ScriptNode().run(inp, context)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


def test_each_beat_sees_all_beats_and_everything_written_before(store: ArtifactStore) -> None:
    provider = CountingStub()
    script = _run(store, provider, _input())

    assert len(provider.calls) == 3
    for position, call in enumerate(provider.calls):
        content = call.messages[0].content
        for title in TITLES:
            assert title in content
        assert f"▶ You are writing beat {position + 1} of 3: {TITLES[position]}" in content

        earlier = [s for s in script.segments if int(s.beat_id[-3:]) < position]
        for segment in earlier:
            assert f"{segment.speaker}: {segment.text}" in content
        if earlier:
            last = earlier[-1]
            ending = content.split("The previous beat ended with:\n", 1)[1].split("\n", 1)[0]
            assert ending.startswith(f"{last.speaker}: ")
            quoted = ending.removeprefix(f"{last.speaker}: ").lstrip("…")
            assert last.text.endswith(quoted)
            assert len(quoted) == min(len(last.text), ENDING_CHARS)
            history = content.split("Written so far", 1)[1].split("▶ You are writing", 1)[0]
            assert "[b0" not in history
        else:
            assert "Written so far" not in content
            assert "The previous beat ended with" not in content

        later = [s for s in script.segments if int(s.beat_id[-3:]) >= position]
        for segment in later:
            assert segment.text not in content

    first, middle, last_call = (call.messages[0].content for call in provider.calls)
    assert "Opening guidance: Name the topic." in first
    assert "Closing guidance" not in first
    assert "Next comes beat 2: Die Lichtreaktion" in first
    assert "Next comes beat 3: Der Calvin-Zyklus" in middle
    assert "Closing guidance: Recap the through-line." in last_call
    assert "Next comes" not in last_call
    assert "Pick up where the previous beat ended" in provider.calls[0].system


def test_the_stable_prefix_only_grows_from_beat_to_beat(store: ArtifactStore) -> None:
    provider = CountingStub()
    _run(store, provider, _input())

    messages = [call.messages[0] for call in provider.calls]
    for message in messages:
        assert message.cache_breaks == sorted(message.cache_breaks)
        assert message.content[message.cache_breaks[-1] :].startswith("▶ You are writing")
    for before, after in zip(messages, messages[1:], strict=False):
        stable = before.content[: before.cache_breaks[-1]]
        assert after.content.startswith(stable)
        assert after.cache_breaks[: len(before.cache_breaks)] == before.cache_breaks
        assert len(after.cache_breaks) == len(before.cache_breaks) + 1


def test_changing_a_beat_rewrites_it_and_the_later_beats_only(store: ArtifactStore) -> None:
    original = _run(store, CountingStub(), _input())

    unchanged = CountingStub()
    assert _run(store, unchanged, _input()) == original
    assert unchanged.calls == []

    middle = CountingStub()
    revised = _run(store, middle, _input(_outline(beat001=90)))
    assert [call.messages[0].content.count("▶") for call in middle.calls] == [1, 1]
    assert "beat 2 of 3" in middle.calls[0].messages[0].content
    assert "beat 3 of 3" in middle.calls[1].messages[0].content
    kept = [s for s in revised.segments if s.beat_id == "beat000"]
    assert kept == [s for s in original.segments if s.beat_id == "beat000"]

    last = CountingStub()
    _run(store, last, _input(_outline(beat002=90)))
    assert len(last.calls) == 1
    assert "beat 3 of 3" in last.calls[0].messages[0].content

    forced = CountingStub()
    _run(store, forced, _input(), force=True)
    assert len(forced.calls) == 3


def test_a_failed_run_resumes_at_the_beat_that_failed(store: ArtifactStore) -> None:
    with pytest.raises(RuntimeError, match="provider went away"):
        _run(store, CountingStub(fail_after=2), _input())

    resumed = CountingStub()
    script = _run(store, resumed, _input())
    assert len(resumed.calls) == 1
    assert "beat 3 of 3" in resumed.calls[0].messages[0].content
    assert {s.beat_id for s in script.segments} == {"beat000", "beat001", "beat002"}
    assert script == _run(ArtifactStore(store.root.parent / "fresh"), CountingStub(), _input())
