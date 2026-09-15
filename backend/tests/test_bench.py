"""The node testing bench: load real documents, edit the bag, run a short chain."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import create_app
from app.models import Document, User
from app.security import hash_password
from app.worker import worker
from tests.support import write_learning_pdf

PASSWORD = "bench-password-1"
EMAIL = "bench@kalliope.test"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def signed_in(client: TestClient) -> TestClient:
    with session_scope() as session:
        session.add(
            User(
                email=EMAIL,
                name="Bench",
                password_hash=hash_password(PASSWORD),
                role="admin",
            )
        )
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture(scope="module")
def parsed_document_id(signed_in: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("bench") / "bench.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)

    with session_scope() as session:
        document = Document(filename="bench.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        document_id = document.id

    worker.parse_document(document_id)
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document is not None and document.parse_status == "parsed", document.parse_error
    return document_id


def _wait(signed_in: TestClient, bench_id: str) -> dict[str, object]:
    body: dict[str, object] = {}
    for _ in range(80):
        body = signed_in.get(f"/api/bench/runs/{bench_id}").json()
        if body["status"] in {"completed", "failed", "paused"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"bench run {bench_id} did not finish: {body}")


def test_bench_requires_authentication() -> None:
    with TestClient(create_app()) as anonymous:
        response = anonymous.get("/api/bench/catalogue")
    assert response.status_code == 401


def test_catalogue_lists_every_value_key(signed_in: TestClient) -> None:
    body = signed_in.get("/api/bench/catalogue").json()
    keys = {item["key"] for item in body["values"]}
    assert keys >= {
        "document_ref",
        "target_minutes",
        "format_spec",
        "audience_spec",
        "parsed",
        "budget",
        "selection",
        "outline",
        "script",
        "objectives",
    }
    parsed = next(item for item in body["values"] if item["key"] == "parsed")
    assert "ingest" in parsed["produced_by"]
    assert "content_budget" in parsed["consumed_by"]
    minutes = next(item for item in body["values"] if item["key"] == "target_minutes")
    assert minutes["seed"] is True
    assert minutes["template"] == 15


def test_validate_accepts_a_single_node_when_the_bag_has_its_inputs(signed_in: TestClient) -> None:
    missing = signed_in.post(
        "/api/bench/validate",
        json={"nodes": [{"node": "content_budget", "config": {}}], "seed_keys": []},
    ).json()
    assert missing["valid"] is False
    assert any("parsed" in error for error in missing["errors"])

    ok = signed_in.post(
        "/api/bench/validate",
        json={
            "nodes": [{"node": "content_budget", "config": {}}],
            "seed_keys": ["parsed", "target_minutes"],
        },
    ).json()
    assert ok["valid"] is True
    assert ok["errors"] == []


def test_validate_does_not_require_a_full_pipeline(signed_in: TestClient) -> None:
    body = signed_in.post(
        "/api/bench/validate",
        json={
            "nodes": [
                {"node": "select", "config": {}},
                {"node": "outline", "config": {}},
            ],
            "seed_keys": ["parsed", "budget", "audience_spec", "format_spec"],
        },
    ).json()
    assert body["valid"] is True


def test_load_document_fills_parsed_and_seeds(
    signed_in: TestClient, parsed_document_id: str
) -> None:
    body = signed_in.post(
        "/api/bench/load",
        json={"document_id": parsed_document_id, "include_payload": False},
    ).json()
    keys = {item["key"] for item in body["values"]}
    assert keys >= {"document_ref", "parsed", "format_spec", "audience_spec", "target_minutes"}
    parsed = next(item for item in body["values"] if item["key"] == "parsed")
    assert parsed["available"] is True
    assert parsed["source"] == "document"
    assert parsed["artifact_hash"]
    assert parsed["summary"]["blocks"]["count"] > 0


def test_execute_content_budget_against_a_real_document(
    signed_in: TestClient, parsed_document_id: str
) -> None:
    loaded = signed_in.post(
        "/api/bench/load",
        json={"document_id": parsed_document_id, "include_payload": True},
    ).json()
    seeds = {
        item["key"]: (
            {"payload": item["payload"]}
            if item.get("payload") is not None
            else {"artifact_hash": item["artifact_hash"]}
        )
        for item in loaded["values"]
        if item["key"] in {"parsed", "target_minutes"}
    }
    created = signed_in.post(
        "/api/bench/runs",
        json={
            "document_id": parsed_document_id,
            "nodes": [{"node": "content_budget", "config": {"min_compression": 2.5}}],
            "seeds": seeds,
        },
    )
    assert created.status_code == 201, created.text
    body = _wait(signed_in, created.json()["id"])
    assert body["status"] == "completed", body.get("error")
    assert body["nodes"] == ["content_budget"]
    assert body["records"][0]["status"] in {"ok", "cached"}

    budget = next(item for item in body["values"] if item["key"] == "budget")
    full = signed_in.get(f"/api/bench/artifacts/{budget['artifact_hash']}").json()
    assert full["verdict"] in {"ok", "clamped"}
    assert full["requested_minutes"] == 15
    assert full["narratable_words"] > 0
    assert body["llm_traces"] == []


def test_edited_target_minutes_reach_the_node(
    signed_in: TestClient, parsed_document_id: str
) -> None:
    loaded = signed_in.post(
        "/api/bench/load",
        json={"document_id": parsed_document_id, "include_payload": True},
    ).json()
    parsed = next(item for item in loaded["values"] if item["key"] == "parsed")
    created = signed_in.post(
        "/api/bench/runs",
        json={
            "document_id": parsed_document_id,
            "nodes": [{"node": "content_budget", "config": {}}],
            "seeds": {
                "parsed": {"artifact_hash": parsed["artifact_hash"]},
                "target_minutes": {"payload": 8},
            },
        },
    )
    assert created.status_code == 201, created.text
    body = _wait(signed_in, created.json()["id"])
    assert body["status"] == "completed", body.get("error")
    budget = next(item for item in body["values"] if item["key"] == "budget")
    full = signed_in.get(f"/api/bench/artifacts/{budget['artifact_hash']}").json()
    assert full["requested_minutes"] == 8


def test_unknown_node_is_refused(signed_in: TestClient) -> None:
    response = signed_in.post(
        "/api/bench/runs",
        json={"nodes": [{"node": "does_not_exist", "config": {}}], "seeds": {}},
    )
    assert response.status_code == 422


def test_bench_runs_do_not_appear_in_production_runs(
    signed_in: TestClient, parsed_document_id: str
) -> None:
    loaded = signed_in.post(
        "/api/bench/load",
        json={"document_id": parsed_document_id, "include_payload": True},
    ).json()
    seeds = {
        item["key"]: {"artifact_hash": item["artifact_hash"]}
        for item in loaded["values"]
        if item["key"] in {"parsed", "target_minutes"} and item["artifact_hash"]
    }
    created = signed_in.post(
        "/api/bench/runs",
        json={
            "document_id": parsed_document_id,
            "nodes": [{"node": "content_budget", "config": {}}],
            "seeds": seeds,
        },
    )
    assert created.status_code == 201, created.text
    bench_id = created.json()["id"]
    _wait(signed_in, bench_id)

    production = signed_in.get("/api/runs").json()
    assert all(item["id"] != bench_id for item in production)
    history = signed_in.get("/api/bench/runs").json()
    assert any(item["id"] == bench_id for item in history)


def test_human_feedback_pauses_and_resumes_in_the_bench(signed_in: TestClient) -> None:
    outline = {
        "beats": [
            {
                "id": "beat000",
                "title": "Einstieg",
                "block_ids": ["b000000"],
                "word_budget": 120,
                "summary": "Kurz",
            }
        ]
    }
    created = signed_in.post(
        "/api/bench/runs",
        json={
            "nodes": [{"node": "human_feedback", "config": {"subject": "outline"}}],
            "seeds": {"outline": {"payload": outline}},
        },
    )
    assert created.status_code == 201, created.text
    bench_id = created.json()["id"]
    paused = _wait(signed_in, bench_id)
    assert paused["status"] == "paused"
    assert paused["pause"]["node"] == "human_feedback"
    assert paused["pause"]["subject"] == "outline"

    feedback = signed_in.get(f"/api/bench/runs/{bench_id}/feedback").json()
    assert feedback["subject"] == "outline"
    assert feedback["outline"]["beats"][0]["id"] == "beat000"

    submitted = signed_in.post(
        f"/api/bench/runs/{bench_id}/feedback/submit",
        json={
            "notes": [
                {
                    "text": "Titel schärfen.",
                    "target": {"kind": "beat", "id": "beat000"},
                }
            ]
        },
    )
    assert submitted.status_code == 200, submitted.text
    done = _wait(signed_in, bench_id)
    assert done["status"] == "completed", done.get("error")
    notes = next(item for item in done["values"] if item["key"] == "notes")
    assert notes["summary"]["items"]["count"] == 1
