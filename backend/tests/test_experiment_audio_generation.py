"""Prepared import and synthesis over stub providers only."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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
        "segment_ids": None,
        "edits": {},
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


def _wait(client, experiment_id):
    for _ in range(300):
        experiment = client.get(f"/api/experiments/runs/{experiment_id}").json()
        if experiment["status"] in {"completed", "failed"}:
            return experiment
        time.sleep(0.02)
    raise AssertionError(experiment)


def test_selected_and_edited_segments_leave_the_prepared_script_alone(client, run_id):  # noqa: F811
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    loaded = client.post("/api/experiments/audio_generation/source", json={"run_id": run_id})
    assert loaded.status_code == 200, loaded.text
    prepared = json.loads(loaded.json()["fields"]["audio_script"])
    digest = loaded.json()["fields"]["artifact_hash"]
    first, second = prepared["lines"][0], prepared["lines"][1]
    edited = "[excited] Ganz neu formuliert."
    setup = {
        "artifact_hash": digest,
        # Order follows the script, not the list.
        "segment_ids": [second["segment_id"], first["segment_id"]],
        "edits": {first["segment_id"]: edited},
        "voice_cast": {
            "model_id": "eleven_v4",
            "stability": 0.5,
            # Only the selected speakers need a voice.
            "voices": [
                {"speaker": first["speaker"], "voice_id": "v-mod"},
                {"speaker": second["speaker"], "voice_id": "v-exp"},
                {"speaker": "Niemand", "voice_id": ""},
            ],
        },
    }
    source = {"run_id": run_id, "beat_id": loaded.json()["source"]["beat_id"]}

    def post(changes):
        body = json.loads(json.dumps({"setup": setup, "source": source}))
        body["setup"].update(changes)
        return client.post("/api/experiments/audio_generation/runs", json=body)

    for changes, message in [
        ({"segment_ids": []}, "at least one"),
        ({"segment_ids": ["nope"]}, "Unknown segments"),
        ({"edits": {"nope": "x"}}, "Unknown segments"),
        ({"edits": {first["segment_id"]: "   "}}, "no text"),
    ]:
        refused = post(changes)
        assert refused.status_code == 422 and message in refused.json()["detail"], refused.text
    assert not speech.requests

    response = post({})
    assert response.status_code == 201, response.text
    experiment = _wait(client, response.json()["id"])
    assert experiment["status"] == "completed", experiment
    output = experiment["output"]
    assert output["segments"] == 2 and output["edited"] == 1
    assert output["characters"] == len(edited) + len(second["tagged"])
    assert output["audio_script_hash"] != digest

    take = _take(client, run_id, {"completed", "failed"})
    assert take["status"] == "completed", take["error"]
    spoken = take["audio_script"]["lines"]
    assert [line["segment_id"] for line in spoken] == [first["segment_id"], second["segment_id"]]
    assert spoken[0]["tagged"] == edited and spoken[0]["speaker"] == first["speaker"]
    assert spoken[0]["spoken"] == "Ganz neu formuliert." and spoken[0]["tags"] == ["[excited]"]
    assert spoken[1] == second
    sent = [item.text for request in speech.requests for item in request.inputs]
    assert edited in sent and first["tagged"] not in sent
    assert take["mix"]

    again = client.post("/api/experiments/audio_generation/source", json=source).json()
    assert again["fields"]["artifact_hash"] == digest
    assert json.loads(again["fields"]["audio_script"]) == prepared
    assert take["id"] in {beat["id"] for beat in again["beats"]}
    for selection in ({"run_id": run_id}, {"run_id": run_id, "beat_id": take["id"]}):
        retained = client.post("/api/experiments/audio_generation/source", json=selection)
        assert retained.status_code == 200, retained.text
        retained = retained.json()
        assert retained["source"]["beat_id"] == take["id"]
        assert retained["fields"]["artifact_hash"] == output["audio_script_hash"]
        assert json.loads(retained["fields"]["audio_script"])["lines"] == spoken
    reused_setup = {**setup, "artifact_hash": output["audio_script_hash"]}
    rerun = client.post(
        "/api/experiments/audio_generation/runs",
        json={"setup": reused_setup, "source": {"run_id": run_id, "beat_id": take["id"]}},
    )
    assert rerun.status_code == 201, rerun.text
    assert _wait(client, rerun.json()["id"])["status"] == "completed"
    reused_take = _take(client, run_id, {"completed", "failed"})
    assert reused_take["status"] == "completed", reused_take["error"]
    assert reused_take["audio_script"]["lines"] == spoken
    speech_registry.reset_provider()



def test_only_selected_speakers_need_available_voices(client, run_id):  # noqa: F811
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    try:
        loaded = client.post(
            "/api/experiments/audio_generation/source", json={"run_id": run_id}
        ).json()
        prepared = json.loads(loaded["fields"]["audio_script"])
        speaker = prepared["lines"][0]["speaker"]
        speakers = list(dict.fromkeys(line["speaker"] for line in prepared["lines"]))
        assert len(speakers) > 1
        selected = [line for line in prepared["lines"] if line["speaker"] == speaker]
        cast = [
            {
                "speaker": f" {name.upper()} ",
                "voice_id": " v-mod " if name == speaker else "retired-voice",
            }
            for name in speakers
        ]
        body = {
            "source": {"run_id": run_id, "beat_id": loaded["source"]["beat_id"]},
            "setup": {
                "artifact_hash": loaded["fields"]["artifact_hash"],
                "segment_ids": [line["segment_id"] for line in selected],
                "voice_cast": {"voices": cast},
            },
        }
        invalid = json.loads(json.dumps(body))
        invalid["setup"]["segment_ids"] = None
        assert client.post("/api/experiments/audio_generation/runs", json=invalid).status_code == 422
        invalid = json.loads(json.dumps(body))
        invalid["setup"]["voice_cast"]["voices"][0]["voice_id"] = ""
        assert client.post("/api/experiments/audio_generation/runs", json=invalid).status_code == 422
        assert not speech.requests
        response = client.post("/api/experiments/audio_generation/runs", json=body)
        assert response.status_code == 201, response.text
        experiment = _wait(client, response.json()["id"])
        assert experiment["status"] == "completed", experiment
        assert experiment["setup"]["voice_cast"]["voices"] == [
            {**voice, "voice_name": None} for voice in cast
        ]
        take = _take(client, run_id, {"completed", "failed"})
        assert take["status"] == "completed", take["error"]
        assert take["audio_script"]["lines"] == selected
        assert all(item.voice_id == "v-mod" for req in speech.requests for item in req.inputs)
    finally:
        speech_registry.reset_provider()


def test_voice_sample_is_made_once_on_request(client):
    speech_registry.reset_provider()
    assert (
        client.post("/api/audio/voices/v-mod/sample", json={"model_id": "eleven_v4"}).status_code
        == 409
    )
    speech = StubSpeech()
    speech_registry.register_provider(speech)
    assert client.get("/api/audio/voices/v-mod/sample").status_code == 404
    assert client.post("/api/audio/voices/unknown/sample", json={}).status_code == 404
    assert client.post("/api/audio/voices/v-mod/sample", json={"model_id": "x"}).status_code == 422
    assert not speech.requests

    made = client.post("/api/audio/voices/v-mod/sample", json={"model_id": "eleven_v4"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["cost_usd"] > 0 and body["characters"] > 0
    assert [(r.model_id, [i.voice_id for i in r.inputs]) for r in speech.requests] == [
        ("eleven_v4", ["v-mod"])
    ]
    audio = client.get(body["url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    assert audio.content

    reused = client.post("/api/audio/voices/v-mod/sample", json={"model_id": "eleven_v4"}).json()
    assert reused == {**body, "cost_usd": 0.0}
    assert len(speech.requests) == 1
    speech_registry.reset_provider()


def test_concurrent_voice_samples_share_generation(client, monkeypatch, tmp_path):
    from app.api import audio
    from app.pipeline.framework.artifacts import ArtifactStore

    speech = StubSpeech()
    dialogue = speech.dialogue

    def slow_dialogue(request):
        time.sleep(0.1)
        return dialogue(request)

    monkeypatch.setattr(speech, "dialogue", slow_dialogue)
    monkeypatch.setattr(audio, "_store", lambda: ArtifactStore(tmp_path))
    speech_registry.register_provider(speech)
    ready = Barrier(4)

    def request_sample():
        ready.wait(timeout=5)
        return client.post("/api/audio/voices/v-mod/sample", json={"model_id": "eleven_v4"})

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: request_sample(), range(4)))
        assert [response.status_code for response in responses] == [200] * 4
        bodies = [response.json() for response in responses]
        assert len(speech.requests) == 1
        assert sum(body["cost_usd"] > 0 for body in bodies) == 1
        assert len({body["url"] for body in bodies}) == 1
        assert client.get(bodies[0]["url"]).content
    finally:
        speech_registry.reset_provider()
