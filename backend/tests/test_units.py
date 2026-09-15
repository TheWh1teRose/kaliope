"""Unit coverage for the pieces the invariants depend on.

These need no corpus, so they stay green on a fresh checkout and pin the
behaviour the corpus-parameterised tests rely on.
"""

from __future__ import annotations

import pytest

from app.ingestion.anchors import rects_for
from app.ingestion.normalize import SOFT_HYPHEN, normalize_runs, normalize_text
from app.ingestion.runs import EditLedger, TextEdit, apply_edits
from app.lang.detect import detect_language
from app.lang.readability import has_formula, score
from app.lang.resources import conjunctions_for, words_per_minute
from app.llm.base import Completion, CompletionRequest, LLMClient, Message, Usage, parse_json
from app.llm.registry import cost_usd, provider_name_for, supports_sampling
from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.pipeline.framework.keys import node_key
from app.pipeline.nodes.script import locate_quote
from app.schemas.document import Block, RunSpan, TextRun
from app.schemas.review import ReasonCode, requires_note
from app.schemas.zones import Zone, is_narratable, salience_of


def run(text: str, **kwargs: object) -> TextRun:
    width = 6.0
    return TextRun.model_validate(
        {
            "page": 0,
            "bbox": (10.0, 100.0, 10.0 + width * len(text), 112.0),
            "text": text,
            "font_size": 10.0,
            "font_name": "Test",
            "bold": False,
            "in_filled_rect": False,
            "raw_index": 0,
            "char_x": [10.0 + width * i for i in range(len(text) + 1)],
            **kwargs,
        }
    )


# --------------------------------------------------------- the edit primitive


def test_apply_edits_keeps_geometry_aligned() -> None:
    original = run("abcdef")
    edited = apply_edits(original, [TextEdit(2, 4, "")])
    assert edited.text == "abef"
    assert len(edited.char_x) == len(edited.text) + 1
    # 'e' kept the x it had before the deletion.
    assert edited.char_x[2] == original.char_x[4]


def test_apply_edits_subdivides_a_replacement() -> None:
    edited = apply_edits(run("aﬁb"), [TextEdit(1, 2, "fi")])
    assert edited.text == "afib"
    assert len(edited.char_x) == 5
    assert edited.char_x[1] < edited.char_x[2] < edited.char_x[3]


def test_apply_edits_rejects_overlaps() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        apply_edits(run("abcdef"), [TextEdit(0, 3, "x"), TextEdit(2, 4, "y")])


# ------------------------------------------------------------- normalization


def test_soft_hyphen_and_ligature_are_removed() -> None:
    result = normalize_runs([run(f"Hormon{SOFT_HYPHEN}system ﬁndet")], "de")
    text = result.runs[0].text
    assert SOFT_HYPHEN not in text
    assert "findet" in text
    assert result.runs[0].has_geometry()


def test_suspended_compound_is_not_joined() -> None:
    """§5.4.3: ``Hormon-`` + ``und`` must keep its hyphen."""
    first = run("Hormon-", page=0)
    second = run("und Nervensystem")
    second.bbox = (10.0, 120.0, 200.0, 132.0)  # next line
    result = normalize_runs([first, second], "de")
    assert result.suspended_compounds == 1
    assert result.runs[0].text.endswith("-")
    assert not result.runs[0].tight_join_next


def test_line_break_hyphen_is_joined() -> None:
    first = run("Nerven-")
    second = run("system reguliert")
    second.bbox = (10.0, 120.0, 200.0, 132.0)
    result = normalize_runs([first, second], "de")
    assert result.dehyphenated_joins == 1
    assert result.runs[0].text == "Nerven"
    assert result.runs[0].tight_join_next


def test_intra_line_hyphen_is_kept() -> None:
    """``well-known`` split across two spans on one line is one word, not two."""
    first = run("well-")
    second = run("known")
    second.bbox = (100.0, 100.0, 160.0, 112.0)  # same line
    result = normalize_runs([first, second], "en")
    assert result.dehyphenated_joins == 0
    assert result.runs[0].text == "well-"


def test_letterspacing_is_collapsed() -> None:
    result = normalize_runs([run("L E R N Z I E L E")], "de")
    assert result.runs[0].text == "LERNZIELE"
    assert result.letterspaced_runs == 1


def test_letterspacing_collapse_is_idempotent() -> None:
    once = normalize_runs([run("L E R N Z I E L E")], "de").runs
    twice = normalize_runs(once, "de").runs
    assert [r.text for r in once] == [r.text for r in twice]


