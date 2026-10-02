"""Outline experiment ("Ablaufplan"): defaults, production parity, runs, parsing, sources."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.experiments  # noqa: F401 - registers every experiment
from app.db import session_scope
from app.experiments.base import render
from app.experiments.outline_plan import (
    OUTLINE_TEMPLATE,
    SAMPLE_OUTLINE,
    outline_warnings,
    read_outline,
)
from app.experiments.registry import get_experiment
from app.llm import registry
from app.main import create_app
from app.models import Document, LLMCall, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.nodes.outline import _SCHEMA, _SYSTEM
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf

PASSWORD = "outline-experiment-password-1"
EMAIL = "outline-experiments@kalliope.test"
KEY = "outline"


# ----------------------------------------------------------------- defaults


def test_registered_with_the_production_prompt_schema_and_model() -> None:
    experiment = get_experiment(KEY)
    setup: Any = experiment.defaults()
    assert setup.system_prompt == _SYSTEM
    assert setup.json_schema == _SCHEMA
    assert setup.json_schema is not _SCHEMA, "editing a setup must not touch production"
    assert setup.user_template == OUTLINE_TEMPLATE
    assert setup.fields == SAMPLE_OUTLINE
    assert (setup.settings.model, setup.settings.max_tokens) == ("claude-opus-5", 12000)
    assert experiment.validate_setup(setup) == []
    assert {spec.key for spec in experiment.field_specs()} == set(SAMPLE_OUTLINE)


# ------------------------------------------------------------------ parsing


def test_read_outline_numbers_beats_like_the_node_and_keeps_them_raw() -> None:
    outline, problem = read_outline(
        {
            "beats": [
                {"title": "Eins", "block_ids": ["b1", "zz"], "word_budget": 10},
                {"title": "Zwei", "block_ids": [], "word_budget": 0, "summary": "S"},
            ]
        }
    )
    assert problem is None and outline is not None
    assert [b.id for b in outline.beats] == ["beat000", "beat001"]
    assert outline.beats[0].block_ids == ["b1", "zz"], "nothing is filtered"
    assert outline.beats[1].summary == "S"


@pytest.mark.parametrize(
    "payload",
    [
        {"sections": []},
        {"beats": [{"headline": "renamed", "block_ids": [], "word_budget": 1}]},
        {"beats": ["not an object"]},
        ["beats"],
    ],
)
def test_read_outline_explains_answers_of_another_shape(payload: Any) -> None:
    outline, problem = read_outline(payload)
    assert outline is None
    assert problem and "raw text" in problem


def test_outline_warnings_name_what_production_would_correct() -> None:
    outline, _ = read_outline(
        {
            "beats": [
                {"title": "A", "block_ids": ["b12", "b99"], "word_budget": 100},
                {"title": "B", "block_ids": ["b14"], "word_budget": 100},
            ]
        }
    )
    assert outline is not None
    warnings = outline_warnings(outline, SAMPLE_OUTLINE)
    assert warnings == [
        "passage ids not in the input: b99",
        "beat budgets sum to 200 against a target of 750; production would rescale them",
    ]
    assert outline_warnings(outline, {"passages": "", "target_words": "200"}) == []


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
                    name="Ablaufplan",
                    password_hash=hash_password(PASSWORD),
                    role="reviewer",
                )
            )
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture(scope="module")
def finished_run(
    signed_in: TestClient, tmp_path_factory: pytest.TempPathFactory, provider: StubProvider
) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("outline") / "outline.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="outline.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        document_id = document.id
    worker.parse_document(document_id)

    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": 15},
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        run_id = run.id
    worker.execute_run(run_id)
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.status == "completed", run.error
    return run_id


def _setup(**settings: Any) -> dict[str, Any]:
    setup = get_experiment(KEY).defaults().model_dump(mode="json")
    setup["settings"].update(settings)
    return setup


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(200):
        body = client.get(f"/api/experiments/runs/{run_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"experiment run {run_id} did not finish: {body}")


def _start(client: TestClient, setup: dict[str, Any]) -> dict[str, Any]:
    created = client.post(f"/api/experiments/{KEY}/runs", json={"setup": setup})
    assert created.status_code == 201, created.text
    return _wait(client, created.json()["id"])


# ------------------------------------------------------------------- source


def test_fields_from_a_run_render_exactly_what_production_sent(
    signed_in: TestClient, finished_run: str, provider: StubProvider
) -> None:
    sent = [call for call in provider.calls if call.system == _SYSTEM]
    assert sent, "the run called the outline node"
    body = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": finished_run}).json()
    assert body["beats"] == []
    assert body["source"]["run_id"] == finished_run
    assert body["source"]["document_title"]
    assert body["source"]["outline"]["beats"], "the run's outline is attached as reference"
    assert render(OUTLINE_TEMPLATE, body["fields"]).text == sent[-1].messages[0].content

    missing = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": "nope"})
    assert missing.status_code == 422


# --------------------------------------------------------------------- runs


def test_run_parses_an_outline_and_tracks_its_cost(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup())
    assert body["status"] == "completed", body["error"]
    outline = body["output"]["outline"]
    assert [beat["id"] for beat in outline["beats"]] == ["beat000", "beat001", "beat002"]
    assert body["output"]["payload"]["beats"][0]["title"] == outline["beats"][0]["title"]
    assert "beat budgets sum to 2100 against a target of 750" in body["warnings"][0]
    call = body["calls"][0]
    assert call["system"] == _SYSTEM
    assert call["messages"][0]["content"] == render(OUTLINE_TEMPLATE, SAMPLE_OUTLINE).text
    assert provider.calls[-1].json_schema == _SCHEMA
    assert body["total_cost_usd"] > 0
    with session_scope() as session:
        recorded = session.query(LLMCall).filter(LLMCall.node_name == f"experiment:{KEY}").count()
    assert recorded >= 1


def test_another_json_shape_stays_raw_with_a_warning(
    signed_in: TestClient, provider: StubProvider
) -> None:
    setup = _setup(structured=False)
    setup["system_prompt"] = "Plan sections, not beats."  # the stub answers {}
    body = _start(signed_in, setup)
    assert body["status"] == "completed", body["error"]
    assert provider.calls[-1].json_schema is None
    assert body["output"]["payload"] == {}
    assert body["output"]["outline"] is None
    assert any('no "beats" list' in warning for warning in body["warnings"])


def test_plain_text_json_is_still_read_as_an_outline(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup(structured=False))
    assert body["status"] == "completed", body["error"]
    assert json.loads(body["output"]["text"])["beats"]
    assert body["output"]["outline"]["beats"]


def test_collect_keeps_the_outline_and_the_settings(signed_in: TestClient) -> None:
    run = _start(signed_in, _setup(temperature=0.3))
    saved = signed_in.post(
        f"/api/experiments/runs/{run['id']}/save", json={"item": "main", "label": "Plan v1"}
    )
    assert saved.status_code == 201, saved.text
    output = saved.json()
    assert output["output"]["outline"]["beats"]
    assert output["setup"]["settings"]["temperature"] == 0.3
    assert output["setup"]["system_prompt"] == _SYSTEM
    assert output["meta"]["model"] == "claude-opus-5"
    listed = signed_in.get(f"/api/experiments/{KEY}/outputs").json()
    assert listed[0]["id"] == output["id"]
    stats = signed_in.get(f"/api/experiments/{KEY}").json()["stats"]
    assert stats["saved_count"] >= 1 and stats["spent_usd"] > 0
