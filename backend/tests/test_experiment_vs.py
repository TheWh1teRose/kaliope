"""Verbalized Sampling experiment: prompt rendering, schemas, parsing, checks, cost."""

from __future__ import annotations

import json

import pytest

from app.experiments.verbalized_sampling.checks import check_citations, parse_passages
from app.experiments.verbalized_sampling.cost import (
    BASELINE_NODE,
    VS_NODE,
    estimate,
    summarize,
)
from app.experiments.verbalized_sampling.options import VSOptions
from app.experiments.verbalized_sampling.parse import VSParseError, parse_completion, parse_payload
from app.experiments.verbalized_sampling.prompts import (
    BASELINE_SUFFIX,
    DEFAULT_BASE_PROMPT,
    DEFAULT_VS_INSTRUCTION,
    PAPER_VS_INSTRUCTION,
    PROBABILITY_DEFINITION,
    baseline_system_prompt,
    render_vs_instruction,
    vs_system_prompt,
)
from app.experiments.verbalized_sampling.schema import baseline_schema, vs_schema
from app.llm import registry
from app.llm.base import Completion, Usage
from app.pipeline.nodes.script import _SCHEMA as SCRIPT_SCHEMA
from app.pipeline.nodes.script import _SYSTEM as SCRIPT_SYSTEM

SPEAKERS = ["Moderator", "Expertin"]
QUOTE = "Enzyme sind Biokatalysatoren, die die Aktivierungsenergie herabsetzen."


def _segments(n: int = 2) -> list[dict[str, object]]:
    out: list[dict[str, object]] = [
        {"speaker": "moderator", "text": f"Frage {n}?", "kind": "pedagogy", "citations": []},
        {
            "speaker": "Expertin",
            "text": f"Antwort {n}.",
            "kind": "claim",
            "citations": [{"block_id": "b-1", "quote": QUOTE}],
        },
    ]
    return out


def _answer(probabilities: list[object]) -> dict[str, object]:
    return {
        "responses": [
            {"segments": _segments(i), "probability": p} for i, p in enumerate(probabilities)
        ]
    }


def _completion(text: str, stop_reason: str) -> Completion:
    return Completion(text=text, model_id="m", usage=Usage(), latency_ms=1, stop_reason=stop_reason)


# ------------------------------------------------------------------- prompts


def test_default_base_is_production_prompt_without_its_closing_line() -> None:
    assert SCRIPT_SYSTEM.rstrip().endswith(BASELINE_SUFFIX)
    assert not DEFAULT_BASE_PROMPT.endswith(BASELINE_SUFFIX)
    assert SCRIPT_SYSTEM.startswith(DEFAULT_BASE_PROMPT)


def test_baseline_prompt_is_production_prompt_and_never_has_the_vs_block() -> None:
    assert baseline_system_prompt(DEFAULT_BASE_PROMPT) == SCRIPT_SYSTEM.rstrip()
    # A base that still carries the closing line is not doubled.
    assert baseline_system_prompt(SCRIPT_SYSTEM).count(BASELINE_SUFFIX) == 1
    assert "Verbalized sampling" not in baseline_system_prompt(DEFAULT_BASE_PROMPT)


def test_render_fills_k_and_the_probability_definition() -> None:
    text, warnings = render_vs_instruction(DEFAULT_VS_INSTRUCTION, VSOptions(k=4))
    assert warnings == []
    assert "Generate 4 different versions" in text
    assert PROBABILITY_DEFINITION in text
    assert "{{" not in text


def test_missing_k_placeholder_warns_and_keeps_unknown_placeholders() -> None:
    text, warnings = render_vs_instruction("Write some versions. {{own}}", VSOptions())
    assert len(warnings) == 1 and "{{k}}" in warnings[0]
    assert "{{own}}" in text


def test_paper_wording_uses_the_same_probability_definition() -> None:
    text, warnings = render_vs_instruction(PAPER_VS_INSTRUCTION, VSOptions(k=5))
    assert warnings == []
    assert text.startswith("Generate 5 responses to the input prompt.")
    assert PROBABILITY_DEFINITION in text


def test_vs_system_prompt_strips_the_json_suffix_before_the_vs_block() -> None:
    rendered, _ = render_vs_instruction(DEFAULT_VS_INSTRUCTION, VSOptions())
    system = vs_system_prompt(DEFAULT_BASE_PROMPT, rendered)
    assert system.startswith(DEFAULT_BASE_PROMPT)
    assert system.endswith("with no explanations or extra text.")
    # The production prompt already ends with the one-object closing line.
    from_production = vs_system_prompt(SCRIPT_SYSTEM, rendered)
    assert BASELINE_SUFFIX not in from_production
    assert from_production.startswith(DEFAULT_BASE_PROMPT)
    assert rendered.strip() in from_production


# -------------------------------------------------------------------- schema


