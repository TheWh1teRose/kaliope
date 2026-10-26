"""Moving runs to the trash and back."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import feedback as feedback_api
from app.api.runs import trash_run
from app.db import session_scope
from app.errors import ProblemException
from app.main import create_app
from app.models import Document, Run, RunNode, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.series import trash_series
from app.worker import worker

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


def _series(
    document_id: str, status: str = "completed", episodes: int = 2
) -> tuple[str, list[str]]:
    """A series with its planner run and ``episodes`` finished episode runs."""
    with session_scope() as session:
        series = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            status=status,
            request_json={"approved": True, "minutes_per_episode": 10},
        )
        session.add(series)
        session.flush()
        series_id = series.id
    run_ids = [_run(document_id, series_id=series_id, episode_index=0)]
    for index in range(1, episodes + 1):
        run_ids.append(_run(document_id, series_id=series_id, episode_index=index))
    return series_id, run_ids


def _listed_series(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/series", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def _trash_marks(series_id: str) -> tuple[object, set[object]]:
    with session_scope() as session:
        series = session.get(Series, series_id)
        assert series is not None
        runs = session.scalars(select(Run).where(Run.series_id == series_id)).all()
        return series.trashed_at, {run.trashed_at for run in runs}


def test_trashing_an_episode_trashes_its_whole_series(client: TestClient, document_id: str) -> None:
    series_id, (planner, first, second) = _series(document_id)
    assert first in _listed(client) and second in _listed(client)

    trashed = client.post(f"/api/runs/{first}/trash")
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["trashed_at"]

    # The series, its planner and every episode carry the same mark.
    series_mark, run_marks = _trash_marks(series_id)
    assert series_mark is not None and run_marks == {series_mark}
    assert first not in _listed(client) and second not in _listed(client)
    assert series_id not in _listed_series(client)
    # The trash lists the series once, not its episodes.
    assert series_id in _listed_series(client, trashed=True)
    assert not {planner, first, second} & set(_listed(client, trashed=True))

    # Nothing is removed: the episode and the series are still reachable.
    assert client.get(f"/api/runs/{second}").json()["trashed_at"]
    detail = client.get(f"/api/series/{series_id}")
    assert detail.status_code == 200
    assert detail.json()["trashed_at"]

    # Restoring one episode brings the whole series back.
    restored = client.post(f"/api/runs/{second}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["trashed_at"] is None
    assert _trash_marks(series_id) == (None, {None})
    assert first in _listed(client) and second in _listed(client)
    assert series_id in _listed_series(client)
    assert series_id not in _listed_series(client, trashed=True)


def test_a_series_goes_to_the_trash_and_back_as_one(client: TestClient, document_id: str) -> None:
    series_id, run_ids = _series(document_id, status="stopped")
    trashed = client.post(f"/api/series/{series_id}/trash")
    assert trashed.status_code == 200, trashed.text
    mark = trashed.json()["trashed_at"]
    assert mark
    assert _trash_marks(series_id)[1] != {None}
    assert not set(run_ids) & set(_listed(client))

    again = client.post(f"/api/series/{series_id}/trash")
    assert again.status_code == 200
    assert again.json()["trashed_at"] == mark
    assert client.post(f"/api/runs/{run_ids[1]}/trash").json()["trashed_at"]

    restored = client.post(f"/api/series/{series_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["trashed_at"] is None
    assert _trash_marks(series_id) == (None, {None})
    assert set(run_ids[1:]) <= set(_listed(client))
    assert client.post(f"/api/series/{series_id}/restore").status_code == 200


def test_the_trash_lists_the_latest_trashed_series_first(
    client: TestClient, document_id: str
) -> None:
    first, _ = _series(document_id)
    second, _ = _series(document_id)
    client.post(f"/api/series/{second}/trash")
    client.post(f"/api/series/{first}/trash")
    listed = [sid for sid in _listed_series(client, trashed=True) if sid in {first, second}]
    assert listed == [first, second]


@pytest.mark.parametrize("status", ["queued", "planning", "outlining", "writing"])
def test_a_series_in_progress_cannot_be_trashed(
    client: TestClient, document_id: str, status: str
) -> None:
    series_id, run_ids = _series(document_id, status=status)
    for url in (f"/api/series/{series_id}/trash", f"/api/runs/{run_ids[1]}/trash"):
        response = client.post(url)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Stop the series, or wait for it to finish, before moving it to the trash."
        )
    assert _trash_marks(series_id) == (None, {None})
    assert series_id in _listed_series(client)


def test_a_series_with_a_running_episode_or_job_cannot_be_trashed(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    series_id, run_ids = _series(document_id, status="failed")
    with session_scope() as session:
        run = session.get(Run, run_ids[2])
        assert run is not None
        run.status = "running"
    assert client.post(f"/api/series/{series_id}/trash").status_code == 409
    assert _trash_marks(series_id) == (None, {None})

    with session_scope() as session:
        run = session.get(Run, run_ids[2])
        assert run is not None
        run.status = "failed"
    monkeypatch.setattr(worker, "series_active", lambda checked: checked == series_id)
    assert client.post(f"/api/series/{series_id}/trash").status_code == 409
    assert _trash_marks(series_id) == (None, {None})


def test_a_stopped_series_with_waiting_episodes_can_be_trashed(
    client: TestClient, document_id: str
) -> None:
    # A stopped or failed series leaves the episodes it had not reached queued.
    series_id, run_ids = _series(document_id, status="stopped")
    with session_scope() as session:
        run = session.get(Run, run_ids[2])
        assert run is not None
        run.status = "queued"
    response = client.post(f"/api/series/{series_id}/trash")
    assert response.status_code == 200, response.text
    assert response.json()["trashed_at"]


def test_a_trashed_series_cannot_be_continued(client: TestClient, document_id: str) -> None:
    series_id, run_ids = _series(document_id, status="planned")
    assert client.post(f"/api/series/{series_id}/trash").status_code == 200
    for action, body in (("approve", None), ("resume", None), ("replan", {"episodes": 3})):
        response = client.post(f"/api/series/{series_id}/{action}", json=body)
        assert response.status_code == 409, (action, response.text)
        assert response.json()["detail"] == "Restore the series before starting it again."

    with pytest.raises(ProblemException) as caught:
        worker.submit_series(series_id)
    assert caught.value.status_code == 409
    with pytest.raises(ProblemException):
        worker.submit_run(run_ids[1])

    # Even a job already handed to the worker leaves the series where it is.
    worker.execute_series(series_id)
    detail = client.get(f"/api/series/{series_id}").json()
    assert detail["status"] == "planned"
    assert detail["trashed_at"]
    assert detail["active"] is False


def test_a_series_trashed_while_resuming_is_not_queued(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    series_id, _ = _series(document_id, status="stopped")
    submitted: list[str] = []

    def trash_meanwhile(checked: str) -> bool:
        with session_scope() as session:
            assert trash_series(session, checked)
        return False

    monkeypatch.setattr(worker, "series_active", trash_meanwhile)
    monkeypatch.setattr(worker, "submit_series", lambda claimed: submitted.append(claimed))
    response = client.post(f"/api/series/{series_id}/resume")
    monkeypatch.undo()
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Restore the series before starting it again."
    assert submitted == []
    detail = client.get(f"/api/series/{series_id}").json()
    assert detail["status"] == "stopped"
    assert detail["trashed_at"]


def test_trashing_an_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.post("/api/runs/nope/trash").status_code == 404
    assert client.post("/api/runs/nope/restore").status_code == 404


def test_a_trashed_paused_run_cannot_be_started(client: TestClient, document_id: str) -> None:
    run_id = _run(
        document_id,
        status="paused",
        manifest_json={
            "pause": {
                "node": "human_feedback",
                "payload": {"subject": "script", "instructions": "Weiter"},
                "bag_hashes": {},
                "draft_notes": [],
            }
        },
    )
    trashed = client.post(f"/api/runs/{run_id}/trash")
    assert trashed.status_code == 200, trashed.text
    before = client.get(f"/api/runs/{run_id}").json()
    assert before["trashed_at"]

    submitted = client.post(f"/api/runs/{run_id}/feedback/submit", json={"notes": []})
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["detail"] == "Restore the run before starting it again."

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "paused"
    assert detail["trashed_at"] == before["trashed_at"]
    assert "submitted_hash" not in detail["manifest"]["pause"]
    assert detail["manifest"]["pause"]["draft_notes"] == []
    assert detail["active"] is False
    assert run_id not in _listed(client)
    assert run_id in _listed(client, trashed=True)

    with pytest.raises(ProblemException) as caught:
        worker.submit_run(run_id)
    assert caught.value.status_code == 409
    assert caught.value.detail == "Restore the run before starting it again."

    worker.execute_run(run_id)
    after = client.get(f"/api/runs/{run_id}").json()
    assert after["status"] == "paused"
    assert after["trashed_at"] == before["trashed_at"]
    assert after["active"] is False


def test_a_claimed_run_cannot_be_trashed(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _run(
        document_id,
        status="paused",
        manifest_json={
            "pause": {
                "node": "human_feedback",
                "payload": {"subject": "script", "instructions": "Weiter"},
                "bag_hashes": {},
                "draft_notes": [],
            }
        },
    )
    real_compile = feedback_api.compile_submission
    queued: list[str] = []

    def trash_while_compiling(*args: object, **kwargs: object) -> object:
        with session_scope() as session:
            run = session.get(Run, run_id)
            assert run is not None and run.status == "queued" and run.trashed_at is None
            with pytest.raises(ProblemException) as caught:
                trash_run(run_id, session)
            assert caught.value.status_code == 409
            assert caught.value.detail == (
                "Stop the run, or wait for it to finish, before moving it to the trash."
            )
        return real_compile(*args, **kwargs)

    monkeypatch.setattr(feedback_api, "compile_submission", trash_while_compiling)
    monkeypatch.setattr(worker, "submit_run", lambda claimed: queued.append(claimed))
    submitted = client.post(f"/api/runs/{run_id}/feedback/submit", json={"notes": []})
    assert submitted.status_code == 200, submitted.text
    assert queued == [run_id]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "queued"
    assert detail["trashed_at"] is None
    assert detail["manifest"]["pause"]["submitted_hash"]
    assert run_id not in _listed(client, trashed=True)

    refused = client.post(f"/api/runs/{run_id}/trash")
    assert refused.status_code == 409, refused.text
    after = client.get(f"/api/runs/{run_id}").json()
    assert after["status"] == "queued"
    assert after["trashed_at"] is None


def test_a_failed_submit_releases_the_claim(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _run(
        document_id,
        status="paused",
        manifest_json={
            "pause": {
                "node": "human_feedback",
                "payload": {"subject": "script", "instructions": "Weiter"},
                "bag_hashes": {},
                "draft_notes": [],
            }
        },
    )

    def fail_compile(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad notes")

    monkeypatch.setattr(feedback_api, "compile_submission", fail_compile)
    submitted = client.post(f"/api/runs/{run_id}/feedback/submit", json={"notes": []})
    assert submitted.status_code == 422, submitted.text

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "paused"
    assert detail["trashed_at"] is None
    assert "submitted_hash" not in detail["manifest"]["pause"]
    assert run_id in _listed(client)


def test_a_stop_during_claim_does_not_submit_the_pause(
    client: TestClient, document_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _run(
        document_id,
        status="paused",
        manifest_json={
            "pause": {
                "node": "human_feedback",
                "payload": {"subject": "script", "instructions": "Weiter"},
                "bag_hashes": {},
                "draft_notes": [],
            }
        },
    )
    real_compile = feedback_api.compile_submission
    started: list[str] = []

    def stop_while_compiling(*args: object, **kwargs: object) -> object:
        with session_scope() as session:
            user_id = session.scalars(select(User.id).where(User.email == EMAIL)).one()
            run = session.get(Run, run_id)
            assert run is not None and run.status == "queued" and run.trashed_at is None
        status, _outcome = worker.stop_run(run_id, user_id)
        assert status == "stopped"
        return real_compile(*args, **kwargs)

    monkeypatch.setattr(feedback_api, "compile_submission", stop_while_compiling)
    monkeypatch.setattr(worker, "submit_run", lambda claimed: started.append(claimed))
    submitted = client.post(f"/api/runs/{run_id}/feedback/submit", json={"notes": []})
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["detail"] == "run status is 'stopped', not paused"
    assert started == []

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "stopped"
    assert detail["trashed_at"] is None
    assert "submitted_hash" not in detail["manifest"]["pause"]
    assert detail["manifest"]["pause"]["draft_notes"] == []
    assert [node["node_name"] for node in detail["nodes"]] == ["script"]
