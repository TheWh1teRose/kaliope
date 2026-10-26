"""What a restart leaves behind, and what the database mount can throw at us.

A new process finds runs and series an earlier one was driving; nothing drives
them now, so startup fails a series and its runs, stops a standalone run, and
a series can be resumed. On the Cloud Storage mount a pooled connection can
point at a file another writer replaced; such a connection is dropped instead
of failing every request it serves. And a startup that migrates must leave the
server's error log switched on.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.config import Settings, get_settings, set_settings
from app.db import reset_engine, session_scope, stale_file, watch_engine
from app.main import create_app
from app.models import Document, Run, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.worker import INTERRUPTED, worker

PASSWORD = "restart-password-1"
EMAIL = "restart@kalliope.test"


@contextmanager
def _data_dir(path: Path) -> Iterator[Settings]:
    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=path, testing=True)
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        yield settings
    finally:
        set_settings(previous)
        reset_engine()


def _run(document_id: str, status: str, **extra: Any) -> str:
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
        return run.id


def test_startup_fails_what_the_previous_process_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _data_dir(tmp_path / "restart"):
        with TestClient(create_app()):
            pass  # migrate the fresh volume
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Neustart", password_hash=hash_password(PASSWORD)))
            document = Document(
                filename="neu.pdf", sha256="ab" * 32, parse_status="parsed", parse_version=1
            )
            session.add(document)
            session.flush()
            document_id = document.id
            series = Series(
                document_id=document_id,
                flow_id="baseline_v0",
                flow_version="1.0",
                plan_flow_id="series_plan_v0",
                status="planning",
                request_json={"approved": True, "minutes_per_episode": 10},
            )
            session.add(series)
            session.flush()
            series_id = series.id
        planner = _run(document_id, "running", series_id=series_id, episode_index=0)
        queued = _run(document_id, "queued")
        done = _run(document_id, "completed")
        paused = _run(document_id, "paused")

        resumed: list[str] = []
        monkeypatch.setattr(worker, "submit_series", resumed.append)
        with TestClient(create_app()) as client:
            with session_scope() as session:
                for run_id, status in ((planner, "failed"), (queued, "stopped")):
                    run = session.get(Run, run_id)
                    assert run is not None
                    assert (run.status, run.error) == (status, INTERRUPTED)
                    assert run.finished_at is not None
                assert session.get(Run, done).status == "completed"  # type: ignore[union-attr]
                assert session.get(Run, paused).status == "paused"  # type: ignore[union-attr]
                row = session.get(Series, series_id)
                assert row is not None and (row.status, row.error) == ("failed", INTERRUPTED)

            login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert login.status_code == 200
            standalone = client.get(f"/api/runs/{queued}")
            assert standalone.status_code == 200
            assert standalone.json()["status"] == "stopped"
            assert standalone.json()["error"] == INTERRUPTED
            episode = client.get(f"/api/runs/{planner}")
            assert episode.status_code == 200
            assert (episode.json()["status"], episode.json()["error"]) == ("failed", INTERRUPTED)
            response = client.post(f"/api/series/{series_id}/resume")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "queued"
            assert resumed == [series_id]


def test_startup_keeps_the_server_error_log_on(tmp_path: Path) -> None:
    """Alembic's logging config must not disable loggers the server already made."""
    server_log = logging.getLogger("uvicorn.error")
    app_log = logging.getLogger("kalliope")
    with _data_dir(tmp_path / "logs"), TestClient(create_app()):
        assert not server_log.disabled
        assert not app_log.disabled


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor, failures: list[str]) -> None:
        self._cursor = cursor
        self._failures = failures

    def execute(self, *args: Any) -> Any:
        if self._failures:
            raise sqlite3.OperationalError(self._failures.pop())
        return self._cursor.execute(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _Connection:
    """A DBAPI connection whose next statement can be made to fail."""

    def __init__(self, path: Path, failures: list[str]) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._failures = failures

    def cursor(self) -> _Cursor:
        return _Cursor(self._connection.cursor(), self._failures)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def test_a_stale_database_file_drops_the_pooled_connections(tmp_path: Path) -> None:
    failures: list[str] = []
    opened: list[_Connection] = []

    def connect() -> _Connection:
        opened.append(_Connection(tmp_path / "stale.db", failures))
        return opened[-1]

    engine = watch_engine(create_engine("sqlite://", creator=connect))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
    assert len(opened) == 1

    failures.append("disk I/O error")
    with pytest.raises(OperationalError) as caught, engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert caught.value.connection_invalidated

    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
    assert len(opened) == 2, "the stale connection was handed out again"

    # An ordinary statement error keeps the connection.
    failures.append("no such table: missing")
    with pytest.raises(OperationalError) as caught, engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert not caught.value.connection_invalidated
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert len(opened) == 2
    engine.dispose()


def test_only_file_errors_count_as_stale() -> None:
    io_error = sqlite3.OperationalError("disk I/O error")
    assert stale_file(io_error)
    assert not stale_file(sqlite3.OperationalError("database is locked"))
    assert not stale_file(ValueError("disk I/O error"))
    assert not stale_file(None)
