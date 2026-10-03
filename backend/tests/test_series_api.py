"""A series end to end over the API: plan, hold, replan, approve, write, export, resume."""

from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.events import bus
from app.llm import registry
from app.llm.base import CompletionRequest
from app.main import create_app
from app.models import Document, Run, RunNode, Series, User
from app.security import hash_password
from app.series import series_channel
from app.worker import worker
from tests.support import TOPICS, StubProvider

PASSWORD = "series-password-1"
EMAIL = "series@kalliope.test"


class SeriesStub(StubProvider):
    """The shared stub; ``fail_episode`` makes that episode's beat calls fail."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_episode: int | None = None

    def complete(self, request: CompletionRequest) -> Any:
        content = request.messages[-1].content if request.messages else ""
        if (
            self.fail_episode is not None
            and "You write one beat" in (request.system or "")
            and f", episode {self.fail_episode} of " in content
        ):
            raise RuntimeError("provider went away")
        return super().complete(request)


@pytest.fixture(scope="module")
def provider() -> Iterator[SeriesStub]:
    stub = SeriesStub()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def client(provider: SeriesStub) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Serie", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="module")
def document_id(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    payload = _long_pdf()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="series.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        identifier = document.id
    worker.parse_document(identifier)
    return identifier


def _long_pdf() -> bytes:
    """Twelve pages of body text: enough for three episodes of three minutes."""
    import pymupdf as fitz

    doc = fitz.open()
    for index, (title, text) in enumerate([*TOPICS, *TOPICS]):
        # Each page's wording differs, so the repetition filter keeps it as body text.
        body = text.replace(".", f" in Kapitel {index + 1}.")
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 40), "Kursmaterial Physiologie", fontsize=8)
        page.insert_text((520, 802), str(index + 1), fontsize=8)
        page.insert_text((60, 90), f"{index + 1}. {title}", fontsize=18)
        page.insert_textbox(fitz.Rect(60, 110, 285, 760), body * 9, fontsize=8)
        page.insert_textbox(fitz.Rect(310, 110, 535, 760), body * 9, fontsize=8)
    payload: bytes = doc.tobytes()
    doc.close()
    return payload


def _wait(client: TestClient, series_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        body = client.get(f"/api/series/{series_id}").json()
        if body["status"] in states and not body["active"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"series stayed '{body['status']}'")


def _beat_calls(provider: StubProvider, episode: int) -> list[str]:
    return [
        call.messages[-1].content
        for call in provider.calls
        if "You write one beat" in (call.system or "")
        and f", episode {episode} of " in call.messages[-1].content
    ]


def test_the_budget_says_how_long_the_document_can_be(client: TestClient, document_id: str) -> None:
    body = client.get(f"/api/documents/{document_id}/budget").json()
    assert body["words_per_minute"] == 135
    assert body["dialogue_expansion"] == 2.5
    assert body["max_supportable_minutes"] == round(
        body["narratable_words"] / body["words_per_minute"] * body["dialogue_expansion"], 2
    )


def test_a_planner_flow_is_not_a_single_run_and_a_pausing_flow_not_a_series(
    client: TestClient, document_id: str
) -> None:
    single = client.post(
        "/api/runs", json={"document_id": document_id, "flow_id": "series_plan_v0"}
    )
    assert single.status_code == 422
    series = client.post(
        "/api/series",
        json={"document_id": document_id, "flow_id": "objectives_v0", "minutes_per_episode": 3},
    )
    assert series.status_code == 422
    flows = {f["id"]: f["purpose"] for f in client.get("/api/flows").json()}
    assert flows["series_plan_v0"] == "series_plan" and flows["baseline_v0"] == "episode"


def test_an_audio_pipeline_starts_neither_a_run_nor_a_series(
    client: TestClient, document_id: str
) -> None:
    flows = {f["id"]: f["purpose"] for f in client.get("/api/flows").json()}
    assert flows["elevenlabs_dialog_v0"] == "audio"
    single = client.post(
        "/api/runs", json={"document_id": document_id, "flow_id": "elevenlabs_dialog_v0"}
    )
    assert single.status_code == 422
    assert "audio pipeline" in single.json()["detail"]
    series = client.post(
        "/api/series",
        json={
            "document_id": document_id,
            "flow_id": "elevenlabs_dialog_v0",
            "minutes_per_episode": 3,
        },
    )
    assert series.status_code == 422


def test_a_series_holds_at_its_plan_then_writes_every_episode_in_order(
    client: TestClient, document_id: str, provider: SeriesStub
) -> None:
    created = client.post(
        "/api/series",
        json={
            "document_id": document_id,
            "minutes_per_episode": 3,
            "episodes": 2,
            "name": "  Klimaserie  ",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Klimaserie"
    series_id = created.json()["id"]

    held = _wait(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]
    assert [e["status"] for e in held["episodes"]] == ["pending", "pending"]
    assert held["plan_run_status"] == "completed"
    listed = {r["id"] for r in client.get("/api/runs").json()}
    assert held["plan_run_id"] not in listed, "the planner run is not a run to review"

    # Planned again with three episodes, then released.
    replanned = client.post(f"/api/series/{series_id}/replan", json={"episodes": 3})
    assert replanned.status_code == 200, replanned.text
    held = _wait(client, series_id, {"planned", "failed"})
    assert len(held["plan"]["episodes"]) == 3

    calls_before = len(provider.calls)
    approved = client.post(f"/api/series/{series_id}/approve")
    assert approved.status_code == 200, approved.text
    done = _wait(client, series_id, {"completed", "failed"})
    assert done["status"] == "completed", done["error"]
    assert [e["status"] for e in done["episodes"]] == ["completed"] * 3
    assert done["progress"] == {"outlined": 3, "written": 3, "episodes": 3}
    assert done["checks"]["S1"]["status"] in {"pass", "warn"}
    assert done["checks"]["S1"]["assigned_share"] >= 0.9

    # All outlines come before any script: the first beat call follows the last outline.
    new_calls = provider.calls[calls_before:]
    kinds = [
        "outline"
        if "You plan the running order" in (c.system or "")
        else "beat"
        if "You write one beat" in (c.system or "")
        else "other"
        for c in new_calls
    ]
    assert kinds.index("beat") > max(i for i, k in enumerate(kinds) if k == "outline")

    # Episode 2 sees the other outlines and episode 1's text; episode 1 sees no earlier text.
    first, second = _beat_calls(provider, 1), _beat_calls(provider, 2)
    assert first and second
    assert all("Earlier episodes in full" not in m for m in first)
    assert "Running order of episode 3" in first[0] and "Running order of episode 1" in second[0]
    assert "[Episode 1:" in second[0] and "Earlier episodes in full" in second[0]
    earlier = second[0].split("Earlier episodes in full", 1)[1].split("Series rules:", 1)[0]
    assert not re.search(r"^\[b\d+\]", earlier, re.MULTILINE), "no passage headers in it"

    assert done["name"] == "Klimaserie"
    episode = done["episodes"][1]
    assert episode["name"] == "Klimaserie Teil 2"
    run = client.get(f"/api/runs/{episode['run_id']}").json()
    assert run["series_id"] == series_id and run["episode_index"] == 2
    assert run["name"] == "Klimaserie Teil 2"
    assert {g["id"] for g in run["gates"]} >= {"G1", "G4", "G8"}
    with session_scope() as session:
        rows = session.query(RunNode).filter(RunNode.run_id == episode["run_id"]).all()
        assert {r.node_name for r in rows} >= {"select", "outline", "script"}
        stored = session.get(Run, episode["run_id"])
        assert stored is not None
        assert stored.total_cost_usd == pytest.approx(sum(r.cost_usd for r in rows))
        assert stored.context_hash

    graph = client.get(f"/api/series/{series_id}/graph").json()
    assert [n["name"] for n in graph["plan"]["nodes"]] == [
        "ingest",
        "content_budget",
        "series_plan",
    ]
    assert len(graph["episodes"]) == 3
    script = next(n for n in graph["episodes"][1]["graph"]["nodes"] if n["name"] == "script")
    assert script["status"] == "ok"
    assert "series_context" in {p["key"] for p in script["consumes"]}

    # Every run's events reached the series channel, tagged with their episode.
    _subscriber, replay = bus.subscribe(series_channel(series_id))
    bus.unsubscribe(series_channel(series_id), _subscriber)
    tagged = {e.data.get("episode") for e in replay if e.type == "node.started"}
    assert {1, 2, 3} <= tagged
    assert replay[-1].type == "series.completed"

    exported = client.get(f"/api/series/{series_id}/export?format=zip")
    assert exported.status_code == 200
    assert "kalliope-serie-klimaserie.zip" in exported.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    names = archive.namelist()
    assert len([n for n in names if n.startswith("folge-")]) == 3 and "serie.json" in names
    episode_file = next(n for n in names if n.startswith("folge-02"))
    assert "Klimaserie Teil 2" in archive.read(episode_file).decode()


def test_a_failed_episode_resumes_where_it_stopped(
    client: TestClient, document_id: str, provider: SeriesStub
) -> None:
    provider.fail_episode = 2
    try:
        created = client.post(
            "/api/series",
            json={
                "document_id": document_id,
                "minutes_per_episode": 3,
                "episodes": 2,
                "hint": "zweiter Lauf",
            },
        )
        assert created.json()["name"] is None
        series_id = created.json()["id"]
        held = _wait(client, series_id, {"planned", "failed"})
        assert held["status"] == "planned", held["error"]
        approved = client.post(f"/api/series/{series_id}/approve")
        assert approved.status_code == 200, approved.text
        failed = _wait(client, series_id, {"completed", "failed"})
    finally:
        provider.fail_episode = None
    assert failed["status"] == "failed"
    assert failed["error"].startswith("Folge 2:")
    assert [e["status"] for e in failed["episodes"]] == ["completed", "failed"]
    failed_run_id = failed["episodes"][1]["run_id"]
    with session_scope() as session:
        run = session.get(Run, failed_run_id)
        assert run is not None and run.error
        config = dict(run.config_json or {})
        config["verdict"] = "insufficient"
        run.config_json = config

    calls_before = len(provider.calls)
    resumed = client.post(f"/api/series/{series_id}/resume")
    assert resumed.status_code == 200, resumed.text
    done = _wait(client, series_id, {"completed", "failed"})
    assert done["status"] == "completed", done["error"]
    assert done["episodes"][1]["error"] is None
    recovered = client.get(f"/api/runs/{failed_run_id}").json()
    assert recovered["name"] is None
    assert recovered["status"] == "completed"
    assert recovered["error"] is None
    assert recovered["verdict"] is None
    systems = [c.system or "" for c in provider.calls[calls_before:]]
    assert systems and all("You write one beat" in s for s in systems), (
        "only episode 2's script runs again"
    )


def test_a_forced_series_outlines_each_episode_once(
    client: TestClient, document_id: str, provider: SeriesStub
) -> None:
    calls_before = len(provider.calls)
    created = client.post(
        "/api/series",
        json={
            "document_id": document_id,
            "minutes_per_episode": 3,
            "episodes": 2,
            "force": True,
        },
    )
    series_id = created.json()["id"]
    held = _wait(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]
    approved = client.post(f"/api/series/{series_id}/approve")
    assert approved.status_code == 200, approved.text
    done = _wait(client, series_id, {"completed", "failed"})
    assert done["status"] == "completed", done["error"]
    outlines = [
        c for c in provider.calls[calls_before:] if "You plan the running order" in (c.system or "")
    ]
    assert len(outlines) == 2, "the script stage reuses the outline its context was built from"


def _channel_types(series_id: str) -> list[str]:
    channel = series_channel(series_id)
    subscriber, replay = bus.subscribe(channel)
    bus.unsubscribe(channel, subscriber)
    return [event.type for event in replay]


def test_approve_replan_and_resume_drop_the_previous_terminal_event(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new live stream must not replay the terminal event of the run it replaces."""
    monkeypatch.setattr(worker, "submit_series", lambda _series_id: None)
    with session_scope() as session:
        row = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            request_json={
                "episodes": 2,
                "minutes_per_episode": 3,
                "hint": None,
                "approved": False,
            },
            status="planned",
        )
        session.add(row)
        session.flush()
        series_id = row.id
    bus.publish(series_channel(series_id), "series.planned", {"run_id": "old"})
    assert "series.planned" in _channel_types(series_id)

    replanned = client.post(
        f"/api/series/{series_id}/replan",
        json={"episodes": 3, "minutes_per_episode": 4, "hint": "kürzer"},
    )
    assert replanned.status_code == 200, replanned.text
    assert replanned.json()["request"]["minutes_per_episode"] == 4
    assert "series.planned" not in _channel_types(series_id)

    bus.publish(series_channel(series_id), "series.planned", {"run_id": "old"})
    with session_scope() as session:
        row = session.get(Series, series_id)
        assert row is not None
        row.status = "planned"
    approved = client.post(f"/api/series/{series_id}/approve")
    assert approved.status_code == 200, approved.text
    assert "series.planned" not in _channel_types(series_id)

    bus.publish(series_channel(series_id), "series.failed", {"error": "old"})
    with session_scope() as session:
        row = session.get(Series, series_id)
        assert row is not None
        row.status = "failed"
    resumed = client.post(f"/api/series/{series_id}/resume")
    assert resumed.status_code == 200, resumed.text
    assert "series.failed" not in _channel_types(series_id)


