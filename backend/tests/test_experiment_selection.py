"""Selection experiment ("Auswahl"): defaults, production parity, runs, parsing, sources."""

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
from app.experiments.registry import get_experiment
from app.experiments.selection import (
    DOCUMENT_GOALS,
    SAMPLE_SELECTION,
    SELECTION_TEMPLATE,
    read_selection,
    selection_warnings,
)
from app.llm import registry
from app.main import create_app
from app.models import Document, LLMCall, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.nodes.select import _SCHEMA, _SYSTEM
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf

PASSWORD = "selection-experiment-password-1"
EMAIL = "selection-experiments@kalliope.test"
KEY = "selection"


# ----------------------------------------------------------------- defaults


def test_registered_with_the_production_prompt_schema_and_model() -> None:
    experiment = get_experiment(KEY)
    setup: Any = experiment.defaults()
    assert setup.system_prompt == _SYSTEM
    assert setup.json_schema == _SCHEMA
    assert setup.json_schema is not _SCHEMA, "editing a setup must not touch production"
    assert setup.user_template == SELECTION_TEMPLATE
    assert setup.fields == SAMPLE_SELECTION
    assert (setup.settings.model, setup.settings.max_tokens) == ("claude-opus-5", 16000)
    assert experiment.validate_setup(setup) == []
    assert {spec.key for spec in experiment.field_specs()} == set(SAMPLE_SELECTION)


# ------------------------------------------------------------------ parsing


def test_read_selection_keeps_the_answer_raw_and_adds_what_the_node_adds() -> None:
    selection, problem = read_selection(
        {
            "learning_goals": [{"text": "Erklären"}, {"id": "g7", "text": "Zuordnen"}],
            "selected_blocks": [
                {"block_id": "b15", "goal_ids": ["g0"]},
                {"block_id": "zz", "goal_ids": [], "reason": "frei erfunden"},
                {"block_id": "b12", "goal_ids": ["g7"]},
            ],
            "rationale": "Kurz.",
        },
        SAMPLE_SELECTION,
    )
    assert problem is None and selection is not None
    assert [g.id for g in selection.learning_goals] == ["g0", "g7"]
    assert {g.source for g in selection.learning_goals} == {"generated"}
    assert selection.block_ids() == ["b15", "zz", "b12"], "nothing is filtered or sorted"
    assert [b.salience for b in selection.selected_blocks] == [1.3, 1.0, 1.0]

    fields = {**SAMPLE_SELECTION, "goal_instruction": DOCUMENT_GOALS + "- Ziel"}
    from_document, _ = read_selection(
        {"learning_goals": [{"id": "g0", "text": "A"}], "selected_blocks": []}, fields
    )
    assert from_document is not None and from_document.learning_goals[0].source == "document"
    assert from_document.rationale == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"passages": []},
        {"learning_goals": [], "chosen": []},
        {"learning_goals": [{"id": "g0"}], "selected_blocks": []},
        {"learning_goals": [], "selected_blocks": ["b12"]},
        ["learning_goals"],
    ],
)
def test_read_selection_explains_answers_of_another_shape(payload: Any) -> None:
    selection, problem = read_selection(payload, SAMPLE_SELECTION)
    assert selection is None
    assert problem and "raw text" in problem


