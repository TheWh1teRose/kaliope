"""Cooperative stop: queued, running, series, and audio.

A stop never looks like a failure. Work that already finished stays cached, and
a later execution of the same run reuses it. Stopping twice, or stopping after
the work has finished, changes nothing.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, func, select

from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.llm import registry
from app.llm.base import CompletionRequest
from app.main import create_app
from app.models import AudioTake, Document, GateResult, Run, Segment, Series, User
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow as PipelineFlow
from app.security import hash_password
from app.speech import registry as speech_registry
from app.speech.base import DialogueRequest, cost_usd
from app.worker import worker
from tests.support import StubProvider, StubSpeech
from tests.test_gates import claim, filler, make_context
from tests.test_series_api import _long_pdf

PASSWORD = "stop-password-1"
EMAIL = "stop@kalliope.test"
_STUB = StubProvider()


class BlockingProvider(StubProvider):
    """The stub, except one matching call waits until the test releases it."""

    def __init__(self, predicate: Any) -> None:
        super().__init__()
        self.predicate = predicate
        self.entered = threading.Event()
        self.release = threading.Event()
        self.blocked_once = False
        self.later: list[str] = []

    def complete(self, request: CompletionRequest) -> Any:
        system = request.system or ""
        content = request.messages[-1].content if request.messages else ""
        if not self.blocked_once and self.predicate(system, content):
            self.blocked_once = True
            self.entered.set()
            assert self.release.wait(30), "the stop test never released the provider"
        elif self.blocked_once:
            self.later.append(system)
        return super().complete(request)


class BlockingSpeech(StubSpeech):
    """The stub speech provider; the first dialogue waits until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_first = True

    def dialogue(self, request: DialogueRequest) -> Any:
        if self.block_first:
            self.block_first = False
            self.entered.set()
            assert self.release.wait(30), "the stop test never released speech"
        return super().dialogue(request)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    registry.register_provider("anthropic", _STUB)
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Stop", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client
    registry.reset_providers()
    speech_registry.reset_provider()