def test_a_failed_replan_keeps_the_previous_plan(
    client: TestClient, document_id: str, provider: SeriesStub
) -> None:
    created = client.post(
        "/api/series",
        json={"document_id": document_id, "minutes_per_episode": 3, "episodes": 2},
    )
    series_id = created.json()["id"]
    held = _wait(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]
    title = held["plan"]["title"]
    episode_count = len(held["plan"]["episodes"])
    previous_minutes = held["request"]["minutes_per_episode"]
    lengths = [episode["target_minutes"] for episode in held["plan"]["episodes"]]

    provider.fail_with = RuntimeError("planner broke")
    try:
        replanned = client.post(
            f"/api/series/{series_id}/replan",
            json={"episodes": 3, "minutes_per_episode": 4, "hint": "kürzer"},
        )
        assert replanned.status_code == 200, replanned.text
        done = _wait(client, series_id, {"planned", "failed"})
    finally:
        provider.fail_with = None

    assert done["status"] == "planned", done
    assert "planner broke" in (done["error"] or "")
    assert done["plan"]["title"] == title
    assert len(done["plan"]["episodes"]) == episode_count
    assert done["request"]["minutes_per_episode"] == previous_minutes
    assert done["request"]["episodes"] == held["request"]["episodes"]
    assert done["request"].get("hint") == held["request"].get("hint")

    approved = client.post(f"/api/series/{series_id}/approve")
    assert approved.status_code == 200, approved.text
    written = _wait(client, series_id, {"completed", "failed"})
    assert written["status"] == "completed", written["error"]
    assert len(written["episodes"]) == episode_count
    for episode, length in zip(written["episodes"], lengths, strict=True):
        run = client.get(f"/api/runs/{episode['run_id']}").json()
        assert run["target_minutes"] == int(length)


def test_an_outlined_episode_graph_keeps_series_context(
    client: TestClient, document_id: str
) -> None:
    with session_scope() as session:
        outlined = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="running",
            config_json={"episode_brief_artifact": "brief-hash", "target_minutes": 3},
            episode_index=1,
        )
        single = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="running",
            config_json={"target_minutes": 3},
        )
        session.add_all([outlined, single])
        session.flush()
        outlined_id, single_id = outlined.id, single.id

    outlined_graph = client.get(f"/api/runs/{outlined_id}/graph")
    assert outlined_graph.status_code == 200, outlined_graph.text
    script = next(node for node in outlined_graph.json()["nodes"] if node["name"] == "script")
    assert "series_context" in {port["key"] for port in script["consumes"]}

    single_graph = client.get(f"/api/runs/{single_id}/graph")
    assert single_graph.status_code == 200, single_graph.text
    single_script = next(node for node in single_graph.json()["nodes"] if node["name"] == "script")
    assert "series_context" not in {port["key"] for port in single_script["consumes"]}