def test_selection_warnings_name_what_production_would_correct() -> None:
    selection, _ = read_selection(
        {
            "learning_goals": [{"id": "g0", "text": "A"}],
            "selected_blocks": [
                {"block_id": "b12", "goal_ids": ["g0", "g9"]},
                {"block_id": "b99", "goal_ids": []},
                {"block_id": "b12", "goal_ids": []},
            ],
            "rationale": "",
        },
        SAMPLE_SELECTION,
    )
    assert selection is not None
    assert selection_warnings(selection, SAMPLE_SELECTION) == [
        "passage ids not in the input: b99",
        "passages chosen more than once: b12",
        "goal ids without a learning goal: g9",
    ]
    empty, _ = read_selection(
        {"learning_goals": [], "selected_blocks": [{"block_id": "b99"}], "rationale": ""},
        SAMPLE_SELECTION,
    )
    assert empty is not None
    warnings = selection_warnings(empty, SAMPLE_SELECTION)
    assert "no learning goals; production would fail the step" in warnings
    assert "no passage id from the input; production would fail the step" in warnings


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
                    name="Auswahl",
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
    source = write_learning_pdf(tmp_path_factory.mktemp("selection") / "selection.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="selection.pdf", sha256=digest, parse_status="pending")
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
    assert sent, "the run called the select node"
    body = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": finished_run}).json()
    assert body["beats"] == []
    assert body["source"]["run_id"] == finished_run
    assert body["source"]["document_title"]
    assert body["source"]["selection"]["selected_blocks"], "the run's selection is attached"
    assert render(SELECTION_TEMPLATE, body["fields"]).text == sent[-1].messages[0].content

    missing = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": "nope"})
    assert missing.status_code == 422


# --------------------------------------------------------------------- runs


def test_run_parses_a_selection_and_tracks_its_cost(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup())
    assert body["status"] == "completed", body["error"]
    selection = body["output"]["selection"]
    assert [goal["id"] for goal in selection["learning_goals"]] == ["g0", "g1"]
    assert [b["block_id"] for b in selection["selected_blocks"]] == [
        "b3",
        "b12",
        "b14",
        "b15",
        "b16",
        "b21",
    ]
    assert selection["selected_blocks"][3]["salience"] == 1.3
    assert body["output"]["payload"]["rationale"] == selection["rationale"]
    assert body["warnings"] == []
    call = body["calls"][0]
    assert call["system"] == _SYSTEM
    assert call["messages"][0]["content"] == render(SELECTION_TEMPLATE, SAMPLE_SELECTION).text
    assert provider.calls[-1].json_schema == _SCHEMA
    assert body["total_cost_usd"] > 0
    with session_scope() as session:
        recorded = session.query(LLMCall).filter(LLMCall.node_name == f"experiment:{KEY}").count()
    assert recorded >= 1


def test_another_json_shape_stays_raw_with_a_warning(
    signed_in: TestClient, provider: StubProvider
) -> None:
    setup = _setup(structured=False)
    setup["system_prompt"] = "Rank the passages instead."  # the stub answers {}
    body = _start(signed_in, setup)
    assert body["status"] == "completed", body["error"]
    assert provider.calls[-1].json_schema is None
    assert body["output"]["payload"] == {}
    assert body["output"]["selection"] is None
    assert any('no "learning_goals" list' in warning for warning in body["warnings"])


def test_plain_text_json_is_still_read_as_a_selection(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup(structured=False))
    assert body["status"] == "completed", body["error"]
    assert json.loads(body["output"]["text"])["selected_blocks"]
    assert body["output"]["selection"]["selected_blocks"]


def test_collect_keeps_the_selection_and_the_settings(signed_in: TestClient) -> None:
    run = _start(signed_in, _setup(temperature=0.2))
    saved = signed_in.post(
        f"/api/experiments/runs/{run['id']}/save", json={"item": "main", "label": "Auswahl v1"}
    )
    assert saved.status_code == 201, saved.text
    output = saved.json()
    assert output["output"]["selection"]["learning_goals"]
    assert output["setup"]["settings"]["temperature"] == 0.2
    assert output["setup"]["system_prompt"] == _SYSTEM
    assert output["meta"]["model"] == "claude-opus-5"
    listed = signed_in.get(f"/api/experiments/{KEY}/outputs").json()
    assert listed[0]["id"] == output["id"]
    stats = signed_in.get(f"/api/experiments/{KEY}").json()["stats"]
    assert stats["saved_count"] >= 1 and stats["spent_usd"] > 0
