"""The data download: a run, or a whole series, as one zip a model can read node by node."""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.llm import registry
from app.main import create_app
from app.models import Document, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf
from tests.test_series_api import _long_pdf

PASSWORD = "bundle-password-1"
EMAIL = "bundle-reviewer@kalliope.test"
COMMENT = "Dieser Übergang ist privat."


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    registry.register_provider("anthropic", StubProvider())
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(
                User(email=EMAIL, name="Prüferin Privat", password_hash=hash_password(PASSWORD))
            )
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client
    registry.reset_providers()


def _document(payload: bytes, filename: str) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename=filename, sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        identifier = document.id
    worker.parse_document(identifier)
    return identifier


@pytest.fixture(scope="module")
def run_id(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    source = write_learning_pdf(tmp_path_factory.mktemp("bundle") / "bundle.pdf")
    document_id = _document(source.read_bytes(), "bundle.pdf")
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            name="Datenlauf",
            config_json={"target_minutes": 15},
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        identifier = run.id
    worker.execute_run(identifier)
    with session_scope() as session:
        stored = session.get(Run, identifier)
        assert stored is not None and stored.status == "completed", stored and stored.error
        # Who stopped or started a run is personal and stays out of the bundle.
        stored.config_json = {**stored.config_json, "stopped_by": "user-secret-id"}
    return identifier


def _zip(response: Any) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def _edit_and_comment(client: TestClient, run_id: str) -> dict[str, Any]:
    client.post(f"/api/runs/{run_id}/review/start")
    segment = client.get(f"/api/runs/{run_id}/script").json()["segments"][0]
    edited = client.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment["id"],
            "action": "edit",
            "reason_code": "CLUMSY_LANGUAGE",
            "text_before": segment["text"],
            "text_after": "Von der Prüferin neu gefasst.",
        },
    )
    assert edited.status_code == 201, edited.text
    commented = client.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment["id"],
            "action": "comment",
            "note": COMMENT,
        },
    )
    assert commented.status_code == 201, commented.text
    return segment


def test_a_run_bundle_walks_the_graph_node_by_node(client: TestClient, run_id: str) -> None:
    segment = _edit_and_comment(client, run_id)

    response = client.get(f"/api/runs/{run_id}/bundle")
    archive = _zip(response)
    assert "kalliope-lauf-datenlauf-daten.zip" in response.headers["content-disposition"]
    names = set(archive.namelist())
    assert {"README.md", "run.json", "run.md", "script.md"} <= names

    data = json.loads(archive.read("run.json"))
    graph = client.get(f"/api/runs/{run_id}/graph").json()
    assert data["run"]["id"] == run_id and data["run"]["status"] == "in_review"
    assert data["run"]["total_cost_usd"] == graph["total_cost_usd"]
    # The nodes are the graph's, in its order, with what the graph shows of each.
    assert [n["name"] for n in data["nodes"]] == [n["name"] for n in graph["nodes"]]
    for exported, shown in zip(data["nodes"], graph["nodes"], strict=True):
        for key in ("status", "model_id", "tokens_in", "tokens_out", "cost_usd", "wall_ms"):
            assert exported[key] == shown[key], (shown["name"], key)
        assert exported["gates"] == shown["findings"]
        assert {p["key"] for p in exported["inputs"]} == {p["key"] for p in shown["consumes"]}

    # Each output is complete in its own file and matches the stored artifact.
    script_node = next(n for n in data["nodes"] if n["name"] == "script")
    assert script_node["output"]["file"] in names
    artifact = client.get(f"/api/runs/{run_id}/artifacts/script").json()
    assert json.loads(archive.read(script_node["output"]["file"])) == artifact
    # An input points at the file of the node that produced it, or at a seed.
    for node in data["nodes"]:
        for port in node["inputs"]:
            assert port["file"] in names, (node["name"], port)
    assert {seed["key"] for seed in data["seeds"]} >= {"format_spec", "document_ref"}

    # Node config as the inspector shows it.
    io_ = client.get(f"/api/runs/{run_id}/nodes/script/io").json()
    assert script_node["config"] == io_["config"]

    # Gates carry their rule and their result.
    assert data["gates"] and all("spec" in g for g in data["gates"])

    # The reviewer's edit is in; their comment, name and e-mail are not.
    first = next(s for s in data["script"]["segments"] if s["id"] == segment["id"])
    assert first["text"] == "Von der Prüferin neu gefasst."
    assert first["edited"] is True and first["original_text"] == segment["text"]
    assert "Von der Prüferin neu gefasst." in archive.read("script.md").decode()
    everything = b"".join(archive.read(name) for name in names).decode()
    for private in (COMMENT, EMAIL, "Prüferin Privat", "user-secret-id"):
        assert private not in everything

    readable = archive.read("run.md").decode()
    assert readable.startswith("# Lauf Datenlauf")
    for node in data["nodes"]:
        assert f". {node['name']} (v{node['version']})" in readable


