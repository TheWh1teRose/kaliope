"""Episode API and real OpenAI SDK against a disposable HTTP provider.

The provider boundary is simulated: its reported 70k output tokens include
reasoning. These checks do not prove OpenAI's hard limits or unbounded inference.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
import uvicorn

from app.config import get_settings
from app.db import session_scope
from app.llm import registry
from app.llm.base import CompletionRequest, Message
from app.main import create_app
from app.models import Document, User
from app.pipeline.framework.artifacts import ArtifactStore
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider
from tests.test_script_context import PASSAGES, _block, _input


@pytest.mark.parametrize(
    "cap, provider_cutoff, expected_status",
    [
        (64_000, False, "failed"),
        (0, False, "completed"),
        (0, True, "completed"),
        (64_000, True, "completed"),
    ],
)
def test_episode_length_through_http(
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
    provider_cutoff: bool,
    expected_status: str,
) -> None:
    requests: list[dict[str, Any]] = []
    beat_requests: list[dict[str, Any]] = []
    stub = StubProvider()

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            request = CompletionRequest(
                model=payload["model"],
                system=payload["messages"][0]["content"],
                messages=[Message(**entry) for entry in payload["messages"][1:]],
            )
            text = stub.complete(request).text
            is_script = "You write one beat" in request.system
            if is_script:
                beat_requests.append(payload)
            needed = 70_000 if is_script and len(beat_requests) == 2 else 1000
            cap_cutoff = is_script and payload.get("max_completion_tokens", 128_000) < needed
            stopped = is_script and (provider_cutoff or cap_cutoff)
            # A provider cutoff can still contain usable JSON. The numeric-cap
            # reproduction instead ends mid-JSON, as in the original failure.
            if cap_cutoff and not provider_cutoff:
                text = '{"segments": ['
            output_tokens = min(needed, payload.get("max_completion_tokens", needed))
            response = {
                "id": f"local-{len(requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "length" if stopped else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": output_tokens,
                    "total_tokens": 200 + output_tokens,
                    "completion_tokens_details": {"reasoning_tokens": max(0, output_tokens - 1000)},
                },
            }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    provider_server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    provider_thread = threading.Thread(target=provider_server.serve_forever, daemon=True)
    provider_thread.start()
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{provider_server.server_port}/v1")
    monkeypatch.setattr(get_settings(), "openai_api_key", "disposable-local-only")
    # Production resolution must construct the real adapter and real SDK.
    monkeypatch.setattr(registry, "_providers", {})

    app_socket = socket.socket()
    app_socket.bind(("127.0.0.1", 0))
    app_port = app_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error"))
    app_thread = threading.Thread(target=server.run, kwargs={"sockets": [app_socket]}, daemon=True)
    app_thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and app_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "product API did not start"
        with httpx.Client(base_url=f"http://127.0.0.1:{app_port}", timeout=15) as client:
            email = f"length-{cap}-{int(provider_cutoff)}@kalliope.test"
            with session_scope() as session:
                session.add(
                    User(
                        email=email,
                        name="Length test",
                        role="admin",
                        password_hash=hash_password("local-test-password"),
                    )
                )
            login = client.post(
                "/api/auth/login", json={"email": email, "password": "local-test-password"}
            )
            assert login.status_code == 200, login.text

            # Seed a parsed document with enough actual source words for the
            # content-budget floor; every generation node runs normally.
            parsed = _input().parsed.model_copy(
                update={
                    "blocks": [
                        _block(i, (passage + " ") * 12) for i, passage in enumerate(PASSAGES)
                    ]
                }
            )
            store = ArtifactStore(get_settings().artifacts_dir)
            digest = store.put("parsed", parsed).hash
            with session_scope() as session:
                document = Document(
                    filename="length.pdf",
                    sha256=digest,
                    parse_status="parsed",
                    parse_version=1,
                    parsed_artifact_hash=digest,
                )
                session.add(document)
                session.flush()
                document_id = document.id

            original = client.get("/api/pipelines/baseline_v0").json()
            definition = original["definition"]
            for entry in definition["nodes"]:
                if entry["node"] in {"select", "outline", "script"}:
                    entry["config"]["model"] = "gpt-6.1-sol"
                if entry["node"] == "script":
                    entry["config"].update(max_tokens=cap, effort="medium")
            saved = client.put("/api/pipelines/baseline_v0", json=definition)
            assert saved.status_code == 200, saved.text
            try:
                created = client.post(
                    "/api/runs",
                    json={
                        "document_id": document_id,
                        "flow_id": "baseline_v0",
                        "target_minutes": 5,
                        "force": True,
                    },
                )
                assert created.status_code == 201, created.text
                run_id = created.json()["id"]
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    run = client.get(f"/api/runs/{run_id}").json()
                    if run["status"] in {"failed", "completed"}:
                        break
                    time.sleep(0.01)
                assert run["status"] == expected_status, run
                events = client.get(f"/api/runs/{run_id}/events").text
                if expected_status == "failed":
                    assert "cut off" in run["error"]
                    assert len(beat_requests) == 2
                else:
                    script = client.get(f"/api/runs/{run_id}/script")
                    assert script.status_code == 200, script.text
                    assert len({s["beat_id"] for s in script.json()["segments"]}) == 3
                    script_node = next(n for n in run["nodes"] if n["node_name"] == "script")
                    assert script_node["tokens_out"] == 2000 + min(cap or 70_000, 70_000)
                    if cap == 0:
                        assert script_node["tokens_out"] == 72_000 > 64_000
                    assert len(beat_requests) == 3
                    assert any(s["text"] for s in script.json()["segments"])
                assert all(request["model"] == "gpt-6.1-sol" for request in beat_requests)
                assert all(request["reasoning_effort"] == "medium" for request in beat_requests)
                if cap == 0:
                    assert all("max_completion_tokens" not in request for request in beat_requests)
                else:
                    assert all(request["max_completion_tokens"] == cap for request in beat_requests)
                if provider_cutoff or expected_status == "failed":
                    assert "cut off" in events and "parsed output may be incomplete" in events
                else:
                    assert "cut off" not in events
            finally:
                restored = client.post(
                    f"/api/pipelines/baseline_v0/versions/{original['revision']}/restore"
                )
                assert restored.status_code == 200, restored.text
    finally:
        worker.shutdown(wait=True)
        server.should_exit = True
        app_thread.join(timeout=10)
        app_socket.close()
        provider_server.shutdown()
        provider_server.server_close()
        provider_thread.join(timeout=10)
    assert not app_thread.is_alive() and not provider_thread.is_alive()
