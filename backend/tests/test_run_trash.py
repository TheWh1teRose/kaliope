"""Moving runs to the trash and back."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import create_app
from app.models import Document, Run, RunNode, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password

PASSWORD = "trash-password-1"
EMAIL = "trash@kalliope.test"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Papierkorb", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="module")
def document_id(client: TestClient) -> str:
    with session_scope() as session:
        document = Document(
            filename="muell.pdf",
            sha256="cd" * 32,
            title="Müll",
            parse_status="parsed",
            parse_version=1,
        )
        session.add(document)
        session.flush()
        return document.id


def _run(document_id: str, status: str = "completed", **extra: object) -> str:
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status=status,
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            **extra,
        )
        session.add(run)
        session.flush()
        session.add(
            RunNode(run_id=run.id, node_name="script", node_version="0", cache_key=run.id * 4)
        )
        return run.id


def _listed(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/runs", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def test_a_trashed_run_leaves_the_list_and_restores_unchanged(
    client: TestClient, document_id: str
) -> None:
    run_id = _run(document_id, name="Wegwerfen")
    other = _run(document_id)
    assert run_id in _listed(client)

    trashed = client.post(f"/api/runs/{run_id}/trash")
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["trashed_at"]
    assert run_id not in _listed(client)
    assert run_id not in _listed(client, document_id=document_id)
    assert other in _listed(client)
    assert run_id in _listed(client, trashed=True)
    assert other not in _listed(client, trashed=True)

    # Nothing is removed: the run and what it produced are still reachable.
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["trashed_at"]
    assert [node["node_name"] for node in detail.json()["nodes"]] == ["script"]

    again = client.post(f"/api/runs/{run_id}/trash")
    assert again.status_code == 200
    assert again.json()["trashed_at"] == detail.json()["trashed_at"]

    restored = client.post(f"/api/runs/{run_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["trashed_at"] is None
    assert restored.json()["name"] == "Wegwerfen"
    assert run_id in _listed(client)
    assert run_id not in _listed(client, trashed=True)
    assert client.post(f"/api/runs/{run_id}/restore").status_code == 200


def test_the_trash_lists_the_latest_trashed_run_first(client: TestClient, document_id: str) -> None:
    first, second = _run(document_id), _run(document_id)
    client.post(f"/api/runs/{second}/trash")
    client.post(f"/api/runs/{first}/trash")
    listed = [run_id for run_id in _listed(client, trashed=True) if run_id in {first, second}]
    assert listed == [first, second]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_run_in_progress_cannot_be_trashed(
    client: TestClient, document_id: str, status: str
) -> None:
    run_id = _run(document_id, status=status)
    response = client.post(f"/api/runs/{run_id}/trash")
    assert response.status_code == 409
    assert run_id in _listed(client)


def test_an_episode_is_trashed_only_with_its_series(client: TestClient, document_id: str) -> None:
    with session_scope() as session:
        series = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            status="completed",
        )
        session.add(series)
        session.flush()
        series_id = series.id
    run_id = _run(document_id, series_id=series_id, episode_index=1)
    response = client.post(f"/api/runs/{run_id}/trash")
    assert response.status_code == 409
    assert run_id in _listed(client)


def test_trashing_an_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.post("/api/runs/nope/trash").status_code == 404
    assert client.post("/api/runs/nope/restore").status_code == 404