def test_normalize_text_is_stable() -> None:
    assert normalize_text("„Zitat“  mit   Leerzeichen") == '"Zitat" mit Leerzeichen'


def test_ledger_totals() -> None:
    ledger = EditLedger()
    ledger.remove("a", 3)
    ledger.remove("a", 2)
    ledger.add("b", 1)
    assert ledger.total_removed == 5
    assert ledger.total_added == 1


# -------------------------------------------------------------- anchors


def test_rects_for_a_sub_range_is_narrower_than_the_block() -> None:
    text = "Der Hypothalamus steuert die Hypophyse."
    block = Block(
        id="b0",
        ordinal=0,
        text=text,
        page=0,
        bboxes=[(0, (10.0, 100.0, 250.0, 112.0))],
        char_map=[
            RunSpan(
                char_start=0,
                char_end=len(text),
                page=0,
                bbox=(10.0, 100.0, 250.0, 112.0),
                char_x=[10.0 + 6.0 * i for i in range(len(text) + 1)],
            )
        ],
    )
    whole = rects_for(block, 0, len(text))[0].bbox
    part = rects_for(block, 4, 16)[0].bbox
    assert part[0] > whole[0]
    assert part[2] < whole[2]
    assert part[1] == whole[1]


def test_rects_falls_back_to_the_block_without_a_char_map() -> None:
    block = Block(
        id="b0", ordinal=0, text="abc", page=0, bboxes=[(0, (1.0, 2.0, 3.0, 4.0))], char_map=[]
    )
    rects = rects_for(block, 0, 3)
    assert rects and rects[0].bbox == (1.0, 2.0, 3.0, 4.0)


# ------------------------------------------------------ citation locating


def test_locate_quote_finds_an_exact_match() -> None:
    text = "Der Hypothalamus steuert die Hypophyse."
    assert locate_quote(text, "steuert die Hypophyse") == (17, 38)


def test_locate_quote_tolerates_whitespace_differences() -> None:
    text = "Der Hypothalamus  steuert\ndie Hypophyse."
    span = locate_quote(text, "steuert die Hypophyse")
    assert span is not None
    start, end = span
    assert "steuert" in text[start:end]


def test_locate_quote_refuses_a_short_needle() -> None:
    assert locate_quote("Der Hypothalamus steuert.", "Der") is None


# --------------------------------------------------------------- language


def test_german_is_detected_over_english() -> None:
    german = (
        "Das Hormonsystem und das Nervensystem arbeiten eng zusammen und ergänzen "
        "einander, weil die Hormone langsamer wirken als die Nervenimpulse im Körper. "
        "Der Hypothalamus steuert die Hypophyse, die ihrerseits zahlreiche periphere "
        "Drüsen reguliert, sodass die Konzentration im Blut konstant bleibt."
    )
    detection = detect_language(german)
    assert detection.language == "de"
    assert detection.confidence > 0.3
    assert detect_language(german.replace("ä", "a").replace("ö", "o")).language == "de"


def test_short_text_is_undetectable_rather_than_guessed() -> None:
    assert detect_language("kurz").language == "und"


def test_speaking_rate_is_per_language() -> None:
    assert words_per_minute("de") == 135
    assert words_per_minute("xx") == 150  # documented default, not German


def test_conjunctions_fall_back_to_the_union() -> None:
    """An unknown language over-suppresses joins rather than fabricating words."""
    unknown = conjunctions_for("xx")
    assert "und" in unknown and "and" in unknown


def test_readability_only_scores_languages_it_knows() -> None:
    assert has_formula("de") and not has_formula("cs")
    assert score("cs", "cokoliv") is None
    assert score("de", "Das ist ein kurzer Satz. Und noch einer.") is not None


# --------------------------------------------------------------- registry


def test_cost_is_computed_from_the_pricing_table() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("claude-opus-5", usage) == pytest.approx(30.0)
    # An unknown model is usable, but its cost is recorded as zero rather than
    # invented.
    assert cost_usd("some-future-model", usage) == 0.0


def test_provider_is_inferred_for_an_unknown_model() -> None:
    assert provider_name_for("claude-something-new") == "anthropic"
    with pytest.raises(KeyError, match="cannot determine a provider"):
        provider_name_for("mystery-model")


def test_sampling_support_is_declared_per_model() -> None:
    assert supports_sampling("claude-haiku-4-5") is True
    assert supports_sampling("claude-opus-5") is False


# -------------------------------------------------------- cache and store


