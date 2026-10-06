"""Prepared import and synthesis over stub providers only."""

import json
import time

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
    payload = {"setup": setup, "source": {"run_id": run_id, "beat_id": original["id"]}}
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
    assert take["audio_script"] == prepared
    assert len(provider.calls) == llm_calls
    assert take["mix"] and client.get(take["mix"]["url"]).status_code == 200
    from app.pipeline.audio_chunks import plan_requests
    from app.schemas.audio import AudioScript, VoiceCast

    planned = plan_requests(
        AudioScript.model_validate(prepared).lines, VoiceCast.model_validate(setup["voice_cast"])
    )
    # Identical planned requests are synthesized once and reused from the cache.
    unique = {plan.key: plan for plan in planned}
    assert [req.inputs for req in speech.requests] == [plan.inputs for plan in unique.values()]
    assert len(take["chunks"]) == len(planned)
    assert len(speech.requests) < len(planned)  # this fixture contains repeated lines
    assert all(
        req.model_id == "eleven_v3" and req.stability == 1 and req.seed == 42
        for req in speech.requests
    )
    # A second configuration stays linked to the same prepared text on provider failure.
    from app.speech.base import SpeechError

    speech.fail_with = SpeechError("Too few credits", status=402)
    payload["setup"]["voice_cast"]["seed"] = 43
    failed = client.post("/api/experiments/audio_generation/runs", json=payload)
    assert failed.status_code == 201
    take = _take(client, run_id, {"failed"})
    assert "Too few credits" in take["error"]
    assert take["audio_script"] == prepared and take["resumable"]
    assert take["voice_cast"]["seed"] == 43
    speech.fail_with = None
    resumed = client.post(f"/api/audio/takes/{take['id']}/resume")
    assert resumed.status_code == 200
    take = _take(client, run_id, {"completed"})
    assert take["mix"]
    speech_registry.reset_provider()
