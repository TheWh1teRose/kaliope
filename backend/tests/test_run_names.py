"""Optional display names for a run and a series."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.runs import _to_markdown
from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine, session_scope
from app.main import create_app
from app.models import Document, Run, Series, User
from app.naming import NAME_MAX_LENGTH, clean_name
from app.schemas.pipeline import Script, Segment
from app.security import hash_password
from app.series import apply_episode_names, episode_run_name

PASSWORD = "names-password-1"
EMAIL = "names@kalliope.test"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Namen", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="module")
def document_id(client: TestClient) -> str:
    with session_scope() as session:
        document = Document(
            filename="klima.pdf",
            sha256="ab" * 32,
            title="Klima",
            parse_status="parsed",
            parse_version=1,
        )
        session.add(document)
        session.flush()
        return document.id


def _script() -> Script:
    return Script(segments=[Segment(id="s", speaker="A", text="Hallo", kind="claim", beat_id="b")])


def _parsed() -> SimpleNamespace:
    return SimpleNamespace(document_id="d1", parse_version=1, language="de")


def test_episode_names_follow_the_series_name() -> None:
    assert episode_run_name(None, 1) is None
    assert episode_run_name("", 1) is None
    assert episode_run_name("Klimaserie", 1) == "Klimaserie Teil 1"
    assert episode_run_name("Klimaserie", 3) == "Klimaserie Teil 3"


def test_a_blank_name_is_no_name_and_unicode_is_kept() -> None:
    assert clean_name(None) is None
    assert clean_name("") is None
    assert clean_name("  \t ") is None
    assert clean_name("  Klima – Übung 日本語  ") == "Klima – Übung 日本語"
    with pytest.raises(Exception) as raised:
        clean_name("x" * (NAME_MAX_LENGTH + 1))
    assert getattr(raised.value, "status_code", None) == 422


def test_create_run_stores_a_trimmed_name_and_lists_it(
    client: TestClient, document_id: str
) -> None:
    unnamed = client.post("/api/runs", json={"document_id": document_id})
    assert unnamed.status_code == 201, unnamed.text
    assert unnamed.json()["name"] is None
    assert unnamed.json()["document_title"] == "Klima"

    named = client.post(
        "/api/runs",
        json={"document_id": document_id, "name": "  Kurzfassung  "},
    )
    assert named.status_code == 201, named.text
    body = named.json()
    assert body["name"] == "Kurzfassung"
    listed = {row["id"]: row for row in client.get("/api/runs").json()}
    assert listed[body["id"]]["name"] == "Kurzfassung"
    assert listed[unnamed.json()["id"]]["name"] is None

    renamed = client.patch(f"/api/runs/{body['id']}", json={"name": "  Zweite Fassung  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Zweite Fassung"
    cleared = client.patch(f"/api/runs/{body['id']}", json={"name": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["name"] is None


@pytest.mark.parametrize("name", ["", "   ", None])
def test_an_empty_run_name_is_stored_as_unset(
    client: TestClient, document_id: str, name: str | None
) -> None:
    created = client.post("/api/runs", json={"document_id": document_id, "name": name})
    assert created.status_code == 201, created.text
    assert created.json()["name"] is None


def test_a_run_name_rejects_more_than_the_limit(client: TestClient, document_id: str) -> None:
    too_long = client.post(
        "/api/runs", json={"document_id": document_id, "name": "ä" * (NAME_MAX_LENGTH + 1)}
    )
    assert too_long.status_code == 422
    ok = client.post("/api/runs", json={"document_id": document_id, "name": "ü" * NAME_MAX_LENGTH})
    assert ok.status_code == 201, ok.text
    assert ok.json()["name"] == "ü" * NAME_MAX_LENGTH


def test_create_series_stores_a_trimmed_name(client: TestClient, document_id: str) -> None:
    created = client.post(
        "/api/series",
        json={
            "document_id": document_id,
            "minutes_per_episode": 3,
            "episodes": 2,
            "name": "  Äöü Serie  ",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Äöü Serie"
    assert client.get(f"/api/series/{created.json()['id']}").json()["name"] == "Äöü Serie"


def test_renaming_a_series_retitles_its_episodes(client: TestClient, document_id: str) -> None:
    with session_scope() as session:
        series = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            name="Alt",
            status="planned",
        )
        session.add(series)
        session.flush()
        series_id = series.id
        for index in (1, 2):
            session.add(
                Run(
                    document_id=document_id,
                    flow_id="baseline_v0",
                    flow_version="1.0",
                    status="completed",
                    series_id=series_id,
                    episode_index=index,
                    name=episode_run_name(series.name, index),
                )
            )

    renamed = client.patch(f"/api/series/{series_id}", json={"name": "Neue Serie"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Neue Serie"
    runs = {
        row["episode_index"]: row
        for row in client.get("/api/runs").json()
        if row["series_id"] == series_id
    }
    assert runs[1]["name"] == "Neue Serie Teil 1"
    assert runs[2]["name"] == "Neue Serie Teil 2"

    cleared = client.patch(f"/api/series/{series_id}", json={"name": ""})
    assert cleared.json()["name"] is None
    runs = {
        row["episode_index"]: row
        for row in client.get("/api/runs").json()
        if row["series_id"] == series_id
    }
    assert runs[1]["name"] is None and runs[2]["name"] is None

    with session_scope() as session:
        series = session.get(Series, series_id)
        assert series is not None
        series.name = "Nochmal"
        apply_episode_names(session, series)
        stored = {
            run.episode_index: run.name
            for run in session.query(Run).filter(Run.series_id == series_id)
        }
    assert stored[1] == "Nochmal Teil 1"


def test_export_heading_uses_the_name_and_falls_back_to_the_document() -> None:
    document = Document(filename="klima.pdf", sha256="c" * 64, title="Klima")
    script = _script()
    parsed = _parsed()
    unnamed = Run(document_id="d", flow_id="baseline_v0", flow_version="1.0", total_cost_usd=0)
    assert _to_markdown(unnamed, document, parsed, script, {}).startswith("# Klima\n")  # type: ignore[arg-type]

    named = Run(
        document_id="d",
        flow_id="baseline_v0",
        flow_version="1.0",
        total_cost_usd=0,
        name="Kurzfassung",
    )
    assert _to_markdown(named, document, parsed, script, {}).startswith("# Kurzfassung\n")  # type: ignore[arg-type]

    bare = Document(filename="klima.pdf", sha256="d" * 64, title=None)
    assert _to_markdown(unnamed, bare, parsed, script, {}).startswith("# klima.pdf\n")  # type: ignore[arg-type]
    assert _to_markdown(unnamed, None, parsed, script, {}).startswith("# Skript\n")  # type: ignore[arg-type]


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


def test_the_migration_adds_nullable_names_from_the_current_head(tmp_path: Path) -> None:
    from alembic import command
    from app.migrations import _alembic_config

    with _data_dir(tmp_path / "names"):
        config = _alembic_config()
        command.upgrade(config, "a3f9c2e17b54")
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
                    " '{}', '{}', '{}', 'planned', '{}', '2026-10-01')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO runs (id, document_id, flow_id, flow_version, flow_revision,"
                    " config_json, format_spec_json, audience_spec_json, status, created_at,"
                    " total_cost_usd, series_id, episode_index) VALUES ('r1', 'd1', 'baseline_v0',"
                    " '1.0', 0, '{}', '{}', '{}', 'completed', '2026-10-01', 0.5, 's1', 1)"
                )
            )

        command.upgrade(config, "head")
        with get_engine().connect() as connection:
            run_name, series_name, status = connection.execute(
                sa.text(
                    "SELECT runs.name, series.name, runs.status FROM runs"
                    " JOIN series ON series.id = runs.series_id"
                )
            ).one()
        assert (run_name, series_name, status) == (None, None, "completed")

        command.downgrade(config, "a3f9c2e17b54")
        with get_engine().connect() as connection:
            inspector = sa.inspect(connection)
            assert "name" not in {c["name"] for c in inspector.get_columns("runs")}
            assert "name" not in {c["name"] for c in inspector.get_columns("series")}
            assert connection.execute(sa.text("SELECT id FROM runs")).scalar() == "r1"

        command.upgrade(config, "head")
