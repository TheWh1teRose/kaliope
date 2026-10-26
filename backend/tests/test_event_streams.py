"""Live-update streams give their request slot and their DB connection back.

Cloud Run serves a handful of requests at once, and each open stream is one of
them. A stream must therefore hold no pooled connection while it waits, end
when its client goes away or its lifetime is up, and stop beating once nothing
drives the job. A reopened stream picks up after the last event it saw.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import events
from app.db import get_engine, session_scope
from app.events import bus
from app.main import create_app
from app.models import Document, Run, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.worker import worker

PASSWORD = "stream-password-1"
EMAIL = "stream@kalliope.test"
LOGIN = {"email": EMAIL, "password": PASSWORD}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        with session_scope() as session:
            session.add(User(email=EMAIL, name="Strom", password_hash=hash_password(PASSWORD)))
        response = test_client.post("/api/auth/login", json=LOGIN)
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="module")
def server(client: TestClient) -> Iterator[str]:
    """The app behind a real HTTP server: the test client cannot leave a stream open."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=port, lifespan="off", log_level="warning"
    )
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not running.started:
        assert time.monotonic() < deadline, "the test server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(10)


@pytest.fixture()
def http(server: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server, timeout=10) as signed_in:
        assert signed_in.post("/api/auth/login", json=LOGIN).status_code == 200
        yield signed_in


@pytest.fixture(scope="module")
def document_id(client: TestClient) -> str:
    with session_scope() as session:
        document = Document(
            filename="strom.pdf", sha256="ef" * 32, parse_status="parsed", parse_version=1
        )
        session.add(document)
        session.flush()
        return document.id


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


def _series(document_id: str, status: str) -> str:
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
        return series.id


@pytest.fixture()
def driven(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Job ids the worker pretends to be running."""
    jobs: set[str] = set()
    real = worker.job_active
    monkeypatch.setattr(worker, "job_active", lambda job_id: job_id in jobs or real(job_id))
    return jobs


def _wait_for(predicate: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.02)


def _sse(client: TestClient, path: str, **kwargs: Any) -> str:
    with client.stream("GET", path, **kwargs) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def test_open_streams_hold_no_pooled_connection(
    http: httpx.Client, document_id: str, driven: set[str]
) -> None:
    """More streams than the pool has connections, and other requests still go through."""
    run_id = _run(document_id, "running")
    driven.add(run_id)
    streams = []
    try:
        for _ in range(20):
            context = http.stream("GET", f"/api/runs/{run_id}/events")
            response = context.__enter__()
            assert response.status_code == 200
            # Keep the line reader: dropping it would close the stream.
            lines = response.iter_lines()
            streams.append((context, lines))
            assert next(lines) == ": connected"
        _wait_for(lambda: bus.subscribers(run_id) == 20)
        assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
        started = time.monotonic()
        assert http.get("/api/health").status_code == 200
        assert http.get(f"/api/runs/{run_id}").status_code == 200
        assert time.monotonic() - started < 2
    finally:
        for context, _lines in streams:
            context.__exit__(None, None, None)
    _wait_for(lambda: bus.subscribers(run_id) == 0)


def test_a_client_disconnect_frees_the_stream(
    http: httpx.Client, document_id: str, driven: set[str]
) -> None:
    series_id = _series(document_id, "planning")
    driven.add(series_id)
    channel = f"series:{series_id}"
    with http.stream("GET", f"/api/series/{series_id}/events") as response:
        lines = response.iter_lines()
        assert next(lines) == ": connected"
        _wait_for(lambda: bus.subscribers(channel) == 1)
    _wait_for(lambda: bus.subscribers(channel) == 0, seconds=2)


def test_the_lifetime_cap_closes_a_stream_and_asks_for_a_new_one(
    http: httpx.Client, document_id: str, driven: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "STREAM_LIFETIME_SECONDS", 0.5)
    run_id = _run(document_id, "running")
    driven.add(run_id)
    started = time.monotonic()
    with http.stream("GET", f"/api/runs/{run_id}/events") as response:
        body = "".join(response.iter_text())
    assert time.monotonic() - started < 5
    assert body.rstrip().endswith('data: {"type": "stream.renew"}')
    _wait_for(lambda: bus.subscribers(run_id) == 0)


def test_a_stream_nothing_drives_goes_idle(client: TestClient, document_id: str) -> None:
    """A run or series left mid-way with no worker job closes its stream at once."""
    run_id = _run(document_id, "running")
    assert "event: stream.idle" in _sse(client, f"/api/runs/{run_id}/events")
    series_id = _series(document_id, "writing")
    assert "event: stream.idle" in _sse(client, f"/api/series/{series_id}/events")
    assert bus.subscribers(run_id) == 0


def test_a_quiet_stream_goes_idle_once_its_job_ends(
    client: TestClient, document_id: str, driven: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.2)
    run_id = _run(document_id, "running")
    driven.add(run_id)
    timer = threading.Timer(0.5, driven.discard, args=(run_id,))
    timer.start()
    body = _sse(client, f"/api/runs/{run_id}/events")
    timer.join()
    assert ": heartbeat" in body
    assert body.rstrip().endswith('data: {"type": "stream.idle"}')


def test_an_episode_of_a_driven_series_stays_live(
    client: TestClient, document_id: str, driven: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An episode waits for its series; that is not idle."""
    monkeypatch.setattr(events, "STREAM_LIFETIME_SECONDS", 0.3)
    series_id = _series(document_id, "writing")
    run_id = _run(document_id, "queued", series_id=series_id, episode_index=2)
    driven.add(series_id)
    body = _sse(client, f"/api/runs/{run_id}/events")
    assert "stream.idle" not in body
    assert "event: stream.renew" in body


def test_a_reopened_stream_starts_after_the_last_event_it_saw(
    client: TestClient, document_id: str
) -> None:
    run_id = _run(document_id, "completed")
    for step in ("first", "second", "third"):
        bus.publish(run_id, "node.progress", {"message": step})
    ids = [
        int(line.removeprefix("id: "))
        for line in _sse(client, f"/api/runs/{run_id}/events").splitlines()
        if line.startswith("id: ")
    ]
    assert len(ids) == 3 and ids == sorted(ids)

    reopened = _sse(client, f"/api/runs/{run_id}/events", params={"after": ids[1]})
    assert "first" not in reopened and "second" not in reopened
    assert "third" in reopened
    assert "event: run.completed" in reopened

    # The browser's own reconnect sends the id as a header instead.
    native = _sse(client, f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(ids[0])})
    assert "first" not in native and "second" in native and "third" in native
