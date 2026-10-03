"""Series planner experiment: defaults, production prompt, runs, parsing, sources."""

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
from app.experiments.registry import get_experiment
from app.experiments.series_plan import SAMPLE_SERIES, SERIES_TEMPLATE, read_series_plan
from app.llm import registry
from app.main import create_app
from app.models import Document, LLMCall, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.nodes.series_plan import _SCHEMA, _SYSTEM
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf

PASSWORD = "series-plan-experiment-password-1"
EMAIL = "series-plan-experiments@kalliope.test"
KEY = "series_plan"


def test_registered_with_the_production_prompt_schema_and_model() -> None:
    experiment = get_experiment(KEY)
    setup: Any = experiment.defaults()
    assert setup.system_prompt == _SYSTEM
    assert setup.json_schema == _SCHEMA
    assert setup.json_schema is not _SCHEMA, "editing a setup must not touch production"
    assert setup.user_template == SERIES_TEMPLATE
    assert setup.fields == SAMPLE_SERIES
    assert (setup.settings.model, setup.settings.max_tokens) == ("claude-opus-5", 16000)
    assert experiment.validate_setup(setup) == []
    assert {spec.key for spec in experiment.field_specs()} == set(SAMPLE_SERIES)


def test_read_series_plan_keeps_episodes_and_snaps_goals() -> None:
    plan, problem = read_series_plan(
        {
            "title": "Eine Serie",
            "through_line": "Vom Grundsatz zur Anwendung.",
            "episodes": [
                {
                    "title": "Folge 1",
                    "role": "Einführung",
                    "summary": "Der Anfang.",
                    "block_ids": ["b12", "b14"],
                    "goals": [
                        {
                            "text": "Den Anfang erklären",
                            "bloom_level": "analyze",
                            "derivation": "Aus dem gewünschten Ergebnis.",
                        },
                        "nur ein String",
                        {"text": "", "bloom_level": "understand"},
                        {"text": "Kein Niveau", "bloom_level": "topic"},
                    ],
                }
            ],
        }
    )
    assert problem is None and plan is not None
    episode = plan.episodes[0]
    assert episode.role == "Einführung"
    assert episode.block_ids == ["b12", "b14"]
    assert [(goal.text, goal.bloom_level) for goal in episode.goals] == [
        ("Den Anfang erklären", "analyse")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"sections": []},
        {"episodes": [{"headline": "ohne Titel", "block_ids": ["b1"]}]},
        {"episodes": ["not an object"]},
        {"episodes": []},
    ],
)
def test_read_series_plan_explains_answers_of_another_shape(payload: Any) -> None:
    plan, problem = read_series_plan(payload)
    assert plan is None
    assert problem and "raw text" in problem


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
                    name="Folgen planen",
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
    source = write_learning_pdf(tmp_path_factory.mktemp("series-plan") / "series.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="series.pdf", sha256=digest, parse_status="pending")
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


def test_fields_from_a_run_fill_the_planner_inputs(
    signed_in: TestClient, finished_run: str
) -> None:
    body = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": finished_run}).json()
    assert body["source"]["run_id"] == finished_run
    assert body["source"]["document_title"]
    assert body["source"]["plan"] is None
    assert body["fields"]["episode_count"] == "2"
    assert body["fields"]["minutes_per_episode"] == "15"
    assert "dialogue expansion" in body["fields"]["content_budget"]
    assert "b" in body["fields"]["document"]
    assert "Learning goals:" in body["fields"]["selection"]
    assert "Speakers:" in body["fields"]["format_audience"]

    missing = signed_in.post(f"/api/experiments/{KEY}/source", json={"run_id": "nope"})
    assert missing.status_code == 422


def test_run_parses_a_plan_and_tracks_its_cost(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup())
    assert body["status"] == "completed", body["error"]
    plan = body["output"]["plan"]
    assert plan["title"] == "Eine Serie"
    episode = plan["episodes"][0]
    assert episode["title"].startswith("Folge 1")
    assert episode["role"]
    assert episode["block_ids"]
    assert episode["goals"][0]["bloom_level"] == "understand"
    assert body["output"]["payload"]["episodes"][0]["title"] == episode["title"]
    call = body["calls"][0]
    assert call["system"] == _SYSTEM
    assert "Plan 2 episodes of about 15 minutes" in call["messages"][0]["content"]
    assert provider.calls[-1].json_schema == _SCHEMA
    assert body["total_cost_usd"] > 0
    with session_scope() as session:
        recorded = session.query(LLMCall).filter(LLMCall.node_name == f"experiment:{KEY}").count()
    assert recorded >= 1


def test_another_json_shape_stays_raw_with_a_warning(
    signed_in: TestClient, provider: StubProvider
) -> None:
    setup = _setup(structured=False)
    setup["system_prompt"] = "Plan sections, not episodes."
    body = _start(signed_in, setup)
    assert body["status"] == "completed", body["error"]
    assert provider.calls[-1].json_schema is None
    assert body["output"]["payload"] == {}
    assert body["output"]["plan"] is None
    assert any('no "episodes" list' in warning for warning in body["warnings"])


def test_plain_text_json_is_still_read_as_a_plan(
    signed_in: TestClient, provider: StubProvider
) -> None:
    body = _start(signed_in, _setup(structured=False))
    assert body["status"] == "completed", body["error"]
    assert json.loads(body["output"]["text"])["episodes"]
    assert body["output"]["plan"]["episodes"]


def test_collect_keeps_the_plan_and_the_settings(signed_in: TestClient) -> None:
    run = _start(signed_in, _setup(temperature=0.3))
    saved = signed_in.post(
        f"/api/experiments/runs/{run['id']}/save", json={"item": "main", "label": "Plan v1"}
    )
    assert saved.status_code == 201, saved.text
    output = saved.json()
    assert output["output"]["plan"]["episodes"]
    assert output["setup"]["settings"]["temperature"] == 0.3
    assert output["setup"]["system_prompt"] == _SYSTEM
    assert output["meta"]["model"] == "claude-opus-5"
    listed = signed_in.get(f"/api/experiments/{KEY}/outputs").json()
    assert listed[0]["id"] == output["id"]
    stats = signed_in.get(f"/api/experiments/{KEY}").json()["stats"]
    assert stats["saved_count"] >= 1 and stats["spent_usd"] > 0
