"""The Sammlung: folders for collected experiment outputs, one list across experiments."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

import app.experiments  # noqa: F401 - registers every experiment
from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine, session_scope
from app.experiments.registry import get_experiment
from app.experiments.search import item_search_text
from app.llm import registry
from app.main import create_app
from app.models import ExperimentOutput, User
from app.security import hash_password
from tests.support import StubProvider

PASSWORD = "sammlung-password-1"
EMAIL = "sammlung@kalliope.test"

VS_OUTPUT: dict[str, Any] = {
    "variant": "standard",
    "k": 2,
    "vs": {
        "drafts": [
            {
                "item": "vs:0",
                "source": "vs",
                "segments": [
                    {
                        "speaker": "Mia",
                        "text": "Atme tief ein, den Sauerstoff schenkt dir ein Baum.",
                        "kind": "claim",
                        "citations": [{"block_id": "b15", "quote": "Quellzitat Chlorophyll"}],
                    }
                ],
            },
            {
                "item": "vs:1",
                "source": "vs",
                "segments": [
                    {"speaker": "Jonas", "text": "Pflanzen frühstücken Licht.", "kind": "claim"}
                ],
            },
        ],
        "warnings": ["a warning nobody searches for"],
    },
    "baseline": {"drafts": [], "warnings": [], "error": None},
}


# ------------------------------------------------------------- search text


def test_search_text_is_the_items_own_words() -> None:
    first = item_search_text(VS_OUTPUT, "vs:0", None)
    assert "Sauerstoff" in first
    assert "frühstücken" not in first, "a sibling draft must not match"
    assert "Quellzitat" not in first, "source quotes are not the answer"
    assert "warning" not in first

    payload = {"payload": {"segments": [{"speaker": "A", "text": "Hallo Welt"}]}, "text": "{}"}
    assert item_search_text(payload, "main", "{}") == "A\nHallo Welt"
    assert item_search_text({"payload": None, "text": "roh"}, "main", "roh") == "roh"
    assert item_search_text(None, "main", "nur Text") == "nur Text"


# --------------------------------------------------------------- migration


@contextmanager
def _data_dir(path: Path) -> Iterator[Settings]:
    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=path)  # type: ignore[arg-type]
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        yield settings
    finally:
        set_settings(previous)
        reset_engine()


def test_migration_adds_folders_and_backfills_search_text(tmp_path: Path) -> None:
    from alembic import command
    from app.migrations import _alembic_config

    with _data_dir(tmp_path / "migrate"):
        config = _alembic_config()
        command.upgrade(config, "e5a1c7d93b40")
        with get_engine().begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO experiment_outputs (id, experiment_key, item, output_json,"
                    " meta_json, setup_json, created_at) VALUES ('o1', 'verbalized_sampling',"
                    " 'vs:1', :output, '{}', '{}', '2026-10-01 10:00:00')"
                ),
                {"output": json.dumps(VS_OUTPUT)},
            )

        command.upgrade(config, "head")
        with get_engine().connect() as connection:
            tables = sa.inspect(connection).get_table_names()
            assert "output_folders" in tables
            row = connection.execute(
                sa.text("SELECT folder_id, status, note, search_text FROM experiment_outputs")
            ).one()
        assert row[0] is None and row[1] is None and row[2] is None
        assert "frühstücken" in row[3] and "Sauerstoff" not in row[3]

        command.downgrade(config, "e5a1c7d93b40")
        with get_engine().connect() as connection:
            inspector = sa.inspect(connection)
            assert "output_folders" not in inspector.get_table_names()
            columns = {c["name"] for c in inspector.get_columns("experiment_outputs")}
            assert "folder_id" not in columns and "search_text" not in columns
            assert connection.execute(sa.text("SELECT id FROM experiment_outputs")).scalar() == "o1"

        command.upgrade(config, "head")


# ------------------------------------------------------------------- API


@pytest.fixture(scope="module")
def signed_in() -> Iterator[TestClient]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    try:
        with TestClient(create_app()) as client:
            with session_scope() as session:
                session.add(
                    User(
                        email=EMAIL,
                        name="Sammlung",
                        password_hash=hash_password(PASSWORD),
                        role="reviewer",
                    )
                )
            response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert response.status_code == 200, response.text
            yield client
    finally:
        registry.reset_providers()


def _run(client: TestClient) -> str:
    setup = get_experiment("direct_style").defaults().model_dump(mode="json")
    created = client.post("/api/experiments/direct_style/runs", json={"setup": setup})
    assert created.status_code == 201, created.text
    run_id = str(created.json()["id"])
    for _ in range(200):
        body = client.get(f"/api/experiments/runs/{run_id}").json()
        if body["status"] in {"completed", "failed"}:
            assert body["status"] == "completed", body["error"]
            return run_id
        time.sleep(0.05)
    raise AssertionError(f"experiment run {run_id} did not finish")


def _folder(client: TestClient, name: str, parent_id: str | None = None) -> dict[str, Any]:
    created = client.post("/api/experiment-folders", json={"name": name, "parent_id": parent_id})
    assert created.status_code == 201, created.text
    return dict(created.json())


def test_output_folders_follow_the_document_folder_rules(signed_in: TestClient) -> None:
    ton = _folder(signed_in, "Ton & Stil")
    opus = _folder(signed_in, "Opus gegen Sonnet", ton["id"])
    assert opus["depth"] == 1 and opus["path"] == ["Ton & Stil", "Opus gegen Sonnet"]

    assert signed_in.post("/api/experiment-folders", json={"name": "ton & stil"}).status_code == 409
    renamed = signed_in.patch(f"/api/experiment-folders/{opus['id']}", json={"name": "Opus"})
    assert renamed.json()["name"] == "Opus"

    cycle = signed_in.post(
        f"/api/experiment-folders/{ton['id']}/move", json={"parent_id": opus["id"]}
    )
    assert cycle.status_code == 422 and "subfolder" in cycle.json()["detail"]

    deep = ton
    for level in range(7):
        deep = _folder(signed_in, f"Ebene {level}", deep["id"])
    too_deep = signed_in.post(
        "/api/experiment-folders", json={"name": "zu tief", "parent_id": deep["id"]}
    )
    assert too_deep.status_code == 422

    # A document folder is a different tree: its ids mean nothing here.
    document_folder = signed_in.post("/api/folders", json={"name": "Dokumente-Ordner"}).json()
    assert (
        signed_in.post(
            "/api/experiment-folders", json={"name": "x", "parent_id": document_folder["id"]}
        ).status_code
        == 404
    )
    assert not any(
        f["id"] == document_folder["id"] for f in signed_in.get("/api/experiment-folders").json()
    )


def test_collect_into_a_folder_list_move_and_delete(signed_in: TestClient) -> None:
    serie = _folder(signed_in, "Serie Oktober")
    teil = _folder(signed_in, "Teil 1", serie["id"])

    first_run = _run(signed_in)
    missing = signed_in.post(f"/api/experiments/runs/{first_run}/save", json={"folder_id": "nope"})
    assert missing.status_code == 404

    filed = signed_in.post(
        f"/api/experiments/runs/{first_run}/save", json={"folder_id": teil["id"]}
    ).json()
    assert filed["folder_id"] == teil["id"]
    assert filed["folder_path"] == ["Serie Oktober", "Teil 1"]
    assert filed["created_by"] == EMAIL

    # Collecting the same item again never moves it.
    again = signed_in.post(f"/api/experiments/runs/{first_run}/save", json={}).json()
    assert again["id"] == filed["id"] and again["folder_id"] == teil["id"]

    loose = signed_in.post(f"/api/experiments/runs/{_run(signed_in)}/save", json={}).json()
    assert loose["folder_id"] is None and loose["folder_path"] == []

    # The per-experiment list shows the folder too.
    listed = signed_in.get("/api/experiments/direct_style/outputs").json()
    assert next(o for o in listed if o["id"] == filed["id"])["folder_path"][-1] == "Teil 1"

    page = signed_in.get("/api/experiments/outputs", params={"folder_id": serie["id"]}).json()
    assert [o["id"] for o in page["items"]] == [filed["id"]], "subfolders are included"
    assert page["total"] == 1
    only_here = signed_in.get(
        "/api/experiments/outputs", params={"folder_id": serie["id"], "include_sub": False}
    ).json()
    assert only_here["items"] == [] and only_here["total"] == 0

    unfiled = signed_in.get("/api/experiments/outputs", params={"folder_id": "root"}).json()
    assert loose["id"] in [o["id"] for o in unfiled["items"]]
    assert filed["id"] not in [o["id"] for o in unfiled["items"]]
    assert unfiled["root_count"] == unfiled["total"]
    assert unfiled["all_count"] >= unfiled["root_count"] + 1

    everything = signed_in.get(
        "/api/experiments/outputs", params={"experiment": "direct_style", "limit": 200}
    ).json()
    ids = [o["id"] for o in everything["items"]]
    assert ids.index(loose["id"]) < ids.index(filed["id"]), "newest first"
    oldest = signed_in.get(
        "/api/experiments/outputs",
        params={"experiment": "direct_style", "sort": "old", "limit": 200},
    ).json()
    assert [o["id"] for o in oldest["items"]] == list(reversed(ids))
    paged = signed_in.get(
        "/api/experiments/outputs", params={"experiment": "direct_style", "limit": 1, "offset": 1}
    ).json()
    assert [o["id"] for o in paged["items"]] == ids[1:2] and paged["total"] == len(ids)
    assert (
        signed_in.get("/api/experiments/outputs", params={"experiment": "none_such"}).json()[
            "total"
        ]
        == 0
    )
    unknown = signed_in.get("/api/experiments/outputs", params={"folder_id": "nope"})
    assert unknown.status_code == 404

    # Move many at once, then back to "Ohne Ordner".
    moved = signed_in.post(
        "/api/experiments/outputs/move",
        json={"ids": [filed["id"], loose["id"]], "folder_id": serie["id"]},
    )
    assert moved.status_code == 200 and moved.json() == {"moved": 2, "folder_id": serie["id"]}
    tree = {f["id"]: f for f in signed_in.get("/api/experiment-folders").json()}
    assert tree[serie["id"]]["output_count"] == 2 and tree[serie["id"]]["total_output_count"] == 2
    assert tree[teil["id"]]["output_count"] == 0

    assert (
        signed_in.post(
            "/api/experiments/outputs/move", json={"ids": [loose["id"], "nope"], "folder_id": None}
        ).status_code
        == 404
    )
    assert (
        signed_in.post(
            "/api/experiments/outputs/move", json={"ids": [loose["id"]], "folder_id": "nope"}
        ).status_code
        == 404
    )
    back = signed_in.post(
        "/api/experiments/outputs/move", json={"ids": [loose["id"]], "folder_id": "root"}
    )
    assert back.json()["folder_id"] is None

    # Deleting a folder moves its outputs up and never deletes one.
    blocked = signed_in.delete(f"/api/experiment-folders/{serie['id']}")
    assert blocked.status_code == 409 and "cascade" in blocked.json()["detail"]
    deleted = signed_in.delete(f"/api/experiment-folders/{serie['id']}", params={"cascade": True})
    assert deleted.json() == {"deleted_folders": 2, "moved_outputs": 1, "moved_to": None}
    survivor = signed_in.get("/api/experiments/outputs", params={"folder_id": "root"}).json()
    assert filed["id"] in [o["id"] for o in survivor["items"]]

    # Deleting an output that sat in a folder only lowers the count.
    kept = _folder(signed_in, "Behalten")
    signed_in.post(
        "/api/experiments/outputs/move", json={"ids": [filed["id"]], "folder_id": kept["id"]}
    )
    assert signed_in.delete(f"/api/experiments/outputs/{filed['id']}").status_code == 204
    tree = {f["id"]: f for f in signed_in.get("/api/experiment-folders").json()}
    assert tree[kept["id"]]["output_count"] == 0


def test_search_finds_an_items_own_text_label_and_note(signed_in: TestClient) -> None:
    with session_scope() as session:
        for item in ("vs:0", "vs:1"):
            session.add(
                ExperimentOutput(
                    experiment_key="verbalized_sampling",
                    item=item,
                    label="100% Probe_1" if item == "vs:1" else None,
                    output_json=VS_OUTPUT,
                    meta_json={},
                    setup_json={},
                    search_text=item_search_text(VS_OUTPUT, item, None),
                    note="Notiz mit Zauberwort" if item == "vs:0" else None,
                )
            )

    def found(q: str) -> list[str]:
        page = signed_in.get(
            "/api/experiments/outputs", params={"q": q, "experiment": "verbalized_sampling"}
        ).json()
        return [o["item"] for o in page["items"]]

    assert found("sauerstoff") == ["vs:0"]
    assert found("frühstücken") == ["vs:1"]
    assert found("Quellzitat") == []
    assert found("zauberwort") == ["vs:0"]
    assert found("100%") == ["vs:1"]
    assert found("e_1") == ["vs:1"]
    assert found("%") == ["vs:1"], "a % in the query matches only itself"