def test_a_trashed_run_exports_only_after_restore(client: TestClient, run_id: str) -> None:
    assert client.post(f"/api/runs/{run_id}/trash").status_code == 200
    refused = client.get(f"/api/runs/{run_id}/bundle")
    assert refused.status_code == 409
    assert refused.json()["title"] == "Run is in the trash"

    assert client.post(f"/api/runs/{run_id}/restore").status_code == 200
    assert client.get(f"/api/runs/{run_id}/bundle").status_code == 200


def test_an_unknown_run_has_no_bundle(client: TestClient) -> None:
    assert client.get("/api/runs/nope/bundle").status_code == 404
    assert client.get("/api/series/nope/bundle").status_code == 404


def _wait(client: TestClient, series_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        body = client.get(f"/api/series/{series_id}").json()
        if body["status"] in states and not body["active"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"series stayed '{body['status']}'")


def test_a_series_bundle_holds_the_plan_and_every_episode(client: TestClient) -> None:
    document_id = _document(_long_pdf(), "serie.pdf")
    created = client.post(
        "/api/series",
        json={
            "document_id": document_id,
            "minutes_per_episode": 3,
            "episodes": 2,
            "name": "Datenserie",
        },
    )
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]
    held = _wait(client, series_id, {"planned", "failed"})
    assert held["status"] == "planned", held["error"]

    # A planned series already exports its plan and its planner run.
    archive = _zip(client.get(f"/api/series/{series_id}/bundle"))
    assert "plan/run.json" in archive.namelist()
    assert not any(name.startswith("episode-") for name in archive.namelist())

    assert client.post(f"/api/series/{series_id}/approve").status_code == 200
    done = _wait(client, series_id, {"completed", "failed"})
    assert done["status"] == "completed", done["error"]

    response = client.get(f"/api/series/{series_id}/bundle")
    archive = _zip(response)
    assert "kalliope-serie-datenserie-daten.zip" in response.headers["content-disposition"]
    names = set(archive.namelist())
    assert {"README.md", "series.json", "series.md", "plan/run.json", "plan/run.md"} <= names

    data = json.loads(archive.read("series.json"))
    assert data["plan"] == done["plan"]
    assert [e["index"] for e in data["episodes"]] == [1, 2]
    for episode in data["episodes"]:
        folder = episode["folder"]
        assert {f"{folder}run.json", f"{folder}run.md", f"{folder}script.md"} <= names
        run = json.loads(archive.read(f"{folder}run.json"))
        assert run["run"]["id"] == episode["run_id"]
        assert run["run"]["episode_index"] == episode["index"]
        assert {"episode_brief", "series_context"} <= {s["key"] for s in run["seeds"]}

    readable = archive.read("series.md").decode()
    for episode in done["plan"]["episodes"]:
        assert f"### Folge {episode['index']}: {episode['title']}" in readable
    for term in done["plan"]["terms"]:
        assert term["term"] in readable

    assert client.post(f"/api/series/{series_id}/trash").status_code == 200
    refused = client.get(f"/api/series/{series_id}/bundle")
    assert refused.status_code == 409
    assert refused.json()["title"] == "Series is in the trash"
    # An episode of a trashed series is trashed with it.
    assert client.get(f"/api/runs/{data['episodes'][0]['run_id']}/bundle").status_code == 409
    assert client.post(f"/api/series/{series_id}/restore").status_code == 200
    assert client.get(f"/api/series/{series_id}/bundle").status_code == 200
