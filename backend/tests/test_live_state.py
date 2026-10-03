"""Live run state: the node at work is told apart from the waiting ones, and a
resumed run's event stream does not end on the previous execution's pause.

Regressions for what the run page showed before: every node not yet started
reported ``running``, and after notes were submitted the stream replayed the
old ``run.paused`` and closed at once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.api.runs import _node_status
from app.events import EventBus
from app.llm.base import LLMClient
from app.models import RunNode
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, bootstrap_nodes
from app.pipeline.framework.runner import FlowRunner, NodeRecord
from tests.series_support import AUDIENCE, BASELINE_NODES, FORMAT, learning_document
from tests.support import StubProvider


def test_a_node_without_a_row_is_waiting_not_running() -> None:
    finished = RunNode(node_name="ingest", finished_at=datetime.now(UTC), cache_hit=True)
    started = RunNode(node_name="select", finished_at=None, cache_hit=False)
    assert _node_status(finished, reached=False, run_status="running") == "cached"
    assert _node_status(started, reached=False, run_status="running") == "running"
    assert _node_status(None, reached=False, run_status="running") == "pending"
    assert _node_status(None, reached=False, run_status="queued") == "pending"
    assert _node_status(None, reached=True, run_status="running") == "blocked"
    assert _node_status(started, reached=False, run_status="paused") == "paused"


def test_the_runner_records_a_node_when_it_starts(tmp_path: Path) -> None:
    bootstrap_nodes()
    seen: list[tuple[str, bool]] = []

    def on_node(record: NodeRecord) -> None:
        seen.append((record.name, record.finished_at is None))

    provider = StubProvider()
    runner = FlowRunner(
        artifacts=ArtifactStore(tmp_path),
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        on_node=on_node,
    )
    result = runner.execute(
        Flow(id="live", version="1", nodes=BASELINE_NODES),
        "live-run",
        {
            "parsed": learning_document(),
            "target_minutes": 5,
            "format_spec": FORMAT,
            "audience_spec": AUDIENCE,
        },
    )
    assert result.status == "completed"
    names = [n.node for n in BASELINE_NODES]
    assert seen == [pair for name in names for pair in ((name, True), (name, False))]


def test_clearing_a_run_drops_the_previous_execution_from_the_replay() -> None:
    bus = EventBus()
    bus.publish("r1", "run.started")
    bus.publish("r1", "run.paused", {"node": "human_feedback"})
    bus.clear("r1")  # what a resume does before the run executes again
    bus.publish("r1", "run.queued")
    bus.publish("r1", "run.started")
    _subscriber, replay = bus.subscribe("r1")
    assert [e.type for e in replay] == ["run.queued", "run.started"]


def test_submitting_notes_starts_a_fresh_event_history(tmp_path: Path) -> None:
    """After a pause, the resumed run's stream must not replay the old pause."""
    import hashlib
    import time

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db import session_scope
    from app.events import bus
    from app.llm import registry
    from app.main import create_app
    from app.models import Document, Run, User
    from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
    from app.security import hash_password
    from app.worker import worker
    from tests.support import write_learning_pdf

    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    try:
        with TestClient(create_app()) as client:
            with session_scope() as session:
                session.add(
                    User(
                        email="live@kalliope.test",
                        name="Live",
                        password_hash=hash_password("pw-live-1"),
                    )
                )
            login = client.post(
                "/api/auth/login", json={"email": "live@kalliope.test", "password": "pw-live-1"}
            )
            assert login.status_code == 200, login.text

            settings = get_settings()
            settings.ensure_dirs()
            payload = write_learning_pdf(tmp_path / "live.pdf").read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
            with session_scope() as session:
                document = Document(filename="live.pdf", sha256=digest, parse_status="pending")
                session.add(document)
                session.flush()
                document_id = document.id
            worker.parse_document(document_id)
            with session_scope() as session:
                run = Run(
                    document_id=document_id,
                    flow_id="objectives_v0",
                    flow_version="1.0",
                    config_json={"target_minutes": 5},
                    format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
                    audience_spec_json=DEFAULT_AUDIENCE.model_copy(
                        update={"desired_outcome": "Den Stoff erklären können."}
                    ).model_dump(mode="json"),
                    status="queued",
                )
                session.add(run)
                session.flush()
                run_id = run.id
            worker.execute_run(run_id)
            paused = client.get(f"/api/runs/{run_id}").json()
            assert paused["status"] == "paused", paused["error"]
            _sub, before = bus.subscribe(run_id)
            bus.unsubscribe(run_id, _sub)
            assert before[-1].type == "run.paused"

            submitted = client.post(f"/api/runs/{run_id}/feedback/submit", json={"notes": []})
            assert submitted.status_code == 200, submitted.text
            _sub, after = bus.subscribe(run_id)
            bus.unsubscribe(run_id, _sub)
            assert "run.paused" not in [e.type for e in after]

            deadline = time.monotonic() + 60
            while client.get(f"/api/runs/{run_id}").json()["status"] not in {"completed", "failed"}:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            _sub, final = bus.subscribe(run_id)
            bus.unsubscribe(run_id, _sub)
            types = [e.type for e in final]
            assert types[0] == "run.queued" and types[-1] == "run.completed"
            assert "run.paused" not in types
    finally:
        registry.reset_providers()
