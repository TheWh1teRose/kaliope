"""The whole episode as audio: price first, resume without paying twice, one joined file.

Real baseline runs over the stub LLM; ElevenLabs is the stub speech provider,
which returns real (tiny) MP3 tones so the ffmpeg join and the line clock are
tested for real.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import session_scope
from app.llm import registry
from app.main import create_app
from app.models import Document, Run, Series, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.speech import registry as speech_registry
from app.speech.base import SpeechError
from app.worker import worker
from tests.support import StubProvider, StubSpeech, write_learning_pdf

EMAIL = "episode-audio@kalliope.test"
PASSWORD = "episode-audio-password-1"
CAST = {
    "voices": [
        {"speaker": "Moderator", "voice_id": "v-mod"},
        {"speaker": "Expertin", "voice_id": "v-exp"},
    ],
    "model_id": "eleven_v4",
    "stability": 0.5,
}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    registry.register_provider("anthropic", StubProvider())
    speech_registry.reset_provider()
    with TestClient(create_app()) as client:
        with session_scope() as session:
            session.add(
                User(email=EMAIL, name="Folge", password_hash=hash_password(PASSWORD), role="admin")
            )
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        assert client.put("/api/formats/two_host_dialogue/voices", json=CAST).status_code == 200
        yield client
    speech_registry.reset_provider()
    registry.reset_providers()


@pytest.fixture(scope="module")
def document_id(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("episode") / "episode.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="episode.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        new_id = document.id
    worker.parse_document(new_id)
    return new_id


def _finished_run(document_id: str, minutes: int = 15) -> str:
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": minutes},
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        run_id = run.id
    worker.execute_run(run_id)
    with session_scope() as session:
        done = session.get(Run, run_id)
        assert done is not None and done.status == "completed", done and done.error
    return run_id


def _take(client: TestClient, run_id: str, states: set[str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(400):
        body = client.get(f"/api/runs/{run_id}/audio").json()
        if body["takes"] and body["takes"][0]["status"] in states:
            return body["takes"][0]
        time.sleep(0.05)
    raise AssertionError(f"no take reached {states}: {body}")


def _texts(speech: StubSpeech) -> list[tuple[str, ...]]:
    return [tuple(item.text for item in request.inputs) for request in speech.requests]


def test_a_whole_episode_resumes_after_a_failure_and_never_pays_twice(
    client: TestClient, document_id: str
) -> None:
    run_id = _finished_run(document_id)
    # Voices of its own, so no chunk is already cached from another test.
    own = {**CAST, "voices": [{**v, "voice_id": f"ep-{v['voice_id']}"} for v in CAST["voices"]]}
    started = client.post(f"/api/runs/{run_id}/audio", json={"scope": "full", "voice_cast": own})
    assert started.status_code == 201, started.text
    take = _take(client, run_id, {"paused", "failed"})
    assert take["status"] == "paused", take["error"]
    assert take["scope"] == "full"
    script = take["audio_script"]
    assert take["approval"]["lines"] == len(script["lines"])
    assert take["approval"]["characters"] == sum(len(line["tagged"]) for line in script["lines"])
    plan = take["plan"]
    assert len(plan) == take["approval"]["requests"] > 1
    assert {chunk["status"] for chunk in plan} == {"waiting"}

    # The provider goes away after the first request.
    speech = StubSpeech()
    speech.fail_after = (1, SpeechError("ElevenLabs reports too few credits", status=402))
    speech_registry.register_provider(speech)
    try:
        approved = client.post(f"/api/audio/takes/{take['id']}/approve", json={})
        assert approved.status_code == 200, approved.text
        failed = _take(client, run_id, {"completed", "failed"})
        assert failed["status"] == "failed"
        assert "too few credits" in failed["error"]
        assert failed["resumable"] is True
        statuses = [chunk["status"] for chunk in failed["plan"]]
        assert statuses[0] == "done" and "failed" in statuses
        assert len(speech.requests) == 1

        speech.fail_after = None
        resumed = client.post(f"/api/audio/takes/{take['id']}/resume")
        assert resumed.status_code == 200, resumed.text
        done = _take(client, run_id, {"completed", "failed"})
    finally:
        speech_registry.reset_provider()

    assert done["status"] == "completed", done["error"]
    assert done["resumable"] is False
    assert {chunk["status"] for chunk in done["plan"]} == {"done"}
    texts = _texts(speech)
    assert len(texts) == len(set(texts)), "no chunk was spoken twice"
    assert len(texts) <= len(done["plan"])

    mix = done["mix"]
    assert mix is not None
    offsets = mix["chunk_offsets_s"]
    assert offsets == sorted(offsets) and offsets[0] == 0.0
    starts = [line["start_s"] for line in mix["lines"]]
    assert starts == sorted(starts)
    assert {line["segment_id"] for line in mix["lines"]} >= {
        line["segment_id"] for line in script["lines"][:3]
    }
    assert mix["lines"][-1]["end_s"] <= mix["duration_s"] + 0.05
    # Half a second per input plus 0.3 s between chunks, measured from the joined file.
    inputs = sum(len(chunk["segment_ids"]) for chunk in done["plan"])
    expected = 0.5 * inputs + 0.3 * (len(done["plan"]) - 1)
    assert mix["duration_s"] == pytest.approx(expected, abs=0.3)

    inline = client.get(mix["url"])
    assert inline.status_code == 200 and inline.headers["content-type"] == "audio/mpeg"
    assert inline.headers["content-disposition"].startswith("inline")
    saved = client.get(mix["download_url"])
    assert saved.headers["content-disposition"].startswith("attachment")
    assert saved.headers["content-disposition"].endswith('-folge.mp3"')
    assert client.get(mix["url"], headers={"Range": "bytes=0-9"}).status_code == 206

    # The same episode again: every chunk is reused, so the approval costs nothing.
    again = client.post(f"/api/runs/{run_id}/audio", json={"scope": "full", "voice_cast": own})
    assert again.status_code == 201
    second = _take(client, run_id, {"paused", "failed"})
    assert second["approval"]["cached_requests"] == second["approval"]["requests"]
    assert second["approval"]["estimate_usd"] == 0.0
    assert {chunk["status"] for chunk in second["plan"]} == {"cached"}
    reuse = StubSpeech()
    speech_registry.register_provider(reuse)
    try:
        assert client.post(f"/api/audio/takes/{second['id']}/approve", json={}).status_code == 200
        replay = _take(client, run_id, {"completed", "failed"})
    finally:
        speech_registry.reset_provider()
    assert replay["status"] == "completed", replay["error"]
    assert reuse.requests == []
    assert replay["mix"]["url"] != mix["url"]
    assert replay["mix"]["duration_s"] == mix["duration_s"]


def test_only_an_approved_failed_take_resumes(client: TestClient, document_id: str) -> None:
    run_id = _finished_run(document_id, minutes=5)
    client.post(f"/api/runs/{run_id}/audio", json={})
    take = _take(client, run_id, {"paused", "failed"})
    refused = client.post(f"/api/audio/takes/{take['id']}/resume")
    assert refused.status_code == 409
    assert client.get(f"/api/audio/takes/{take['id']}/mix").status_code == 404


def test_a_series_gets_audio_per_episode(client: TestClient, document_id: str) -> None:
    runs = [_finished_run(document_id, minutes=5), _finished_run(document_id, minutes=6)]
    with session_scope() as session:
        series = Series(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            request_json={},
            format_spec_json=get_format("two_host_dialogue").model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="completed",
            checks_json={},
        )
        session.add(series)
        session.flush()
        series_id = series.id
        for index, run_id in enumerate(runs, start=1):
            run = session.get(Run, run_id)
            assert run is not None
            run.series_id = series_id
            run.episode_index = index
            run.name = f"Teil {index}"

    empty = client.get(f"/api/series/{series_id}/audio").json()
    assert [e["index"] for e in empty["episodes"]] == [1, 2]
    assert empty["format_id"] == "two_host_dialogue"
    assert all(e["take"] is None for e in empty["episodes"])

    started = client.post(f"/api/series/{series_id}/audio", json={"scope": "sample"})
    assert started.status_code == 200, started.text
    for run_id in runs:
        _take(client, run_id, {"paused", "failed"})
    waiting = client.get(f"/api/series/{series_id}/audio").json()
    assert waiting["waiting"] == 2
    assert waiting["estimate_usd"] == pytest.approx(
        sum(e["take"]["approval"]["estimate_usd"] for e in waiting["episodes"])
    )
    # Episodes waiting for approval get no second take.
    assert client.post(f"/api/series/{series_id}/audio", json={}).status_code == 409

    assert client.post(f"/api/series/{series_id}/audio/approve").status_code == 409
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    try:
        approved = client.post(f"/api/series/{series_id}/audio/approve")
        assert approved.status_code == 200, approved.text
        finished = [_take(client, run_id, {"completed", "failed"}) for run_id in runs]
    finally:
        speech_registry.reset_provider()
    assert [take["status"] for take in finished] == ["completed", "completed"]
    assert all(take["mix"] for take in finished)
    final = client.get(f"/api/series/{series_id}/audio").json()
    assert final["waiting"] == 0
    assert client.get("/api/series/nope/audio").status_code == 404