def test_vs_schema_reuses_the_script_segments_unchanged() -> None:
    schema = vs_schema()
    item = schema["properties"]["responses"]["items"]
    assert item["properties"]["segments"] == SCRIPT_SCHEMA["properties"]["segments"]
    assert list(item["properties"]) == ["segments", "probability"]
    assert item["required"] == ["segments", "probability"]
    assert schema["required"] == ["responses"]
    assert list(schema["properties"]) == ["responses"]
    assert baseline_schema() == SCRIPT_SCHEMA
    assert baseline_schema() is not SCRIPT_SCHEMA
    # No range or length constraints: providers reject most of them.
    assert "minimum" not in json.dumps(schema)
    assert "minItems" not in json.dumps(schema)


# --------------------------------------------------------------------- parse


def test_parse_well_formed_answer() -> None:
    parsed = parse_payload(_answer([0.4, 0.3, 0.2, 0.06, 0.04]), VSOptions(), speakers=SPEAKERS)
    assert [c.probability for c in parsed.candidates] == [0.4, 0.3, 0.2, 0.06, 0.04]
    assert parsed.warnings == []
    first = parsed.candidates[0]
    assert first.segments[0].speaker == "Moderator"  # snapped onto the declared speaker
    assert first.segments[1].kind == "claim"
    assert first.segments[1].citations[0].block_id == "b-1"
    assert first.words == 4


def test_parse_fenced_completion_and_truncation() -> None:
    body = json.dumps(_answer([0.5, 0.3]))
    fenced = _completion(f"```json\n{body}\n```", "end")
    parsed = parse_completion(fenced, VSOptions(k=2))
    assert len(parsed.candidates) == 2 and parsed.warnings == []

    # Cut off inside the third version: the two complete ones survive.
    cut = json.dumps(_answer([0.5, 0.3, 0.2]))
    cut = cut[: cut.rindex('"probability": 0.2') - 30]
    parsed = parse_completion(_completion(cut, "max_tokens"), VSOptions(k=3))
    assert len(parsed.candidates) == 2
    assert any("unvollständige" in w for w in parsed.warnings)
    assert any("2 Fassungen statt 3" in w for w in parsed.warnings)

    # Cut off after finished objects: those versions are complete and stay.
    finished = json.dumps(_answer([0.5, 0.3, 0.2]))
    for tail in (",", ", {"):
        parsed = parse_completion(_completion(finished[:-2] + tail, "max_tokens"), VSOptions(k=3))
        assert [c.probability for c in parsed.candidates] == [0.5, 0.3, 0.2]
        assert parsed.warnings == []

    # A finished JSON body that still stops at max_tokens keeps every version.
    parsed = parse_completion(_completion(finished, "max_tokens"), VSOptions(k=3))
    assert len(parsed.candidates) == 3
    assert parsed.warnings == []

    # Cut off inside the first version: nothing complete, and the error says why.
    first = json.dumps(_answer([0.5]))
    first = first[: first.index("Antwort 0")]
    with pytest.raises(VSParseError, match="abgeschnitten"):
        parse_completion(_completion(first, "max_tokens"), VSOptions(k=3))


def test_percentages_strings_and_missing_probabilities() -> None:
    parsed = parse_payload(_answer(["35%", 40, 25]), VSOptions(k=3))
    assert [c.probability for c in parsed.candidates] == [0.35, 0.4, 0.25]
    assert any("Prozent" in w for w in parsed.warnings)

    parsed = parse_payload(_answer([35, 40, 25]), VSOptions(k=3))
    assert [c.probability for c in parsed.candidates] == [0.35, 0.4, 0.25]

    # A percent token scales only itself; a 0–1 neighbour stays.
    parsed = parse_payload(_answer(["60%", 0.4]), VSOptions(k=2))
    assert [c.probability for c in parsed.candidates] == [0.6, 0.4]
    assert any("Prozent" in w for w in parsed.warnings)

    # One value just above 1 is a miss, not a reason to rescale the batch.
    parsed = parse_payload(_answer([0.6, 1.05]), VSOptions(k=2))
    assert [c.probability for c in parsed.candidates] == [0.6, 1.0]
    assert any("begrenzt" in w for w in parsed.warnings)
    assert not any("Prozent" in w for w in parsed.warnings)

    parsed = parse_payload(_answer(["0,3", None, "viel"]), VSOptions(k=3))
    assert [c.probability for c in parsed.candidates] == [0.3, None, None]
    assert any("2 Fassung(en) ohne Wahrscheinlichkeit" in w for w in parsed.warnings)


def test_sum_equal_and_clamp_warnings() -> None:
    parsed = parse_payload(_answer([0.9, 0.8]), VSOptions(k=2))
    assert any("Summe der Wahrscheinlichkeiten 1,70" in w for w in parsed.warnings)
    parsed = parse_payload(_answer([0.2, 0.2, 0.2]), VSOptions(k=3))
    assert any("dieselbe Wahrscheinlichkeit" in w for w in parsed.warnings)
    parsed = parse_payload(_answer([-0.1, 0.3]), VSOptions(k=2))
    assert parsed.candidates[0].probability == 0.0
    assert any("begrenzt" in w for w in parsed.warnings)


