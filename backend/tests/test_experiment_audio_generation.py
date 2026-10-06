"""Prepared import and synthesis over stub providers only."""

import json
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import create_app
from app.models import User
from app.security import hash_password
from app.speech import registry as speech_registry
from tests.support import StubSpeech
from tests.test_audio_api import _take, provider, run_id  # noqa: F401, F811


@pytest.fixture(scope="module")
def client(provider):  # noqa: F811
    with TestClient(create_app()) as browser:
        with session_scope() as db:
            db.add(
                User(
                    email="synthesis@test.local",
                    name="Synthesis",
                    role="admin",
                    password_hash=hash_password("synthesis-password"),
                )
            )
        browser.post(
            "/api/auth/login",
            json={"email": "synthesis@test.local", "password": "synthesis-password"},
        )
        yield browser


def test_import_and_explicit_synthesis(client, run_id, provider):  # noqa: F811
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    absent = client.post("/api/experiments/audio_generation/source", json={"run_id": run_id})
    assert absent.status_code == 422 and "raw scripts" in absent.json()["detail"]
    response = client.post(f"/api/runs/{run_id}/audio", json={"scope": "sample"})
    assert response.status_code == 201
    original = _take(client, run_id, {"paused"})
    loaded = client.post("/api/experiments/audio_generation/source", json={"run_id": run_id}).json()
    prepared = json.loads(loaded["fields"]["audio_script"])
    assert prepared == original["audio_script"]
    assert not speech.requests
    llm_calls = len(provider.calls)
    setup = {
        "artifact_hash": loaded["fields"]["artifact_hash"],
        "voice_cast": {
            "model_id": "eleven_v3",
            "stability": 1,
            "seed": 42,
            "voices": [
                {"speaker": speaker, "voice_id": "v-mod"}
                for speaker in dict.fromkeys(line["speaker"] for line in prepared["lines"])
            ],
        },
    }
    setup["edited_text"] = [line["tagged"] for line in prepared["lines"]]
    setup["edited_text"][0] = "[curious] Edited  words [short pause] 🌻"
    setup["line_count"] = 1
    payload = {"setup": setup, "source": {"run_id": run_id, "beat_id": original["id"]}}
    oversized = json.loads(json.dumps(payload))
    oversized["setup"]["edited_text"][0] = "x" * 2001
    rejected = client.post("/api/experiments/audio_generation/runs", json=oversized)
    assert rejected.status_code == 422 and "2000" in rejected.text
    assert not speech.requests
    for field, value in [("model_id", "unsupported"), ("stability", 0.4)]:
        invalid = json.loads(json.dumps(payload))
        invalid["setup"]["voice_cast"][field] = value
        assert (
            client.post("/api/experiments/audio_generation/runs", json=invalid).status_code == 422
        )
    invalid = json.loads(json.dumps(payload))
    invalid["setup"]["voice_cast"]["voices"][0]["voice_id"] = "unknown"
    assert client.post("/api/experiments/audio_generation/runs", json=invalid).status_code == 422
    assert not speech.requests
    changed = json.loads(json.dumps(payload))
    changed["setup"]["artifact_hash"] = "unknown"
    assert client.post("/api/experiments/audio_generation/runs", json=changed).status_code == 422
    assert (
        client.post("/api/experiments/audio_generation/runs", json={"setup": setup}).status_code
        == 422
    )
    response = client.post("/api/experiments/audio_generation/runs", json=payload)
    assert response.status_code == 201, response.text
    experiment_id = response.json()["id"]
    for _ in range(300):
        experiment = client.get(f"/api/experiments/runs/{experiment_id}").json()
        if experiment["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert experiment["status"] == "completed", experiment
    assert experiment["source"]["artifact_hash"] == setup["artifact_hash"]
    assert experiment["setup"] == {
        **setup,
        "voice_cast": {
            **setup["voice_cast"],
            "language_code": None,
            "voices": [{**v, "voice_name": None} for v in setup["voice_cast"]["voices"]],
        },
    }
    take = _take(client, run_id, {"completed", "failed"})
    assert take["status"] == "completed", take["error"]
    expected = json.loads(json.dumps(prepared))
    expected["lines"] = expected["lines"][:1]
    expected["lines"][0]["tagged"] = setup["edited_text"][0]
    assert take["audio_script"] == expected
    retained = client.post(
        "/api/experiments/audio_generation/source",
        json={"run_id": run_id, "beat_id": original["id"]},
    ).json()
    assert json.loads(retained["fields"]["audio_script"]) == prepared
    assert len(provider.calls) == llm_calls
    assert take["mix"] is None  # no join/normalisation pass
    assert client.get(take["chunks"][0]["url"]).status_code == 200
    assert len(speech.requests) == 1
    assert speech.requests[0].inputs[0].text == setup["edited_text"][0]
    assert speech.requests[0].inputs[0].voice_id == "v-mod"
    assert speech.requests[0].previous_request_ids == []
    assert len(take["chunks"]) == 1 and take["plan"][0]["status"] == "done"
    assert all(
        req.model_id == "eleven_v3" and req.stability == 1 and req.seed == 42
        for req in speech.requests
    )
    # Retryable provider errors must not trigger a second paid request.
    from app.speech.base import SpeechError

    speech.fail_with = SpeechError("Rate limited", status=429, retryable=True)
    speech.dialogue = Mock(wraps=speech.dialogue)
    payload["setup"]["voice_cast"]["seed"] = 43
    failed = client.post("/api/experiments/audio_generation/runs", json=payload)
    assert failed.status_code == 201
    for _ in range(300):
        experiment = client.get(f"/api/experiments/runs/{failed.json()['id']}").json()
        if experiment["status"] == "failed":
            break
        time.sleep(0.02)
    assert experiment["status"] == "failed" and "Rate limited" in experiment["error"]
    assert speech.dialogue.call_count == 1
    speech.fail_with = None
    speech_registry.reset_provider()
