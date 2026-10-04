"""Series planner experiment: defaults, production prompt, runs, parsing, sources."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.experiments  # noqa: F401 - registers every experiment
from app.db import session_scope
from app.experiments.base import SourceIn, render
from app.experiments.registry import get_experiment
from app.experiments.series_plan import (
    SAMPLE_SERIES,
    SERIES_TEMPLATE,
    load_series_source,
    planner_minutes_and_count,
    read_series_plan,
    series_fields,
)
from app.llm import registry
from app.main import create_app
from app.models import Document, LLMCall, Run, RunNode, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.series_plan import _SCHEMA, _SYSTEM, SeriesPlanInput, _user_message
from app.schemas.document import Block, IngestionReport, ParsedDocument, RunSpan, Section
from app.schemas.pipeline import (
    ContentBudget,
    EpisodePlan,
    SeriesBudget,
    SeriesPlan,
    SeriesRequest,
)
from app.schemas.zones import Zone
from app.security import hash_password
from app.worker import worker
from tests.series_support import AUDIENCE, FORMAT, learning_document
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
    assert body["fields"]["episode_count"] == "1"
    assert body["fields"]["minutes_per_episode"] == "15"
    assert "selection" not in body["fields"]
    assert "room for dialogue" in body["fields"]["source_budget"]
    assert "Prefer the smaller figure" in body["fields"]["source_budget"]
    assert "b" in body["fields"]["passages"]
    assert body["fields"]["language"]
    assert body["fields"]["speakers"]

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
    message = call["messages"][0]["content"]
    assert "Plan 2 episodes of about 15 minutes" in message
    assert "Selection:" not in message
    assert "Learning goals:" not in message
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


DOC = learning_document()


def _budget(*, words: int, supportable: float) -> ContentBudget:
    return ContentBudget(
        narratable_words=words,
        words_per_minute=135,
        min_compression=2.5,
        dialogue_expansion=2.5,
        max_supportable_minutes=supportable,
        requested_minutes=15,
        target_minutes=15,
        compression_ratio=1.0,
        verdict="ok",
        explanation="",
    )


def _stored_plan(*, emitted: int, requested: int) -> SeriesPlan:
    episodes = [
        EpisodePlan(
            id=f"ep{index:02d}",
            index=index,
            title=f"Folge {index}",
            block_ids=[f"b{index:06d}"],
            target_minutes=15,
            supportable_minutes=10,
        )
        for index in range(1, emitted + 1)
    ]
    return SeriesPlan(
        title="Serie",
        episodes=episodes,
        budget=SeriesBudget(
            max_supportable_minutes=51.85,
            minutes_per_episode=15,
            requested_episodes=requested,
            verdict="reduced" if emitted != requested else "ok",
            explanation="",
        ),
    )


def test_minutes_and_count_follow_the_request_not_the_series_total() -> None:
    plan = {
        "episodes": [{}, {}, {}],
        "budget": {"minutes_per_episode": 15, "requested_episodes": 4},
    }
    assert planner_minutes_and_count(
        plan, SeriesRequest(episodes=4, minutes_per_episode=15), 60
    ) == (15, 4)
    assert planner_minutes_and_count(
        {"episodes": [{}, {}], "budget": {"minutes_per_episode": 15}},
        SeriesRequest(episodes=5, minutes_per_episode=15),
        75,
    ) == (15, 5)
    assert planner_minutes_and_count(None, None, 15) == (15, 1)
    assert planner_minutes_and_count(
        None, SeriesRequest(episodes=None, minutes_per_episode=15), 15
    ) == (15, 1)
    assert planner_minutes_and_count(
        None, SeriesRequest(episodes=2, minutes_per_episode=15), 30
    ) == (15, 2)


def test_source_sentence_uses_the_whole_document_and_spreads_it() -> None:
    thin = _budget(words=400, supportable=7.41)
    five = series_fields(DOC, thin, FORMAT, AUDIENCE, episode_count=5, minutes=15)
    assert "2800 narratable words" in five["source_budget"]
    assert "about 560 words of source" in five["source_budget"]
    assert "400 narratable" not in five["source_budget"]
    assert "b000019" in five["passages"]
    assert "The document states no objectives of its own." in five["objectives"]
    assert five["language"] == "de"
    assert five["hint"] == ""
    rendered = render(SERIES_TEMPLATE, five).text
    produced = _user_message(
        SeriesPlanInput(
            parsed=DOC,
            budget=thin,
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=SeriesRequest(episodes=5, minutes_per_episode=15),
        ),
        DOC.narratable_blocks(),
        5,
        15,
    )
    assert rendered == produced
    two = series_fields(DOC, thin, FORMAT, AUDIENCE, episode_count=2, minutes=15)
    assert "about 810 words of source" in two["source_budget"]


def _node(session: Any, run_id: str, name: str, digest: str) -> None:
    session.add(
        RunNode(
            run_id=run_id,
            node_name=name,
            node_version="1",
            cache_key=digest[:64],
            artifact_hash=digest,
        )
    )


@pytest.mark.usefixtures("signed_in")
def test_plan_run_loads_per_episode_minutes_and_the_requested_count(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    plan = _stored_plan(emitted=3, requested=4)
    with session_scope() as session:
        document = Document(
            filename="plan.pdf", sha256="p" * 64, parse_status="parsed", title="Plan"
        )
        session.add(document)
        session.flush()
        run = Run(
            document_id=document.id,
            flow_id="series_plan_v0",
            flow_version="1.0",
            config_json={
                "target_minutes": 60,
                "series_request": {"episodes": 4, "minutes_per_episode": 15, "hint": None},
            },
            format_spec_json=FORMAT.model_dump(mode="json"),
            audience_spec_json=AUDIENCE.model_dump(mode="json"),
            status="completed",
            episode_index=0,
        )
        session.add(run)
        session.flush()
        _node(session, run.id, "ingest", store.put("parsed", DOC).hash)
        budget = store.put("budget", _budget(words=2800, supportable=51.85))
        _node(session, run.id, "content_budget", budget.hash)
        _node(session, run.id, "series_plan", store.put("series_plan", plan).hash)
        loaded = load_series_source(session, store, SourceIn(run_id=run.id))
    assert loaded.fields["minutes_per_episode"] == "15"
    assert loaded.fields["episode_count"] == "4"
    assert "about 700 words of source" in loaded.fields["source_budget"]
    assert "about 3240" not in loaded.fields["source_budget"]
    assert loaded.source["plan"]["budget"]["requested_episodes"] == 4
    assert "selection" not in loaded.fields


@pytest.mark.usefixtures("signed_in")
def test_episode_run_keeps_the_document_and_the_series_request(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    plan = _stored_plan(emitted=3, requested=4)
    with session_scope() as session:
        document = Document(
            filename="episode.pdf", sha256="e" * 64, parse_status="parsed", title="Episode"
        )
        session.add(document)
        session.flush()
        series = Series(
            document_id=document.id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            request_json={
                "episodes": 4,
                "minutes_per_episode": 15,
                "hint": None,
                "approved": True,
            },
            format_spec_json=FORMAT.model_dump(mode="json"),
            audience_spec_json=AUDIENCE.model_dump(mode="json"),
            plan_artifact_hash=store.put("series_plan", plan).hash,
            status="outlining",
        )
        session.add(series)
        session.flush()
        run = Run(
            document_id=document.id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": 15, "episode_brief_artifact": "brief"},
            format_spec_json=FORMAT.model_dump(mode="json"),
            audience_spec_json=AUDIENCE.model_dump(mode="json"),
            status="outlined",
            series_id=series.id,
            episode_index=1,
        )
        session.add(run)
        session.flush()
        _node(session, run.id, "ingest", store.put("parsed", DOC).hash)
        _node(
            session,
            run.id,
            "content_budget",
            store.put("budget", _budget(words=400, supportable=7.41)).hash,
        )
        loaded = load_series_source(session, store, SourceIn(run_id=run.id))
    assert loaded.fields["minutes_per_episode"] == "15"
    assert loaded.fields["episode_count"] == "4"
    assert "2800 narratable words" in loaded.fields["source_budget"]
    assert "about 700 words of source" in loaded.fields["source_budget"]
    assert "400 narratable" not in loaded.fields["source_budget"]
    assert "b000019" in loaded.fields["passages"]
    assert loaded.source["plan"] is None
    assert "selection" not in loaded.fields


def _wide_document(words: int) -> ParsedDocument:
    text = " ".join(["Wort"] * words)
    box = (60.0, 100.0, 500.0, 200.0)
    block = Block(
        id="b000000",
        ordinal=0,
        text=text,
        page=0,
        bboxes=[(0, box)],
        char_map=[RunSpan(char_start=0, char_end=len(text), page=0, bbox=box)],
        zone=Zone.BODY,
        zone_confidence=0.95,
        salience=1.0,
        section_id="sec0",
    )
    return ParsedDocument(
        document_id="wide",
        parse_version=1,
        language="de",
        page_count=1,
        page_sizes=[(595.0, 842.0)],
        blocks=[block],
        sections=[
            Section(
                id="sec0",
                title="Stoff",
                level=1,
                ordinal=0,
                block_ids=[block.id],
                page_start=0,
            )
        ],
        objectives=["Die Hörerin kann den Treibhauseffekt erklären."],
        report=IngestionReport(
            language="de",
            page_count=1,
            extractable_words=words,
            narratable_words=words,
            visual_content_ratio=0.0,
            text_density=float(words),
            structure_source="typographic",
            structure_confidence="high",
            section_count=1,
            ingestion_confidence="high",
        ),
    )


@pytest.mark.usefixtures("signed_in")
def test_four_by_fifteen_loads_the_cap_and_matches_production(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    parsed = _wide_document(4000)
    budget = _budget(words=400, supportable=7.41)
    request = SeriesRequest(episodes=4, minutes_per_episode=15, hint="Paris extra")
    plan = _stored_plan(emitted=3, requested=4)
    with session_scope() as session:
        document = Document(
            filename="wide.pdf", sha256="w" * 64, parse_status="parsed", title="Wide"
        )
        session.add(document)
        session.flush()
        run = Run(
            document_id=document.id,
            flow_id="series_plan_v0",
            flow_version="1.0",
            config_json={
                "target_minutes": 60,
                "series_request": request.model_dump(mode="json"),
            },
            format_spec_json=FORMAT.model_dump(mode="json"),
            audience_spec_json=AUDIENCE.model_dump(mode="json"),
            status="completed",
            episode_index=0,
        )
        session.add(run)
        session.flush()
        _node(session, run.id, "ingest", store.put("parsed", parsed).hash)
        _node(session, run.id, "content_budget", store.put("budget", budget).hash)
        _node(session, run.id, "series_plan", store.put("series_plan", plan).hash)
        loaded = load_series_source(session, store, SourceIn(run_id=run.id))
    rendered = render(SERIES_TEMPLATE, loaded.fields).text
    produced = _user_message(
        SeriesPlanInput(
            parsed=parsed,
            budget=budget,
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=request,
        ),
        parsed.narratable_blocks(),
        4,
        15,
    )
    assert loaded.fields["minutes_per_episode"] == "15"
    assert loaded.fields["episode_count"] == "4"
    assert rendered == produced
    assert "Plan 4 episodes of about 15 minutes" in rendered
    assert "about 60 minutes" not in rendered
    assert "about 810 words of source" in rendered
    assert "3240" not in rendered
    assert "400 narratable" not in rendered
    assert "Document language: de" in rendered
    assert "Guidance on the split: Paris extra" in rendered
    assert "List the numbers each episode serves" in rendered
