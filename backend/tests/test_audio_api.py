"""Audio takes end to end: tag, stop at the price, speak only after approval.

The run is a real baseline run over the stub LLM. ElevenLabs is the stub speech
provider; without it registered the app behaves as if no key were set.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine, session_scope
from app.llm import registry
from app.main import create_app
from app.models import Document, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.speech import registry as speech_registry
from app.worker import worker
from tests.support import StubProvider, StubSpeech, write_learning_pdf

EMAIL = "audio@kalliope.test"
PASSWORD = "audio-password-1"


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def client(provider: StubProvider) -> Iterator[TestClient]:
    speech_registry.reset_provider()
    with TestClient(create_app()) as client:
        with session_scope() as session:
            session.add(
                User(email=EMAIL, name="Audio", password_hash=hash_password(PASSWORD), role="admin")
            )
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client
    speech_registry.reset_provider()


@pytest.fixture(scope="module")
def run_id(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("audio") / "audio.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="audio.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        document_id = document.id
    worker.parse_document(document_id)
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": 15},
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        new_id = run.id
    worker.execute_run(new_id)
    with session_scope() as session:
        done = session.get(Run, new_id)
        assert done is not None and done.status == "completed", done and done.error
    return new_id


def _take(client: TestClient, run_id: str, states: set[str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(300):
        body = client.get(f"/api/runs/{run_id}/audio").json()
        if body["takes"] and body["takes"][0]["status"] in states:
            return body["takes"][0]
        time.sleep(0.05)
    raise AssertionError(f"no take reached {states}: {body}")


def test_without_a_key_the_status_explains_the_setup(client: TestClient) -> None:
    status = client.get("/api/audio/status").json()
    assert status["configured"] is False
    assert "ELEVENLABS_API_KEY" in status["message"]
    assert [flow["id"] for flow in status["flows"]] == ["elevenlabs_dialog_v0"]
    assert client.get("/api/health").json()["speech_configured"] is False

    voices = client.get("/api/audio/voices")
    assert voices.status_code == 409
    assert "ELEVENLABS_API_KEY" in voices.json()["detail"]


def test_voices_are_kept_per_format(client: TestClient) -> None:
    empty = client.get("/api/formats/two_host_dialogue/voices").json()
    assert empty["saved"] is False and empty["speakers"] == ["Moderator", "Expertin"]

    cast = {
        "voices": [
            {"speaker": "Moderator", "voice_id": "v-mod", "voice_name": "Jonas"},
            {"speaker": "Expertin", "voice_id": "v-exp", "voice_name": "Mara"},
        ],
        "model_id": "eleven_v4",
        "stability": 0.5,
    }
    saved = client.put("/api/formats/two_host_dialogue/voices", json=cast)
    assert saved.status_code == 200, saved.text
    again = client.get("/api/formats/two_host_dialogue/voices").json()
    assert again["saved"] is True
    assert [v["voice_id"] for v in again["cast"]["voices"]] == ["v-mod", "v-exp"]
    assert client.get("/api/formats/nope/voices").status_code == 404


def test_a_sample_is_tagged_priced_and_only_spoken_after_approval(
    client: TestClient, run_id: str
) -> None:
    assert client.get(f"/api/runs/{run_id}/audio").json()["takes"] == []
    started = client.post(f"/api/runs/{run_id}/audio", json={})
    assert started.status_code == 201, started.text

    take = _take(client, run_id, {"paused", "failed"})
    assert take["status"] == "paused", take["error"]
    approval = take["approval"]
    assert approval["characters"] > 0 and approval["requests"] >= 1
    assert approval["estimate_usd"] == pytest.approx(approval["characters"] / 1000 * 0.08)
    assert approval["missing_voices"] == []
    assert take["voice_cast"]["language_code"] == "de"
    assert take["audio_script"]["lines"], "the tagged script is shown before paying"
    assert take["chunks"] == []

    refused = client.post(f"/api/audio/takes/{take['id']}/approve", json={})
    assert refused.status_code == 409
    assert "ELEVENLABS_API_KEY" in refused.json()["detail"]

    speech = StubSpeech()
    speech_registry.register_provider(speech)
    try:
        approved = client.post(f"/api/audio/takes/{take['id']}/approve", json={})
        assert approved.status_code == 200, approved.text
        done = _take(client, run_id, {"completed", "failed"})
    finally:
        speech_registry.reset_provider()

    assert done["status"] == "completed", done["error"]
    assert len(done["chunks"]) == approval["requests"]
    # The stub script repeats itself; an identical chunk is spoken once and reused.
    assert 1 <= len(speech.requests) <= len(done["chunks"])
    assert {i.voice_id for r in speech.requests for i in r.inputs} <= {"v-mod", "v-exp"}
    assert done["audio"]["characters"] == approval["characters"]
    # The take pays for what it spoke; a reused chunk still counts in the audio's value.
    paid = sum(r.characters() for r in speech.requests) / 1000 * 0.08
    assert done["total_cost_usd"] >= paid > 0
    assert done["audio"]["cost_usd"] >= paid

    url = done["chunks"][0]["url"]
    whole = client.get(url)
    assert whole.status_code == 200
    assert whole.headers["content-type"] == "audio/mpeg"
    assert whole.content.startswith(b"ID3")
    part = client.get(url, headers={"Range": "bytes=0-2"})
    assert part.status_code == 206 and part.content == b"ID3"
    assert client.get(f"/api/audio/takes/{take['id']}/chunks/99").status_code == 404


def test_takes_are_refused_where_they_cannot_run(client: TestClient, run_id: str) -> None:
    wrong = client.post(f"/api/runs/{run_id}/audio", json={"flow_id": "baseline_v0"})
    assert wrong.status_code == 422
    assert client.post("/api/runs/nope/audio", json={}).status_code == 404
    full = client.post(f"/api/runs/{run_id}/audio", json={"scope": "full"})
    assert full.status_code == 422, "only the sample in this version"
    take = client.get(f"/api/runs/{run_id}/audio").json()["takes"][0]
    again = client.post(f"/api/audio/takes/{take['id']}/approve", json={})
    assert again.status_code == 409


def test_missing_voices_block_the_approval(client: TestClient, run_id: str) -> None:
    started = client.post(
        f"/api/runs/{run_id}/audio", json={"voice_cast": {"voices": [], "model_id": "eleven_v3"}}
    )
    assert started.status_code == 201, started.text
    take = _take(client, run_id, {"paused", "failed"})
    assert sorted(take["approval"]["missing_voices"]) == ["Expertin", "Moderator"]
    speech_registry.register_provider(StubSpeech())
    try:
        blocked = client.post(f"/api/audio/takes/{take['id']}/approve", json={})
        assert blocked.status_code == 422
        assert "Expertin" in blocked.json()["detail"]
        chosen = client.post(
            f"/api/audio/takes/{take['id']}/approve",
            json={
                "voice_cast": {
                    "voices": [
                        {"speaker": "Moderator", "voice_id": "v1"},
                        {"speaker": "Expertin", "voice_id": "v2"},
                    ],
                    "model_id": "eleven_v3",
                }
            },
        )
        assert chosen.status_code == 200, chosen.text
        done = _take(client, run_id, {"completed", "failed"})
    finally:
        speech_registry.reset_provider()
    assert done["status"] == "completed", done["error"]
    assert done["voice_cast"]["language_code"] == "de", "the language survives a new cast"


def test_the_migration_adds_the_audio_tables(tmp_path: Path) -> None:
    from alembic import command
    from app.migrations import _alembic_config

    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=tmp_path / "audio-db")  # type: ignore[arg-type]
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        config = _alembic_config()
        command.upgrade(config, "f1a6c9d83e20")
        command.upgrade(config, "head")
        tables = set(sa.inspect(get_engine()).get_table_names())
        assert {"audio_takes", "voice_casts"} <= tables
        command.downgrade(config, "f1a6c9d83e20")
        assert "audio_takes" not in set(sa.inspect(get_engine()).get_table_names())
    finally:
        set_settings(previous)
        reset_engine()