def test_node_key_changes_with_every_input() -> None:
    base = dict(name="select", version="1.0", config={"a": 1}, model_id="m", input_hashes=["h"])
    key = node_key(**base)  # type: ignore[arg-type]
    assert key != node_key(**{**base, "version": "1.1"})  # type: ignore[arg-type]
    assert key != node_key(**{**base, "config": {"a": 2}})  # type: ignore[arg-type]
    assert key != node_key(**{**base, "model_id": "n"})  # type: ignore[arg-type]
    assert key != node_key(**{**base, "input_hashes": ["h2"]})  # type: ignore[arg-type]
    # Input order must not matter.
    assert node_key(**{**base, "input_hashes": ["a", "b"]}) == node_key(  # type: ignore[arg-type]
        **{**base, "input_hashes": ["b", "a"]}  # type: ignore[arg-type]
    )


def test_hash_payload_is_key_order_independent() -> None:
    assert hash_payload({"a": 1, "b": 2}) == hash_payload({"b": 2, "a": 1})


def test_artifact_store_is_content_addressed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ArtifactStore(tmp_path)
    first = store.put_raw("kind", {"x": 1})
    second = store.put_raw("kind", {"x": 1})
    assert first.hash == second.hash
    assert store.get_raw(first.hash) == {"x": 1}
    assert store.kind_of(first.hash) == "kind"
    with pytest.raises(KeyError):
        store.get_raw("0" * 64)


# ------------------------------------------------------- shared taxonomies


def test_zone_taxonomy_answers_by_property_not_by_branch() -> None:
    assert is_narratable(Zone.BODY)
    assert not is_narratable(Zone.EXERCISE)
    assert salience_of(Zone.EMPHASIS_CALLOUT) > salience_of(Zone.BODY)
    assert salience_of(Zone.EXERCISE) == 0.0


def test_only_other_requires_a_note() -> None:
    assert requires_note(ReasonCode.OTHER)
    assert not any(requires_note(code) for code in ReasonCode if code is not ReasonCode.OTHER)


def test_parse_json_survives_a_code_fence() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Hier ist das Ergebnis: {"a": 1}') == {"a": 1}
    with pytest.raises(Exception, match="not valid JSON"):
        parse_json("kein JSON")


def test_parse_json_closes_a_truncated_notes_list() -> None:
    cut = (
        '{"notes":[{"target_kind":"segment","target_id":"beat000-s0003",'
        '"text":"Antwort setzt direkt mit der abstrakten Definition ein '
        "('biochemische Stoffe'). Vor die Definition ein Beispiel setzen, "
        "an dem die Zuhörer die Botenstoff-Idee erleben (z. B. Schreck: "
        "Adrenalin wird ausgeschüttet, Herz schlägt schneller – obwohl das "
        'Hormon an einer anderen'
    )
    payload = parse_json(cut)
    assert payload["notes"][0]["target_id"] == "beat000-s0003"
    assert payload["notes"][0]["text"].startswith("Antwort setzt")


def test_parse_json_drops_an_unfinished_last_item() -> None:
    cut = (
        '{"notes":[{"target_kind":"beat","target_id":"beat000","text":"Kürzer."},'
        '{"targ'
    )
    payload = parse_json(cut)
    assert payload == {
        "notes": [{"target_kind": "beat", "target_id": "beat000", "text": "Kürzer."}]
    }


def test_json_payload_names_a_max_tokens_cutoff() -> None:
    completion = Completion(
        text="kein JSON",
        model_id="claude-opus-5",
        usage=Usage(),
        latency_ms=1,
        stop_reason="max_tokens",
    )
    with pytest.raises(Exception, match="hit max_tokens"):
        completion.json_payload()


def test_llm_client_keeps_the_prompt_it_sent() -> None:
    class _Echo:
        name = "echo"

        def available(self) -> bool:
            return True

        def complete(self, request: CompletionRequest) -> Completion:
            return Completion(
                text="ok",
                model_id=request.model,
                usage=Usage(input_tokens=10, output_tokens=2),
                latency_ms=3,
            )

    client = LLMClient(resolve=lambda _model: _Echo(), cost_of=lambda _m, _u: 0.01)
    client.node_name = "select"
    client.complete(
        CompletionRequest(
            model="test-model",
            system="You choose passages.",
            messages=[Message(role="user", content="Candidate [b1]\nHello")],
        )
    )
    assert len(client.traces) == 1
    trace = client.traces[0]
    assert trace["node_name"] == "select"
    assert trace["system"] == "You choose passages."
    assert trace["messages"][0]["content"] == "Candidate [b1]\nHello"
    assert trace["response_text"] == "ok"
