"""Folders in the runs overview: standalone runs and whole series, filed and renamed."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine, session_scope
from app.main import create_app
from app.models import Document, Run, RunNode, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password

PASSWORD = "folder-password-1"
EMAIL = "run-folders@kalliope.test"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Ablage", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="module")
def document_id(client: TestClient) -> str:
    with session_scope() as session:
        document = Document(
            filename="ablage.pdf",
            sha256="ef" * 32,
            title="Ablage",
            parse_status="parsed",
            parse_version=1,
        )
        session.add(document)
        session.flush()
        return document.id


def _run(document_id: str, **extra: object) -> str:
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="completed",
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


def _series(document_id: str, episodes: int = 2) -> tuple[str, list[str]]:
    """A finished series with its planner run and ``episodes`` episode runs."""
    with session_scope() as session:
        series = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            status="completed",
            request_json={"approved": True, "minutes_per_episode": 10},
        )
        session.add(series)
        session.flush()
        series_id = series.id
    run_ids = [_run(document_id, series_id=series_id, episode_index=0)]
    for index in range(1, episodes + 1):
        run_ids.append(_run(document_id, series_id=series_id, episode_index=index))
    return series_id, run_ids


def _folder(client: TestClient, name: str, parent_id: str | None = None) -> dict[str, object]:
    response = client.post("/api/run-folders", json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def _runs(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/runs", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def _series_ids(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/series", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def _tree(client: TestClient) -> dict[str, dict[str, object]]:
    return {row["id"]: row for row in client.get("/api/run-folders").json()}


def test_folders_nest_and_refuse_a_sibling_with_the_same_name(client: TestClient) -> None:
    kurs = _folder(client, "Kurs B")
    modul = _folder(client, "Modul 1", str(kurs["id"]))
    assert modul["depth"] == 1
    assert modul["path"] == ["Kurs B", "Modul 1"]

    assert client.post("/api/run-folders", json={"name": "kurs b"}).status_code == 409
    renamed = client.patch(f"/api/run-folders/{modul['id']}", json={"name": "Modul 2"})
    assert renamed.status_code == 200
    assert renamed.json()["path"] == ["Kurs B", "Modul 2"]

    refused = client.post(f"/api/run-folders/{kurs['id']}/move", json={"parent_id": modul["id"]})
    assert refused.status_code == 422
    # The documents' tree is a separate one.
    assert kurs["id"] not in {row["id"] for row in client.get("/api/folders").json()}


def test_a_run_is_filed_listed_by_folder_and_counted(client: TestClient, document_id: str) -> None:
    before = client.get("/api/run-folders/summary").json()
    run_id = _run(document_id)
    folder = _folder(client, "Entwürfe")
    after = client.get("/api/run-folders/summary").json()
    assert after == {"total": before["total"] + 1, "root": before["root"] + 1}
    assert client.get(f"/api/runs/{run_id}").json()["folder_id"] is None

    moved = client.post(f"/api/runs/{run_id}/move", json={"folder_id": folder["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["folder_id"] == folder["id"]

    assert _runs(client, folder_id=folder["id"]) == [run_id]
    assert run_id not in _runs(client, folder_id="root")
    assert run_id in _runs(client)
    assert _tree(client)[str(folder["id"])]["run_count"] == 1
    assert client.get("/api/run-folders/summary").json() == {
        "total": before["total"] + 1,
        "root": before["root"],
    }

    back = client.post(f"/api/runs/{run_id}/move", json={"folder_id": None})
    assert back.json()["folder_id"] is None
    assert run_id in _runs(client, folder_id="root")


def test_moving_into_an_unknown_folder_is_a_404(client: TestClient, document_id: str) -> None:
    run_id = _run(document_id)
    response = client.post(f"/api/runs/{run_id}/move", json={"folder_id": "nope"})
    assert response.status_code == 404
    assert client.get("/api/runs", params={"folder_id": "nope"}).status_code == 404


def test_a_series_moves_as_one_unit_with_its_episodes(client: TestClient, document_id: str) -> None:
    series_id, (planner, first, second) = _series(document_id)
    folder = _folder(client, "Serien")

    # Moving one episode files the whole series; the runs keep no folder of their own.
    moved = client.post(f"/api/runs/{first}/move", json={"folder_id": folder["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["folder_id"] == folder["id"]
    with session_scope() as session:
        assert session.get(Series, series_id).folder_id == folder["id"]  # type: ignore[union-attr]
        own = session.scalars(select(Run.folder_id).where(Run.series_id == series_id)).all()
        assert set(own) == {None}

    assert client.get(f"/api/series/{series_id}").json()["folder_id"] == folder["id"]
    assert client.get(f"/api/runs/{second}").json()["folder_id"] == folder["id"]
    assert _series_ids(client, folder_id=folder["id"]) == [series_id]
    assert sorted(_runs(client, folder_id=folder["id"])) == sorted([first, second])
    assert planner not in _runs(client, folder_id=folder["id"])
    assert not {first, second} & set(_runs(client, folder_id="root"))
    assert series_id not in _series_ids(client, folder_id="root")
    # A series counts once, not once per episode.
    assert _tree(client)[str(folder["id"])]["run_count"] == 1

    out = client.post(f"/api/series/{series_id}/move", json={"folder_id": "root"})
    assert out.status_code == 200, out.text
    assert out.json()["folder_id"] is None
    assert {first, second} <= set(_runs(client, folder_id="root"))


def test_a_series_is_renamed_from_the_overview(client: TestClient, document_id: str) -> None:
    series_id, (_, first, _) = _series(document_id)
    renamed = client.patch(f"/api/series/{series_id}", json={"name": "Klimapolitik"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Klimapolitik"
    assert client.get(f"/api/runs/{first}").json()["name"] == "Klimapolitik Teil 1"

    # A run's own name falls back to the default title when cleared.
    run_id = _run(document_id)
    assert client.patch(f"/api/runs/{run_id}", json={"name": "Folge X"}).json()["name"] == "Folge X"
    assert client.patch(f"/api/runs/{run_id}", json={"name": "  "}).json()["name"] is None


def test_the_trash_keeps_the_folder_and_leaves_the_counts(
    client: TestClient, document_id: str
) -> None:
    folder = _folder(client, "Papierkorbtest")
    run_id = _run(document_id)
    series_id, (_, first, _) = _series(document_id)
    client.post(f"/api/runs/{run_id}/move", json={"folder_id": folder["id"]})
    client.post(f"/api/series/{series_id}/move", json={"folder_id": folder["id"]})
    assert _tree(client)[str(folder["id"])]["run_count"] == 2

    assert client.post(f"/api/runs/{run_id}/trash").status_code == 200
    assert client.post(f"/api/series/{series_id}/trash").status_code == 200
    assert _tree(client)[str(folder["id"])]["run_count"] == 0
    assert not _runs(client, folder_id=folder["id"])
    assert not _series_ids(client, folder_id=folder["id"])
    assert run_id in _runs(client, trashed=True)

    # Restoring brings each back into its folder.
    client.post(f"/api/runs/{run_id}/restore")
    client.post(f"/api/series/{series_id}/restore")
    assert set(_runs(client, folder_id=folder["id"])) >= {run_id, first}
    assert _tree(client)[str(folder["id"])]["run_count"] == 2


def test_deleting_a_folder_moves_runs_and_series_up(client: TestClient, document_id: str) -> None:
    parent = _folder(client, "Oben")
    child = _folder(client, "Unten", str(parent["id"]))
    run_id = _run(document_id)
    trashed_id = _run(document_id)
    series_id, _ = _series(document_id)
    for target in (f"/api/runs/{run_id}", f"/api/runs/{trashed_id}", f"/api/series/{series_id}"):
        client.post(f"{target}/move", json={"folder_id": child["id"]})
    client.post(f"/api/runs/{trashed_id}/trash")
    assert _tree(client)[str(parent["id"])]["total_run_count"] == 2

    blocked = client.delete(f"/api/run-folders/{parent['id']}")
    assert blocked.status_code == 409
    assert "2 run(s)" in blocked.json()["detail"]

    deleted = client.delete(f"/api/run-folders/{child['id']}", params={"cascade": True})
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_folders": 1, "moved_runs": 2, "moved_to": parent["id"]}
    assert client.get(f"/api/runs/{run_id}").json()["folder_id"] == parent["id"]
    assert client.get(f"/api/series/{series_id}").json()["folder_id"] == parent["id"]
    # A run in the trash moves up too, so restoring it lands in a folder that exists.
    assert client.get(f"/api/runs/{trashed_id}").json()["folder_id"] == parent["id"]

    client.delete(f"/api/run-folders/{parent['id']}", params={"cascade": True})
    assert client.get(f"/api/runs/{run_id}").json()["folder_id"] is None
    assert client.get(f"/api/series/{series_id}").json()["folder_id"] is None


@contextmanager
def _data_dir(path: Path) -> Iterator[None]:
    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=path)  # type: ignore[arg-type]
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        yield
    finally:
        set_settings(previous)
        reset_engine()


def test_the_migration_leaves_existing_runs_in_no_folder(tmp_path: Path) -> None:
    from alembic import command
    from app.migrations import _alembic_config

    with _data_dir(tmp_path / "folders"):
        config = _alembic_config()
        command.upgrade(config, "3c7f2a9e6d14")
        with get_engine().begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO documents (id, filename, sha256, uploaded_at, parse_version,"
                    " parse_status, title_edited) VALUES ('d1', 'a.pdf', 'x', '2026-10-01',"
                    " 1, 'parsed', 0)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO series (id, document_id, flow_id, flow_version, plan_flow_id,"
                    " request_json, format_spec_json, audience_spec_json, status, checks_json,"
                    " created_at) VALUES ('s1', 'd1', 'baseline_v0', '1.0', 'series_plan_v0',"
                    " '{}', '{}', '{}', 'completed', '{}', '2026-10-01')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO runs (id, document_id, name, flow_id, flow_version,"
                    " flow_revision, config_json, format_spec_json, audience_spec_json, status,"
                    " created_at, total_cost_usd, series_id, episode_index) VALUES ('r1', 'd1',"
                    " 'Folge', 'baseline_v0', '1.0', 0, '{}', '{}', '{}', 'completed',"
                    " '2026-10-01', 0.5, 's1', 1)"
                )
            )

        command.upgrade(config, "head")
        with get_engine().connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT runs.folder_id, series.folder_id, runs.name, runs.status FROM runs"
                    " JOIN series ON series.id = runs.series_id"
                )
            ).one()
            assert tuple(row) == (None, None, "Folge", "completed")
            assert "run_folders" in sa.inspect(connection).get_table_names()

        command.downgrade(config, "3c7f2a9e6d14")
        with get_engine().connect() as connection:
            inspector = sa.inspect(connection)
            assert "folder_id" not in {c["name"] for c in inspector.get_columns("runs")}
            assert "folder_id" not in {c["name"] for c in inspector.get_columns("series")}
            assert "run_folders" not in inspector.get_table_names()
            assert connection.execute(sa.text("SELECT name FROM runs")).scalar() == "Folge"

        command.upgrade(config, "head")
