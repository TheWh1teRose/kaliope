"""Gate acceptance criteria (§13.4, AC-GATE-1…4).

Each deterministic gate is driven by a synthetic script constructed to trip
exactly that gate, so a failure names one rule rather than a whole pipeline.
"""

from __future__ import annotations

import pytest

from app.pipeline.gates import ALL_GATE_IDS, GateContext, run_gates
from app.schemas.document import (
    Anchor,
    Block,
    IngestionReport,
    ParsedDocument,
    RunSpan,
)
from app.schemas.pipeline import (
    AudienceSpec,
    Beat,
    ContentBudget,
    FormatSpec,
    LearningGoal,
    Outline,
    Script,
    Segment,
    SelectedBlock,
    Selection,
    SpeakerSpec,
)
from app.schemas.zones import Zone

FORMAT = FormatSpec(
    id="f",
    name="Dialog",
    speakers=[
        SpeakerSpec(id="host", name="Moderator", role="fragt"),
        SpeakerSpec(id="expert", name="Expertin", role="erklärt"),
    ],
    register="formal",
    target_minutes=10,
)

AUDIENCE = AudienceSpec(description="Lernende")

BODY = (
    "Der Hypothalamus steuert die Hypophyse und reguliert damit zahlreiche periphere "
    "Drüsen im Körper des Menschen."
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


def make_context(
    segments: list[Segment],
    *,
    parsed: ParsedDocument | None = None,
    target_minutes: float = 10.0,
    beats: list[Beat] | None = None,
) -> GateContext:
    parsed = parsed or make_parsed()
    outline = Outline(
        beats=beats
        or [Beat(id="beat000", title="Regelkreise", block_ids=["b000000"], word_budget=100)]
    )
    selection = Selection(
        learning_goals=[LearningGoal(id="g0", text="Regelkreise erklären", source="generated")],
        selected_blocks=[SelectedBlock(block_id="b000000")],
        rationale="test",
    )
    words = sum(len(s.text.split()) for s in segments)
    budget = ContentBudget(
        narratable_words=5000,
        words_per_minute=135,
        min_compression=2.5,
        max_supportable_minutes=14.0,
        requested_minutes=int(target_minutes),
        target_minutes=target_minutes,
        compression_ratio=3.0,
        verdict="ok",
        explanation="",
    )
    assert words >= 0
    return GateContext(
        parsed=parsed,
        script=Script(segments=segments),
        outline=outline,
        selection=selection,
        budget=budget,
        format_spec=FORMAT,
    )


def claim(text: str, anchors: list[Anchor] | None = None, **kwargs: object) -> Segment:
    return Segment.model_validate(
        {
            "id": kwargs.pop("id", "s0"),
            "speaker": kwargs.pop("speaker", "Expertin"),
            "text": text,
            "kind": "claim",
            "anchors": anchors or [good_anchor()],
            "beat_id": kwargs.pop("beat_id", "beat000"),
            **kwargs,
        }
    )


def good_anchor() -> Anchor:
    return Anchor(document_id="d", parse_version=1, block_id="b000000", char_start=0, char_end=40)


def filler(words: int) -> str:
    return " ".join(["Wort"] * words)


def status_of(reports: list, gate_id: str) -> str:
    return next(r.status for r in reports if r.id == gate_id)


# --------------------------------------------------------------- AC-GATE-1


def test_ac_gate_1_all_nine_gates_execute() -> None:
    """AC-GATE-1: all nine gates execute and produce a report."""
    target_words = int(10.0 * 135)
    context = make_context([claim(filler(target_words))])
    suite = run_gates(context)

    assert [r.id for r in suite.reports] == ALL_GATE_IDS
    assert len(suite.reports) == 9
    assert all(r.status in {"pass", "warn", "fail", "skipped"} for r in suite.reports)


# --------------------------------------------------------------- AC-GATE-2


def test_g1_fails_on_a_dangling_block_id() -> None:
    dangling = Anchor(
        document_id="d", parse_version=1, block_id="does-not-exist", char_start=0, char_end=5
    )
    suite = run_gates(make_context([claim(filler(1350), [dangling])]), ["G1"])
    assert status_of(suite.reports, "G1") == "fail"
    assert "does not exist" in suite.reports[0].violations[0].message


def test_g1_fails_on_a_range_outside_the_block() -> None:
    outside = Anchor(
        document_id="d", parse_version=1, block_id="b000000", char_start=0, char_end=99999
    )
    suite = run_gates(make_context([claim(filler(1350), [outside])]), ["G1"])
    assert status_of(suite.reports, "G1") == "fail"


def test_g2_fails_on_an_uncited_claim() -> None:
    uncited = Segment(
        id="s0", speaker="Expertin", text=filler(1350), kind="claim", beat_id="beat000"
    )
    suite = run_gates(make_context([uncited]), ["G2"])
    assert status_of(suite.reports, "G2") == "fail"


def test_g2_passes_when_pedagogy_has_no_citation() -> None:
    pedagogy = Segment(
        id="s0", speaker="Moderator", text=filler(1350), kind="pedagogy", beat_id="beat000"
    )
    suite = run_gates(make_context([pedagogy]), ["G2"])
    assert status_of(suite.reports, "G2") == "pass"


def test_g3_fails_when_a_segment_quotes_exercise_text() -> None:
    exercise = (
        "Aufgabe zum Kapitel: Beschriften Sie die Abbildung und ordnen Sie jedem Hormon "
        "sein Zielorgan zu."
    )
    parsed = make_parsed(non_narratable_text=[exercise])
    segment = claim(f"{exercise} {filler(1340)}")
    suite = run_gates(make_context([segment], parsed=parsed), ["G3"])
    assert status_of(suite.reports, "G3") == "fail"


def test_g3_passes_when_nothing_is_reproduced() -> None:
    parsed = make_parsed(non_narratable_text=["Beschriften Sie die Abbildung sorgfältig bitte"])
    suite = run_gates(make_context([claim(filler(1350))], parsed=parsed), ["G3"])
    assert status_of(suite.reports, "G3") == "pass"


def test_g4_fails_outside_the_length_band() -> None:
    suite = run_gates(make_context([claim(filler(30))]), ["G4"])
    assert status_of(suite.reports, "G4") == "fail"
    assert "words" in suite.reports[0].violations[0].message


def test_g4_passes_inside_the_length_band() -> None:
    suite = run_gates(make_context([claim(filler(1350))]), ["G4"])
    assert status_of(suite.reports, "G4") == "pass"


def test_g6_fails_when_boilerplate_leaks() -> None:
    parsed = make_parsed(boilerplate=["Kursmaterial Physiologie Modul 3"])
    segment = claim(f"Kursmaterial Physiologie Modul 3 {filler(1345)}")
    suite = run_gates(make_context([segment], parsed=parsed), ["G6"])
    assert status_of(suite.reports, "G6") == "fail"


def test_g6_ignores_very_short_boilerplate() -> None:
    """A three-character string collides with ordinary prose; the gate says so."""
    parsed = make_parsed(boilerplate=["Der"])
    suite = run_gates(make_context([claim(f"Der {filler(1349)}")], parsed=parsed), ["G6"])
    assert status_of(suite.reports, "G6") == "pass"


def test_g8_fails_on_an_undeclared_speaker() -> None:
    segment = claim(filler(1350), speaker="Sprecher Drei")
    suite = run_gates(make_context([segment]), ["G8"])
    assert status_of(suite.reports, "G8") == "fail"
    assert "not declared" in suite.reports[0].violations[0].message


def test_g8_fails_on_verbatim_duplicates() -> None:
    text = filler(675)
    segments = [claim(text, id="s0"), claim(text, id="s1")]
    suite = run_gates(make_context(segments), ["G8"])
    assert status_of(suite.reports, "G8") == "fail"
    assert any("duplicates" in v.message for v in suite.reports[0].violations)


def test_g8_fails_on_an_uncovered_beat() -> None:
    beats = [
        Beat(id="beat000", title="A", block_ids=["b000000"], word_budget=100),
        Beat(id="beat001", title="B", block_ids=["b000000"], word_budget=100),
    ]
    suite = run_gates(make_context([claim(filler(1350))], beats=beats), ["G8"])
    assert status_of(suite.reports, "G8") == "fail"
    assert any("no segments" in v.message for v in suite.reports[0].violations)


# --------------------------------------------------------------- AC-GATE-3


def test_ac_gate_3_failures_do_not_prevent_review() -> None:
    """AC-GATE-3: gates inform; a failing suite still yields a reviewable report."""
    dangling = Anchor(document_id="d", parse_version=1, block_id="nope", char_start=0, char_end=5)
    suite = run_gates(make_context([claim(filler(10), [dangling], speaker="Fremd")]))
    assert suite.failed, "this script should fail several gates"
    # Nothing raised, every gate reported, and the script is untouched.
    assert len(suite.reports) == 9
    assert all(isinstance(r.violations, list) for r in suite.reports)


# --------------------------------------------------------------- AC-GATE-4


def test_ac_gate_4_g5_skips_without_objectives() -> None:
    suite = run_gates(make_context([claim(filler(1350))]), ["G5"])
    report = suite.reports[0]
    assert report.status == "skipped"
    assert report.skip_reason and "objectives" in report.skip_reason


def test_g5_warns_when_an_objective_is_unserved() -> None:
    parsed = make_parsed(
        objectives=["Die Lernenden können die Kalziumhomöostase quantitativ berechnen."]
    )
    suite = run_gates(make_context([claim(filler(1350))], parsed=parsed), ["G5"])
    assert status_of(suite.reports, "G5") == "warn"


def test_ac_gate_4_g7_skips_without_a_formula() -> None:
    parsed = make_parsed(language="cs")
    parsed.report.language = "cs"
    suite = run_gates(make_context([claim(filler(1350))], parsed=parsed), ["G7"])
    report = suite.reports[0]
    assert report.status == "skipped"
    assert report.skip_reason and "cs" in report.skip_reason


def test_g7_scores_a_language_it_knows() -> None:
    context = make_context([claim(" ".join([BODY] * 40))])
    suite = run_gates(context, ["G7"])
    assert status_of(suite.reports, "G7") in {"pass", "warn"}


def test_g0_fails_below_the_anchor_floor() -> None:
    parsed = make_parsed()
    parsed.report.anchor_integrity = 0.80
    suite = run_gates(make_context([claim(filler(1350))], parsed=parsed), ["G0"])
    assert status_of(suite.reports, "G0") == "fail"


def test_g0_warns_on_low_ingestion_confidence() -> None:
    parsed = make_parsed()
    parsed.report.ingestion_confidence = "low"
    parsed.report.confidence_reasons = ["Die Struktur konnte nicht erkannt werden."]
    suite = run_gates(make_context([claim(filler(1350))], parsed=parsed), ["G0"])
    assert status_of(suite.reports, "G0") == "warn"


def test_unknown_gate_id_is_reported_not_raised() -> None:
    suite = run_gates(make_context([claim(filler(1350))]), ["G0", "G99"])
    assert status_of(suite.reports, "G99") == "skipped"


@pytest.mark.parametrize("gate_id", ALL_GATE_IDS)
def test_every_gate_handles_an_empty_script(gate_id: str) -> None:
    """A gate must never raise, whatever it is handed."""
    suite = run_gates(make_context([]), [gate_id])
    assert suite.reports[0].id == gate_id
