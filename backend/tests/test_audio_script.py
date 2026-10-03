"""The audio tagging step: the word guard, the node and its cache.

The guard is the promise this step makes: a tagged line says exactly what the
script says, up to the spoken forms the model declared. The node may retry a
refused line once and otherwise speaks it untagged; it never fails a run over a
tag. Beats are cached one by one, so an edit re-tags only its beat.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import Completion, CompletionRequest, LLMClient, Usage
from app.pipeline.audio_tags import (
    guard,
    parse_lines,
    prompt_fields,
    render_message,
    strip_tags,
    words,
)
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import discover_flows, flow_directory, flow_purpose
from app.pipeline.nodes.audio_script import AudioScriptInput, AudioScriptNode
from app.pipeline.validation import validate_flow
from app.schemas.audio import AudioScript, SpokenForm
from app.schemas.pipeline import FormatSpec, Script, Segment, SpeakerSpec
from tests.support import StubProvider

FORMAT = FormatSpec(
    id="two",
    name="Zwei",
    speakers=[
        SpeakerSpec(id="mod", name="Moderator", role="host", voice_note="Neugierig."),
        SpeakerSpec(id="exp", name="Expertin", role="expert", voice_note="Ruhig."),
    ],
    register="formal",
    target_minutes=5,
)

LINES = [
    ("beat000", "Moderator", "pedagogy", "Wovon lebt eigentlich eine Pflanze?"),
    ("beat000", "Expertin", "claim", "Von Licht, Wasser und CO₂."),
    ("beat001", "Moderator", "pedagogy", "Und wie viel Licht wird zu Zucker?"),
    ("beat001", "Expertin", "claim", "Rund 1 bis 2 % des Sonnenlichts."),
    ("beat002", "Moderator", "pedagogy", "Wo passiert das, z. B. im Blatt?"),
]


def _script(texts: dict[int, str] | None = None) -> Script:
    texts = texts or {}
    return Script(
        segments=[
            Segment(
                id=f"{beat}-s{index:04d}",
                speaker=speaker,
                text=texts.get(index, text),
                kind=kind,  # type: ignore[arg-type]
                beat_id=beat,
            )
            for index, (beat, speaker, kind, text) in enumerate(LINES)
        ]
    )


class ScriptedProvider:
    """Answers each call with the next function of ``answers``, given the request."""

    name = "anthropic"

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[CompletionRequest] = []

    def available(self) -> bool:
        return True

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        payload = answer(request)
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return Completion(text=body, model_id=request.model, usage=Usage(), latency_ms=1)


def _lines_of(request: CompletionRequest) -> list[tuple[str, str]]:
    """``(id, text)`` of every line the request asks for, last message only."""
    found = []
    for raw in request.messages[-1].content.splitlines():
        if raw.startswith("[") and "] " in raw and ": " in raw:
            line_id = raw[1 : raw.index("]")]
            found.append((line_id, raw.split(": ", 1)[1]))
    return found


def _echo(request: CompletionRequest) -> dict[str, Any]:
    return {
        "lines": [
            {"id": line_id, "tagged": f"[curious] {text}", "spoken_forms": []}
            for line_id, text in _lines_of(request)
        ]
    }


def _rephrase(request: CompletionRequest) -> dict[str, Any]:
    return {
        "lines": [
            {"id": line_id, "tagged": text.replace("Licht", "Sonne"), "spoken_forms": []}
            for line_id, text in _lines_of(request)
        ]
    }


def _run(
    store: ArtifactStore,
    provider: Any,
    script: Script,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> AudioScript:
    context = NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=store,
        config={"model": "claude-sonnet-5-5", **(config or {})},
        logger=logging.getLogger("test"),
        force=force,
    )
    result = AudioScriptNode().run(AudioScriptInput(script=script, format_spec=FORMAT), context)
    assert isinstance(result, AudioScript)
    return result


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


# --------------------------------------------------------------------- guard


def test_tags_alone_pass_and_are_listed() -> None:
    checked = guard(
        "Wovon lebt eine Pflanze?",
        "[curious] Wovon lebt eine Pflanze? [short pause]",
        [],
    )
    assert checked.ok, checked.problem
    assert checked.tags == ["[curious]", "[short pause]"]
    assert strip_tags("[curious] Wovon lebt  [sighs] sie?") == "Wovon lebt sie?"


def test_declared_spoken_forms_pass_and_give_the_spoken_text() -> None:
    forms = [
        SpokenForm(original="1 bis 2 %", spoken="eins bis zwei Prozent"),
        SpokenForm(original="CO₂", spoken="C O zwei"),
    ]
    checked = guard(
        "Aus CO₂ werden rund 1 bis 2 % Zucker.",
        "[thoughtful] Aus C O zwei werden rund eins bis zwei Prozent Zucker.",
        forms,
    )
    assert checked.ok, checked.problem
    assert checked.spoken == "Aus C O zwei werden rund eins bis zwei Prozent Zucker."


def test_case_and_punctuation_may_change_for_delivery() -> None:
    checked = guard(
        "Ist es auch. Aber es reicht.",
        "[chuckles] Ist es auch… Aber es REICHT!",
        [],
    )
    assert checked.ok, checked.problem
    assert words("Ist es auch… Aber") == ["ist", "es", "auch", "aber"]


@pytest.mark.parametrize(
    ("tagged", "forms", "problem"),
    [
        ("In den kleinen grünen Körnchen.", [], "words added: 'kleinen'"),
        ("In den Körnchen.", [], "words missing: 'grünen'"),
        ("In den roten Körnchen.", [], "words changed: 'grünen' became 'roten'"),
        ("Rund eins Prozent in den grünen Körnchen.", [], "words added"),
        ("[warmly]In den grünen Körnchen.", [], "touches a word"),
        ("[a] In [b] den [c] grünen [d] Körnchen.", [], "4 tags"),
        ("In den [grünen Körnchen.", [], "bracket"),
        (
            "In den grünen Körnchen.",
            [SpokenForm(original="z. B.", spoken="zum Beispiel")],
            "not in the line",
        ),
    ],
)
def test_the_guard_refuses_any_other_change(
    tagged: str, forms: list[SpokenForm], problem: str
) -> None:
    checked = guard("In den grünen Körnchen.", tagged, forms)
    assert not checked.ok
    assert checked.problem is not None and problem in checked.problem


def test_typed_lines_get_ids_and_keep_given_ones() -> None:
    lines, problems = parse_lines(
        "Moderator: Hallo, wie geht's?\n\n[s9] Expertin (claim): Es ist 12:30 Uhr.\nohne Sprecher\n"
        "[s9] Moderator: doppelt"
    )
    assert [(line.id, line.speaker, line.kind) for line in lines] == [
        ("l001", "Moderator", None),
        ("s9", "Expertin", "claim"),
    ]
    assert lines[1].text == "Es ist 12:30 Uhr."
    assert problems == ["line 4 has no 'Speaker: text' form", "line 5 repeats the id 's9'"]


# ---------------------------------------------------------------------- node


def test_one_call_per_beat_tags_every_line_and_keeps_the_words(store: ArtifactStore) -> None:
    provider = StubProvider()
    result = _run(store, provider, _script())

    assert len(provider.calls) == 3
    assert [line.segment_id for line in result.lines] == [s.id for s in _script().segments]
    assert all(line.guard == "pass" for line in result.lines)
    assert result.lines[0].tagged.startswith("[thoughtful] ")
    for line in result.lines:
        assert words(line.tagged) == words(line.text)

    first, second = (call.messages[0].content for call in provider.calls[:2])
    assert "- Moderator (host): Neugierig." in first
    assert "[beat000-s0001] Expertin (claim): Von Licht, Wasser und CO₂." in first
    assert "beat001" not in first
    assert "The line just before these" not in first
    assert "Expertin: Von Licht, Wasser und CO₂." in second.split("Lines, one per entry")[0]


def test_unchanged_beats_are_reused_and_an_edit_retags_only_its_beat(
    store: ArtifactStore,
) -> None:
    provider = StubProvider()
    _run(store, provider, _script())
    assert len(provider.calls) == 3

    _run(store, provider, _script())
    assert len(provider.calls) == 3, "a second run with the same script makes no call"

    # Line 2 opens beat001; beat002 also changes, because its context line did not.
    _run(store, provider, _script({2: "Und wie viel Licht wird am Ende zu Zucker?"}))
    assert len(provider.calls) == 4

    _run(store, provider, _script(), force=True)
    assert len(provider.calls) == 7


def test_a_refused_line_is_asked_for_once_more(store: ArtifactStore) -> None:
    provider = ScriptedProvider(_rephrase, _echo, _echo, _echo)
    result = _run(store, provider, _script())

    first_beat = provider.calls[:2]
    assert first_beat[1].messages[-1].content.startswith("These lines failed the word check")
    assert "Problem: words changed: 'licht' became 'sonne'" in first_beat[1].messages[-1].content
    assert [m.role for m in first_beat[1].messages] == ["user", "assistant", "user"]
    assert all(line.guard == "pass" for line in result.lines)


def test_a_line_that_keeps_failing_is_spoken_untagged(store: ArtifactStore) -> None:
    provider = ScriptedProvider(_rephrase)
    result = _run(store, provider, _script())

    refused = [line for line in result.lines if line.guard == "fallback"]
    assert [line.segment_id for line in refused] == ["beat000-s0001", "beat001-s0002"]
    assert refused[0].tagged == refused[0].text == "Von Licht, Wasser und CO₂."
    assert refused[0].problem and "sonne" in refused[0].problem
    assert len(provider.calls) == 5, "three beats, two of them retried"


def test_retry_can_be_switched_off(store: ArtifactStore) -> None:
    provider = ScriptedProvider(_rephrase)
    result = _run(store, provider, _script(), config={"retry": False})
    assert len(provider.calls) == 3
    assert len(result.fallbacks()) == 2


def test_an_answer_that_is_not_json_falls_back_without_failing(store: ArtifactStore) -> None:
    provider = ScriptedProvider(lambda _request: "kein JSON")
    result = _run(store, provider, _script())
    assert len(result.fallbacks()) == len(LINES)
    assert all(line.problem == "no answer for this line" for line in result.lines)


def test_an_empty_script_fails_the_node(store: ArtifactStore) -> None:
    with pytest.raises(NodeError):
        _run(store, StubProvider(), Script(segments=[]))


def test_the_message_is_the_template_over_the_fields() -> None:
    lines, _ = parse_lines("Moderator: Hallo.")
    fields = prompt_fields(
        lines, speakers="- Moderator (host)", language="de", tag_language="de", max_tags=2
    )
    message = render_message(fields)
    assert "Write the audio tags in German. At most 2 tags in one line." in message
    assert message.endswith("[l001] Moderator: Hallo.")
    assert "{{" not in message


# ---------------------------------------------------------------------- flow


def test_the_audio_pipeline_is_its_own_kind_and_valid() -> None:
    flows = discover_flows(flow_directory())
    audio = flows["elevenlabs_dialog_v0"]
    assert flow_purpose(audio) == "audio"
    assert {flow_purpose(f) for k, f in flows.items() if k != "elevenlabs_dialog_v0"} <= {
        "episode",
        "series_plan",
    }
    checked = validate_flow([n.model_dump() for n in audio.nodes], audio.gates)
    assert checked.valid, checked.errors
    assert checked.warnings == []
    assert set(checked.seeds) == {"script", "format_spec", "parsed"}
