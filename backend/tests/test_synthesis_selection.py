"""Single-request limits apply to exact tagged text, not production chunk sizes."""

import pytest

from app.experiments.audio_generation import SynthesisSetup, selected_script
from app.schemas.audio import AudioLine, AudioScript, VoiceCast, VoiceChoice


def setup(**kwargs):
    return SynthesisSetup(
        voice_cast=VoiceCast(voices=[VoiceChoice(speaker="Host", voice_id="voice")]),
        **kwargs,
    )


def script(*texts):
    return AudioScript(
        lines=[
            AudioLine(
                segment_id=str(i),
                beat_id=str(i),
                speaker="Host",
                text=text,
                spoken=text,
                tagged=text,
            )
            for i, text in enumerate(texts)
        ]
    )


def test_initial_contiguous_whole_utterances_across_beats():
    original = script("[curious] " + "x" * 990, "y" * 1000, "z")
    selected = selected_script(original, setup())
    assert selected.lines == original.lines[:2]
    assert selected.character_count() == 2000
    assert len(original.lines) == 3


@pytest.mark.parametrize("model", ["eleven_v3", "eleven_v4"])
def test_exact_edited_text_at_documented_limit(model):
    original = script("original", "second")
    draft = setup(edited_text=["[pause] 🌻" + " " * 1991, "not sent"], line_count=1)
    draft.voice_cast.model_id = model
    selected = selected_script(original, draft)
    assert selected.character_count() == 2000
    assert selected.lines[0].tagged == draft.edited_text[0]
    assert selected.lines[0].speaker == "Host"
    assert selected.lines[0].segment_id == "0"
    assert original.lines[0].tagged == "original"
    draft.edited_text[0] += "!"
    with pytest.raises(ValueError, match="2001.*2000"):
        selected_script(original, draft)


@pytest.mark.parametrize(
    "draft",
    [
        setup(edited_text=["x"]),
        setup(edited_text=["x"], line_count=1),
        setup(line_count=3),
        setup(edited_text=["", "second"], line_count=1),
    ],
)
def test_invalid_edit_structure_selection_and_empty_lines(draft):
    with pytest.raises(ValueError):
        selected_script(script("first", "second"), draft)


def test_speaker_and_tag_structure_remains_in_order():
    original = script("[curious] Question?", "[laughs] Answer.")
    original.lines[1].speaker = "Guest"
    draft = setup(edited_text=["[curious] Edited  question?", "[laughs] New answer!"], line_count=2)
    draft.voice_cast.voices.append(VoiceChoice(speaker="Guest", voice_id="other"))
    chosen = selected_script(original, draft)
    assert [(line.segment_id, line.speaker, line.tagged) for line in chosen.lines] == [
        ("0", "Host", "[curious] Edited  question?"),
        ("1", "Guest", "[laughs] New answer!"),
    ]


def test_provider_unique_voice_limit():
    original = script(*["Hello"] * 11)
    draft = setup(line_count=11)
    draft.voice_cast.voices = []
    for i, line in enumerate(original.lines):
        line.speaker = f"Speaker {i}"
        draft.voice_cast.voices.append(VoiceChoice(speaker=line.speaker, voice_id=f"v{i}"))
    with pytest.raises(ValueError, match="10 unique"):
        selected_script(original, draft)


def test_no_arbitrary_splitting_of_first_utterance_or_tag():
    with pytest.raises(ValueError, match="shorten the first"):
        selected_script(script("[curious] " + "x" * 2000), setup())


def test_unknown_limits_are_not_claimed():
    draft = setup()
    draft.voice_cast.model_id = "unknown"
    with pytest.raises(ValueError, match="No documented"):
        selected_script(script("short"), draft)