@pytest.fixture(scope="module")
def document_id(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    payload = _long_pdf()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="stop.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        identifier = document.id
    worker.parse_document(identifier)
    with session_scope() as session:
        document = session.get(Document, identifier)
        assert document is not None and document.parse_status == "parsed", document.parse_error
    return identifier


def _wait_run(client: TestClient, run_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in states and not body["active"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run stayed {body.get('status')}: {body.get('error')}")


def _wait_series(client: TestClient, series_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/series/{series_id}").json()
        if body["status"] in states and not body["active"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"series stayed {body.get('status')}: {body.get('error')}")


def _wait_take(client: TestClient, run_id: str, take_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    found: dict[str, Any] = {}
    while time.monotonic() < deadline:
        takes = client.get(f"/api/runs/{run_id}/audio").json()["takes"]
        found = next((take for take in takes if take["id"] == take_id), {})
        if found.get("status") in states and not found.get("active", False):
            return found
        time.sleep(0.05)
    raise AssertionError(f"take stayed {found.get('status')}: {found.get('error')}")


def _sse(client: TestClient, path: str) -> str:
    with client.stream("GET", path) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def _beat_bodies(provider: StubProvider) -> list[str]:
    return [
        call.messages[-1].content
        for call in provider.calls
        if "You write one beat" in (call.system or "")
    ]


@contextmanager
def _one_worker_slot() -> Iterator[None]:
    original = worker.max_workers
    worker.shutdown(wait=True)
    worker.max_workers = 1
    worker.start()
    try:
        yield
    finally:
        worker.shutdown(wait=True)
        worker.max_workers = original
        worker.start()


def test_status_columns_are_not_constrained() -> None:
    for model in (Run, Series, AudioTake):
        checks = [c for c in model.__table__.constraints if isinstance(c, CheckConstraint)]
        assert checks == []
        assert model.__table__.c.status.type.length == 20


def test_a_queued_run_is_removed_and_stopping_twice_is_idempotent(
    client: TestClient, document_id: str
) -> None:
    held = threading.Event()
    release = threading.Event()

    def occupy() -> None:
        held.set()
        assert release.wait(30)

    with _one_worker_slot():
        holder = worker.submit(occupy)
        assert held.wait(5)
        try:
            created = client.post(
                "/api/runs", json={"document_id": document_id, "flow_id": "baseline_v0"}
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            assert created.json()["status"] == "queued"

            stopped = client.post(f"/api/runs/{run_id}/stop")
            assert stopped.status_code == 200, stopped.text
            body = stopped.json()
            assert body["outcome"] == "stopped"
            assert body["status"] == "stopped"
            assert body["stopped_by"] == EMAIL

            stored = client.get(f"/api/runs/{run_id}").json()
            assert stored["status"] == "stopped"
            assert stored["error"] is None
            assert stored["stopped_by"] == EMAIL

            again = client.post(f"/api/runs/{run_id}/stop").json()
            assert again["outcome"] == "already_stopped"
            assert again["status"] == "stopped"
            assert client.get(f"/api/runs/{run_id}").json()["status"] == "stopped"
        finally:
            release.set()
            holder.result(timeout=5)


def test_stopping_a_running_run_keeps_artifacts_and_a_rerun_reuses_them(
    client: TestClient, document_id: str
) -> None:
    provider = BlockingProvider(lambda system, _content: "You choose which passages" in system)
    registry.register_provider("anthropic", provider)
    created = client.post("/api/runs", json={"document_id": document_id, "flow_id": "baseline_v0"})
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    try:
        assert provider.entered.wait(60), "select never started"
        stopped = client.post(f"/api/runs/{run_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["outcome"] == "stopping"
        assert stopped.json()["stopped_by"] == EMAIL
        draining = client.get(f"/api/runs/{run_id}").json()
        assert draining["status"] == "stopped" and draining["active"] is True
        listed = client.get("/api/runs").json()
        assert next(row for row in listed if row["id"] == run_id)["active"] is True
        provider.release.set()
        body = _wait_run(client, run_id, {"stopped", "failed", "completed"})
    finally:
        provider.release.set()
        registry.register_provider("anthropic", _STUB)

    assert body["status"] == "stopped", body["error"]
    assert body["error"] is None
    assert body["active"] is False
    assert body["total_cost_usd"] > draining["total_cost_usd"]
    subscriber, replay = bus.subscribe(run_id)
    try:
        assert sum(event.type == "run.stopped" for event in replay) >= 2
        assert replay[-1].type == "run.stopped"
    finally:
        bus.unsubscribe(run_id, subscriber)
    nodes = {node["node_name"]: node for node in body["nodes"]}
    assert nodes["ingest"]["artifact_hash"]
    assert nodes["content_budget"]["artifact_hash"]
    assert not nodes.get("select", {}).get("artifact_hash")
    assert not nodes.get("outline", {}).get("artifact_hash")
    assert not any("You plan the running order" in system for system in provider.later)
    assert not any("You write one beat" in system for system in provider.later)

    graph = client.get(f"/api/runs/{run_id}/graph").json()
    assert graph["failed_node"] is None
    assert all(node["status"] != "failed" and node["error"] is None for node in graph["nodes"])
    assert _sse(client, f"/api/runs/{run_id}/events").count("event: run.stopped") >= 1

    again = client.post(f"/api/runs/{run_id}/stop").json()
    assert again["outcome"] == "already_stopped"

    worker._arm(run_id)
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = "queued"
        run.finished_at = None
    worker.execute_run(run_id)
    resumed = client.get(f"/api/runs/{run_id}").json()
    assert resumed["status"] == "completed", resumed["error"]
    # The node row keeps the cost of the first computation. The manifest of the
    # rerun is where the cache hit is recorded.
    resumed_nodes = {node["name"]: node for node in resumed["manifest"]["nodes"]}
    assert resumed_nodes["ingest"]["cache_hit"] is True
    assert resumed_nodes["content_budget"]["cache_hit"] is True
    assert resumed_nodes["ingest"]["artifact_hash"] == nodes["ingest"]["artifact_hash"]

    finished = client.post(f"/api/runs/{run_id}/stop").json()
    assert finished["outcome"] == "finished"
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"


def test_stopping_during_a_beat_does_not_start_the_next_and_keeps_the_cached_one(
    client: TestClient, document_id: str
) -> None:
    seen = {"beats": 0}

    def second_beat(system: str, _content: str) -> bool:
        if "You write one beat" not in system:
            return False
        seen["beats"] += 1
        return seen["beats"] == 2

    provider = BlockingProvider(second_beat)
    registry.register_provider("anthropic", provider)
    # A different length from the other runs in this module, so this script is
    # not served from their cache and the beats actually run.
    created = client.post(
        "/api/runs",
        json={"document_id": document_id, "flow_id": "baseline_v0", "target_minutes": 8},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    try:
        assert provider.entered.wait(120), client.get(f"/api/runs/{run_id}").json()
        stopped = client.post(f"/api/runs/{run_id}/stop")
        assert stopped.json()["outcome"] == "stopping"
        provider.release.set()
        body = _wait_run(client, run_id, {"stopped", "failed", "completed"})
        bodies = _beat_bodies(provider)
    finally:
        provider.release.set()
        registry.register_provider("anthropic", _STUB)

    assert body["status"] == "stopped", body["error"]
    assert body["error"] is None
    assert len(bodies) == 2
    assert not any("You write one beat" in system for system in provider.later)
    nodes = {node["node_name"]: node for node in body["nodes"]}
    assert nodes["outline"]["artifact_hash"]
    assert not nodes.get("script", {}).get("artifact_hash")

    registry.register_provider("anthropic", provider)
    try:
        worker._arm(run_id)
        with session_scope() as session:
            run = session.get(Run, run_id)
            assert run is not None
            run.status = "queued"
            run.finished_at = None
        worker.execute_run(run_id)
    finally:
        registry.register_provider("anthropic", _STUB)
    resumed = client.get(f"/api/runs/{run_id}").json()
    assert resumed["status"] == "completed", resumed["error"]
    fresh = _beat_bodies(provider)[len(bodies) :]
    assert bodies[0] not in fresh
    assert bodies[1] in fresh


def test_stopping_a_series_keeps_the_plan_and_the_finished_episode(
    client: TestClient, document_id: str
) -> None:
    provider = BlockingProvider(
        lambda system, content: "You write one beat" in system and ", episode 2 of " in content
    )
    created = client.post(
        "/api/series",
        json={"document_id": document_id, "minutes_per_episode": 3, "episodes": 2},
    )
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]
    held = _wait_series(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]
    plan_title = held["plan"]["title"]

    registry.register_provider("anthropic", provider)
    try:
        approved = client.post(f"/api/series/{series_id}/approve")
        assert approved.status_code == 200, approved.text
        assert provider.entered.wait(180), "episode 2 never reached a beat"
        stopped = client.post(f"/api/series/{series_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["outcome"] == "stopping"
        assert stopped.json()["stopped_by"] == EMAIL
        provider.release.set()
        done = _wait_series(client, series_id, {"stopped", "failed", "completed"})
    finally:
        provider.release.set()
        registry.register_provider("anthropic", _STUB)

    assert done["status"] == "stopped", done["error"]
    assert done["error"] is None
    assert done["plan"]["title"] == plan_title
    assert done["stopped_by"] == EMAIL
    statuses = [episode["status"] for episode in done["episodes"]]
    assert statuses[0] == "completed"
    assert statuses[1] == "stopped"
    assert done["episodes"][1]["error"] is None
    assert not any(", episode 3 of " in body for body in _beat_bodies(provider))
    assert not any("You write one beat" in system for system in provider.later)

    graph = client.get(f"/api/series/{series_id}/graph").json()
    assert graph["status"] == "stopped"
    assert graph["plan"] is not None
    assert _sse(client, f"/api/series/{series_id}/events").count("event: series.stopped") >= 1

    again = client.post(f"/api/series/{series_id}/stop").json()
    assert again["outcome"] == "already_stopped"
    assert client.get(f"/api/series/{series_id}").json()["plan"]["title"] == plan_title


def test_resuming_a_stopped_series_in_the_same_process_runs_it(
    client: TestClient, document_id: str
) -> None:
    provider = BlockingProvider(lambda system, _content: "You split a source document" in system)
    registry.register_provider("anthropic", provider)
    try:
        created = client.post(
            "/api/series",
            json={
                "document_id": document_id,
                "minutes_per_episode": 3,
                "episodes": 2,
                # A hint no other test in this module uses, so the planner is not
                # served from their cache and this stop actually meets it mid-call.
                "hint": "resume after stop",
            },
        )
        assert created.status_code == 201, created.text
        series_id = created.json()["id"]
        assert provider.entered.wait(60), "the planner never started"
        stopped = client.post(f"/api/series/{series_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["outcome"] == "stopping"
        provider.release.set()
        done = _wait_series(client, series_id, {"stopped", "failed", "planned", "completed"})
    finally:
        provider.release.set()
        registry.register_provider("anthropic", _STUB)

    assert done["status"] == "stopped", done["error"]

    resumed = client.post(f"/api/series/{series_id}/resume")
    assert resumed.status_code == 200, resumed.text
    continued = _wait_series(client, series_id, {"planned", "failed", "stopped", "completed"})
    assert continued["status"] == "planned", continued["error"]


def test_stopping_a_later_queued_episode_skips_it(client: TestClient, document_id: str) -> None:
    created = client.post(
        "/api/series",
        json={"document_id": document_id, "minutes_per_episode": 3, "episodes": 2, "force": True},
    )
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]
    held = _wait_series(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]

    provider = BlockingProvider(lambda system, _content: "You choose which passages" in system)
    registry.register_provider("anthropic", provider)
    try:
        approved = client.post(f"/api/series/{series_id}/approve")
        assert approved.status_code == 200, approved.text
        assert provider.entered.wait(180), "episode 1 never reached selection"
        live = client.get(f"/api/series/{series_id}").json()
        later = next(episode for episode in live["episodes"] if episode["index"] == 2)
        assert later["status"] == "queued", later
        assert later["run_id"]
        stopped = client.post(f"/api/runs/{later['run_id']}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["outcome"] == "stopped"
        assert stopped.json()["status"] == "stopped"
        provider.release.set()
        done = _wait_series(client, series_id, {"completed", "failed", "stopped"})
    finally:
        provider.release.set()
        registry.register_provider("anthropic", _STUB)

    assert done["status"] == "completed", done["error"]
    assert [episode["status"] for episode in done["episodes"]] == ["completed", "stopped"]


def test_stopping_audio_at_the_approval_spends_nothing_further(
    client: TestClient, document_id: str
) -> None:
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    run_id = _completed_run(client, document_id)
    started = client.post(f"/api/runs/{run_id}/audio", json={})
    assert started.status_code == 201, started.text
    take_id = started.json()["id"]
    waiting = _wait_take(client, run_id, take_id, {"paused", "failed"})
    assert waiting["status"] == "paused", waiting["error"]

    stopped = client.post(f"/api/audio/takes/{take_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["outcome"] == "stopped"
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["stopped_by"] == EMAIL
    stored = _wait_take(client, run_id, take_id, {"stopped"})
    assert stored["error"] is None
    assert stored["chunks"] == []
    assert speech.requests == []

    again = client.post(f"/api/audio/takes/{take_id}/stop").json()
    assert again["outcome"] == "already_stopped"


def test_stopping_audio_mid_chunk_keeps_the_chunk_that_was_already_paid_for(
    client: TestClient, document_id: str
) -> None:
    cast = {
        "voices": [
            {"speaker": "Moderator", "voice_id": "v-mod", "voice_name": "Jonas"},
            {"speaker": "Expertin", "voice_id": "v-exp", "voice_name": "Mara"},
        ],
        "model_id": "eleven_v4",
        "stability": 0.5,
        # A seed no other test uses, so the spoken chunk is not already cached.
        "seed": 424242,
    }
    assert client.put("/api/formats/two_host_dialogue/voices", json=cast).status_code == 200
    speech = BlockingSpeech()
    speech_registry.register_provider(speech)
    run_id = _completed_run(client, document_id)
    started = client.post(f"/api/runs/{run_id}/audio", json={})
    assert started.status_code == 201, started.text
    take_id = started.json()["id"]
    waiting = _wait_take(client, run_id, take_id, {"paused", "failed"})
    assert waiting["status"] == "paused", waiting["error"]
    paid_before = waiting["total_cost_usd"]
    try:
        approved = client.post(f"/api/audio/takes/{take_id}/approve", json={})
        assert approved.status_code == 200, approved.text
        assert speech.entered.wait(60), _wait_take(
            client, run_id, take_id, {"paused", "running", "failed", "stopped", "completed"}
        )
        stopped = client.post(f"/api/audio/takes/{take_id}/stop")
        assert stopped.json()["outcome"] == "stopping"
        draining = client.get(f"/api/runs/{run_id}/audio").json()["takes"][0]
        assert draining["status"] == "stopped" and draining["active"] is True
        speech.release.set()
        stored = _wait_take(client, run_id, take_id, {"stopped", "failed", "completed"})
    finally:
        speech.release.set()

    assert stored["status"] == "stopped", stored["error"]
    assert stored["error"] is None
    assert stored["active"] is False
    assert len(speech.requests) == 1
    first = tuple(item.text for item in speech.requests[0].inputs)
    chunk_cost = cost_usd(cast["model_id"], speech.requests[0].characters())
    assert stored["total_cost_usd"] == pytest.approx(paid_before + chunk_cost)

    # The stop replaced the in-flight render. Approving again resumes from the
    # approval and must reuse the chunk that was already spoken.
    worker._arm(take_id)
    with session_scope() as session:
        take = session.get(AudioTake, take_id)
        assert take is not None
        take.status = "queued"
    worker.execute_take(take_id)
    waiting = _wait_take(client, run_id, take_id, {"paused", "failed", "completed"})
    assert waiting["status"] == "paused", waiting.get("error")
    approved = client.post(f"/api/audio/takes/{take_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    done = _wait_take(client, run_id, take_id, {"completed", "failed", "stopped"})
    assert done["status"] == "completed", done["error"]
    spoken = [tuple(item.text for item in request.inputs) for request in speech.requests]
    assert spoken.count(first) == 1
    paid = sum(cost_usd(cast["model_id"], request.characters()) for request in speech.requests)
    assert done["total_cost_usd"] == pytest.approx(paid_before + paid)

    finished = client.post(f"/api/audio/takes/{take_id}/stop").json()
    assert finished["outcome"] == "finished"
    assert _wait_take(client, run_id, take_id, {"completed"})["status"] == "completed"


def test_a_stop_during_gates_does_not_block_the_next_finish(document_id: str) -> None:
    context = make_context([claim(filler(30))])
    flow = PipelineFlow(id="baseline_v0", version="1", nodes=[], gates=["G4"])
    bag = {
        "parsed": context.parsed,
        "script": context.script,
        "outline": context.outline,
        "selection": context.selection,
        "budget": context.budget,
        "format_spec": context.format_spec,
    }
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="stopped",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    store = ArtifactStore(get_settings().artifacts_dir)
    worker._finish_run(run_id, bag, flow, store)

    with session_scope() as session:
        stored = session.get(Run, run_id)
        assert stored is not None and stored.status == "stopped"
        assert _finish_rows(session, run_id) == (0, 0)
        stored.status = "running"

    worker._finish_run(run_id, bag, flow, store)

    with session_scope() as session:
        stored = session.get(Run, run_id)
        assert stored is not None and stored.status == "completed"
        assert _finish_rows(session, run_id) == (1, 1)


def _finish_rows(session: Any, run_id: str) -> tuple[int, int]:
    gates = session.scalar(
        select(func.count()).select_from(GateResult).where(GateResult.run_id == run_id)
    )
    segments = session.scalar(
        select(func.count()).select_from(Segment).where(Segment.run_id == run_id)
    )
    return int(gates or 0), int(segments or 0)


def _completed_run(client: TestClient, document_id: str) -> str:
    registry.register_provider("anthropic", _STUB)
    created = client.post("/api/runs", json={"document_id": document_id, "flow_id": "baseline_v0"})
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    stored = _wait_run(client, run_id, {"completed", "failed", "stopped"})
    assert stored["status"] == "completed", stored["error"]
    return run_id


def test_busy_run_stop_during_gates_is_durable(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    module = importlib.import_module("app.worker")
    original = module.run_gates
    entered = threading.Event()
    release = threading.Event()

    def gates(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_gates", gates)
    registry.register_provider("anthropic", _STUB)
    created = client.post("/api/runs", json={"document_id": document_id, "flow_id": "baseline_v0"})
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    try:
        assert entered.wait(60)
        stopped = client.post(f"/api/runs/{run_id}/stop").json()
        assert stopped["outcome"] == "stopping"
        assert stopped["status"] == "stopped"
        before = client.get(f"/api/runs/{run_id}").json()
        assert before["status"] == "stopped"
        assert before["stopped_by"] == EMAIL
        assert client.post(f"/api/runs/{run_id}/stop").json()["outcome"] == "already_stopped"
    finally:
        release.set()
    done = _wait_run(client, run_id, {"stopped", "completed", "failed"})
    assert done["status"] == "stopped"
    assert done["error"] is None
    assert done["manifest"] == before["manifest"]
    assert done["total_cost_usd"] == before["total_cost_usd"]
    with session_scope() as session:
        assert _finish_rows(session, run_id) == (0, 0)


@pytest.mark.parametrize("stage", ["plan", "checks"])
def test_busy_series_stop_at_finalization_is_durable(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import importlib

    module = importlib.import_module("app.worker")
    entered = threading.Event()
    release = threading.Event()
    if stage == "plan":
        original = worker._run_for_series

        def finish_plan(*args: Any) -> Any:
            result = original(*args)
            entered.set()
            assert release.wait(30)
            return result

        monkeypatch.setattr(worker, "_run_for_series", finish_plan)
    else:
        check = module.coverage_check

        def coverage(*args: Any) -> Any:
            entered.set()
            assert release.wait(30)
            return check(*args)

        monkeypatch.setattr(module, "coverage_check", coverage)
    registry.register_provider("anthropic", _STUB)
    created = client.post(
        "/api/series",
        json={"document_id": document_id, "minutes_per_episode": 3, "episodes": 2},
    )
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]
    try:
        if stage == "checks":
            planned = _wait_series(client, series_id, {"planned", "failed"})
            assert planned["status"] == "planned", planned["error"]
            assert client.post(f"/api/series/{series_id}/approve").status_code == 200
        assert entered.wait(120)
        stopped = client.post(f"/api/series/{series_id}/stop").json()
        assert stopped["outcome"] == "stopping"
        assert stopped["status"] == "stopped"
        before = client.get(f"/api/series/{series_id}").json()
        assert before["status"] == "stopped"
        assert before["active"] is True
        assert before["stopped_by"] == EMAIL
        assert client.post(f"/api/series/{series_id}/stop").json()["outcome"] == "already_stopped"
    finally:
        release.set()
    done = _wait_series(client, series_id, {"stopped", "planned", "completed", "failed"})
    assert done["status"] == "stopped"
    assert done["active"] is False
    assert done["error"] is None
    if stage == "checks":
        assert done["plan"] == before["plan"]
        assert all(episode["status"] == "completed" for episode in done["episodes"])
    else:
        resumed = client.post(f"/api/series/{series_id}/resume")
        assert resumed.status_code == 200, resumed.text
        done = _wait_series(client, series_id, {"planned", "failed", "stopped"})
        assert done["status"] == "planned", done["error"]


def test_busy_take_stop_at_completion_keeps_paid_output(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    run_id = _completed_run(client, document_id)
    original = worker._end_take
    entered = threading.Event()
    release = threading.Event()
    output: dict[str, Any] = {}

    def end(take_id: str, status: str, **kwargs: Any) -> None:
        if status == "completed":
            output.update(kwargs)
            entered.set()
            assert release.wait(30)
        original(take_id, status, **kwargs)

    monkeypatch.setattr(worker, "_end_take", end)
    created = client.post(f"/api/runs/{run_id}/audio", json={})
    assert created.status_code == 201, created.text
    take_id = created.json()["id"]
    waiting = _wait_take(client, run_id, take_id, {"paused", "failed"})
    assert waiting["status"] == "paused", waiting["error"]
    try:
        assert client.post(f"/api/audio/takes/{take_id}/approve", json={}).status_code == 200
        assert entered.wait(120)
        stopped = client.post(f"/api/audio/takes/{take_id}/stop").json()
        assert stopped["outcome"] == "stopping"
        assert stopped["status"] == "stopped"
        assert stopped["stopped_by"] == EMAIL
        assert (
            client.post(f"/api/audio/takes/{take_id}/stop").json()["outcome"] == "already_stopped"
        )
    finally:
        release.set()
    done = _wait_take(client, run_id, take_id, {"stopped", "completed", "failed"})
    assert done["status"] == "stopped"
    assert done["error"] is None
    assert done["stopped_by"] == EMAIL
    assert done["total_cost_usd"] == pytest.approx(waiting["total_cost_usd"] + output["spent"])
    with session_scope() as session:
        take = session.get(AudioTake, take_id)
        assert take is not None
        assert take.manifest_json["bag_hashes"] == output["manifest"]["bag_hashes"]
        assert "audio_mix" in take.manifest_json["bag_hashes"]
