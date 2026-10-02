"""Verbalized Sampling experiment: registration, pasted values, the run endpoint and collecting."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.experiments  # noqa: F401 - registers every experiment
from app.db import session_scope
from app.experiments.registry import get_experiment
from app.experiments.sources import SAMPLE_BEAT
from app.experiments.verbalized_sampling.experiment import normalise_fields, speaker_names
from app.experiments.verbalized_sampling.prompts import (
    DEFAULT_BASE_PROMPT,
    DEFAULT_VS_INSTRUCTION,
    PAPER_VS_INSTRUCTION,
)
from app.llm import registry
from app.main import create_app
from app.models import LLMCall, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from tests.support import StubProvider

PASSWORD = "vs-experiment-password-1"
EMAIL = "vs-experiments@kalliope.test"
KEY = "verbalized_sampling"


# ----------------------------------------------------------------- registry


def test_registered_with_production_defaults() -> None:
    experiment = get_experiment(KEY)
    setup: Any = experiment.defaults()
    assert setup.base_prompt == DEFAULT_BASE_PROMPT
    assert setup.vs_instruction == DEFAULT_VS_INSTRUCTION
    assert setup.settings.model == "claude-opus-5"
    assert setup.k == 5
    assert experiment.validate_setup(setup) == []
    extras = experiment.extras()
    assert extras["vs_instructions"] == {
        "kalliope": DEFAULT_VS_INSTRUCTION,
        "paper": PAPER_VS_INSTRUCTION,
    }
    assert "responses" in extras["vs_schema"]["properties"]


# ------------------------------------------------------------ pasted values


def test_pasted_werkbank_json_becomes_production_text() -> None:
    spec = get_format("two_host_dialogue")
    fields = normalise_fields(
        {
            "speakers": spec.model_dump_json(),
            "audience": DEFAULT_AUDIENCE.model_dump_json(),
            "passages": json.dumps(
                [{"id": "b1", "text": "Erster Block."}, {"id": "b2", "text": "Zweiter."}]
            ),
            "beat_title": "Titel",
        }
    )
    expected_speakers = "\n".join(
        f"- {s.name} ({s.role})" + (f": {s.voice_note}" if s.voice_note else "")
        for s in spec.speakers
    )
    assert fields["speakers"] == expected_speakers
    assert fields["audience"] == DEFAULT_AUDIENCE.description
    assert fields["passages"] == "[b1]\nErster Block.\n\n[b2]\nZweiter."
    assert fields["beat_title"] == "Titel"
    assert speaker_names(fields["speakers"]) == [s.name for s in spec.speakers]


def test_pasted_text_is_kept_as_typed() -> None:
    assert normalise_fields(SAMPLE_BEAT) == SAMPLE_BEAT
    assert speaker_names(SAMPLE_BEAT["speakers"]) == ["Moderator", "Expertin"]


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def signed_in(provider: StubProvider) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        with session_scope() as session:
            session.add(
                User(
                    email=EMAIL,
                    name="VS",
                    password_hash=hash_password(PASSWORD),
                    role="reviewer",
                )
            )
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client


def _setup(**changes: Any) -> dict[str, Any]:
    setup = get_experiment(KEY).defaults().model_dump(mode="json")
    setup.update(changes)
    return setup


def _run(client: TestClient, setup: dict[str, Any]) -> dict[str, Any]:
    created = client.post(f"/api/experiments/{KEY}/runs", json={"setup": setup})
    assert created.status_code == 201, created.text
    body: dict[str, Any] = {}
    for _ in range(200):
        body = client.get(f"/api/experiments/runs/{created.json()['id']}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"VS run did not finish: {body}")


# ------------------------------------------------------------------------ API


def test_detail_carries_both_instruction_texts(signed_in: TestClient) -> None:
    detail = signed_in.get(f"/api/experiments/{KEY}").json()
    assert detail["title"] == "Verbalized Sampling"
    assert detail["extras"]["vs_instructions"]["paper"] == PAPER_VS_INSTRUCTION
    assert signed_in.get("/api/experiments/direct_style").json()["extras"] == {}


def test_run_makes_one_vs_call_and_one_plain_call(
    signed_in: TestClient, provider: StubProvider
) -> None:
    before = len(provider.calls)
    body = _run(signed_in, _setup(k=3))
    assert body["status"] == "completed", body["error"]
    assert len(provider.calls) - before == 2

    vs_call, plain_call = body["calls"]
    assert vs_call["node_name"] == "experiment:verbalized_sampling"
    assert "Generate 3 different versions" in vs_call["system"]
    assert plain_call["node_name"] == "experiment:verbalized_sampling:baseline"
    assert plain_call["system"].endswith("Return JSON only.")
    assert "Verbalized sampling" not in plain_call["system"]
    assert vs_call["messages"] == plain_call["messages"]

    output = body["output"]
    assert output["k"] == 3 and output["word_budget"] == 220
    assert [d["item"] for d in output["vs"]["drafts"]] == ["vs:0", "vs:1", "vs:2"]
    assert output["vs"]["drafts"][0]["probability"] == 0.4
    assert output["vs"]["drafts"][0]["citations"]["located"] >= 1
    assert {s["speaker"] for s in output["vs"]["drafts"][0]["segments"]} <= {
        "Moderator",
        "Expertin",
    }
    assert [d["item"] for d in output["baseline"]["drafts"]] == ["baseline:0"]
    assert output["baseline"]["error"] is None

    cost = output["cost"]
    assert (cost["vs_calls"], cost["baseline_calls"]) == (1, 1)
    assert cost["vs_usd"] + cost["baseline_usd"] == pytest.approx(body["total_cost_usd"])
    assert cost["ratio_to_single_baseline"] is not None
    assert [item["item"] for item in body["items"]] == ["vs:0", "vs:1", "vs:2", "baseline:0"]
    with session_scope() as session:
        for node in ("experiment:verbalized_sampling", "experiment:verbalized_sampling:baseline"):
            assert session.query(LLMCall).filter(LLMCall.node_name == node).count() >= 1


def test_an_unreadable_vs_answer_still_shows_the_plain_draft(
    signed_in: TestClient, provider: StubProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider, "_vs_beats", lambda system, text: {"nothing": True})
    body = _run(signed_in, _setup())
    assert body["status"] == "completed", body["error"]
    assert body["output"]["vs"]["drafts"] == []
    assert "responses" in body["output"]["vs"]["error"]
    assert body["output"]["vs"]["raw"]
    assert len(body["output"]["baseline"]["drafts"]) == 1


def test_missing_k_placeholder_warns(signed_in: TestClient) -> None:
    instruction = DEFAULT_VS_INSTRUCTION.replace("{{k}}", "several")
    body = _run(signed_in, _setup(vs_instruction=instruction))
    assert any("{{k}}" in warning for warning in body["warnings"])


def test_bad_setups_are_refused(signed_in: TestClient) -> None:
    for setup in (_setup(k=9), _setup(k=1), _setup(user_template="{{nope}}")):
        refused = signed_in.post(f"/api/experiments/{KEY}/runs", json={"setup": setup})
        assert refused.status_code == 422, setup


def test_collect_single_drafts_with_their_source(signed_in: TestClient) -> None:
    body = _run(signed_in, _setup(k=2))
    run_id = body["id"]
    saved = signed_in.post(f"/api/experiments/runs/{run_id}/save", json={"item": "vs:1"})
    assert saved.status_code == 201, saved.text
    meta = saved.json()["meta"]
    assert (meta["vs_source"], meta["vs_index"], meta["k"]) == ("vs", 1, 2)
    assert meta["probability"] == 0.2
    again = signed_in.post(f"/api/experiments/runs/{run_id}/save", json={"item": "vs:1"})
    assert again.json()["id"] == saved.json()["id"]

    plain = signed_in.post(f"/api/experiments/runs/{run_id}/save", json={"item": "baseline:0"})
    assert plain.json()["meta"]["vs_source"] == "baseline"
    refreshed = signed_in.get(f"/api/experiments/runs/{run_id}").json()
    collected = {item["item"] for item in refreshed["items"] if item["output_id"]}
    assert collected == {"vs:1", "baseline:0"}