def test_duplicates_are_flagged() -> None:
    answer = {"responses": [{"segments": _segments(1), "probability": 0.5}] * 2}
    parsed = parse_payload(answer, VSOptions(k=2))
    assert parsed.candidates[1].flags == ["doppelt"]


def test_tolerated_shapes() -> None:
    bare = [
        {"segments": _segments(1), "probability": 0.5},
        {"text": "Nur Text.", "probability": 0.5},
    ]
    parsed = parse_payload(bare, VSOptions(k=2))
    assert len(parsed.candidates) == 2
    assert parsed.candidates[1].segments[0].speaker == "?"
    assert any("bloße Liste" in w for w in parsed.warnings)
    assert any("bloßer Text" in w for w in parsed.warnings)

    parsed = parse_payload({"versions": [{"segments": _segments(1)}]}, VSOptions(k=1 + 1))
    assert any('"versions"' in w for w in parsed.warnings)


def test_nothing_usable_raises() -> None:
    with pytest.raises(VSParseError):
        parse_payload({"responses": [{"segments": [{"text": " "}]}, 3]}, VSOptions())
    with pytest.raises(VSParseError):
        parse_payload({"answer": "nope"}, VSOptions())
    bad = Completion(text="no json here", model_id="m", usage=Usage(), latency_ms=1)
    with pytest.raises(VSParseError):
        parse_completion(bad, VSOptions())


# -------------------------------------------------------------------- checks


def test_passages_from_text_headers_and_json() -> None:
    text = f"[b-1]\n{QUOTE} Mehr.\n\n[b-2]\nZweiter Block."
    assert parse_passages(text) == {"b-1": f"{QUOTE} Mehr.", "b-2": "Zweiter Block."}
    blocks = json.dumps([{"id": "b-1", "text": QUOTE, "page": 1}])
    assert parse_passages(blocks) == {"b-1": QUOTE}
    assert parse_passages(json.dumps({"blocks": [{"id": "x", "text": "y"}]})) == {"x": "y"}
    assert parse_passages("Kein Block hier.") == {}


def test_citation_check_counts_located_unknown_and_unlocated() -> None:
    parsed = parse_payload(_answer([0.5]), VSOptions(k=1 + 1))
    segments = parsed.candidates[0].segments
    passages = {"b-1": f"Vorher. {QUOTE} Nachher."}
    assert check_citations(segments, passages).ok

    segments[1].citations.append(segments[1].citations[0].model_copy(update={"block_id": "b-9"}))
    segments[1].citations.append(
        segments[1].citations[0].model_copy(update={"quote": "Steht nirgends in diesem Block."})
    )
    check = check_citations(segments, passages)
    assert (check.cited, check.located, check.unknown_block, check.unlocated) == (3, 1, 1, 1)
    assert not check.ok


# ---------------------------------------------------------------------- cost


def test_estimate_uses_registry_prices() -> None:
    options = VSOptions(k=5)
    est = estimate(
        "claude-opus-5", input_chars=4000, word_budget=350, options=options, max_tokens=16000
    )
    assert est.vs_output_tokens == 3500
    expected_vs = registry.cost_usd("claude-opus-5", Usage(input_tokens=1000, output_tokens=3500))
    assert est.vs_usd == pytest.approx(expected_vs)
    single = registry.cost_usd("claude-opus-5", Usage(input_tokens=1000, output_tokens=700))
    assert est.baseline_usd == pytest.approx(single)
    assert not est.exceeds_max_tokens
    tight = estimate(
        "claude-opus-5", input_chars=4000, word_budget=350, options=options, max_tokens=2000
    )
    assert tight.exceeds_max_tokens


def test_summarize_splits_vs_and_baseline() -> None:
    traces = [
        {
            "node_name": VS_NODE,
            "cost_usd": 0.16,
            "tokens_in": 3000,
            "tokens_out": 4800,
            "latency_ms": 70000,
        },
        {
            "node_name": BASELINE_NODE,
            "cost_usd": 0.04,
            "tokens_in": 3000,
            "tokens_out": 700,
            "latency_ms": 18000,
        },
        {"node_name": "other", "cost_usd": 9.0},
    ]
    cost = summarize(traces, drafts=5)
    assert cost.vs_usd == pytest.approx(0.16)
    assert cost.baseline_usd == pytest.approx(0.04)
    assert cost.vs_per_draft_usd == pytest.approx(0.032)
    assert cost.ratio_to_single_baseline == 4.0
    assert (cost.tokens_in, cost.tokens_out, cost.latency_ms) == (6000, 5500, 88000)
    assert summarize(traces[:1], drafts=5).ratio_to_single_baseline is None
