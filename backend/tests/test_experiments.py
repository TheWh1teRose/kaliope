"""The Experimentieren section: experiment registry, template rendering, runs and collection."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.experiments  # noqa: F401 - registers every experiment
from app.db import session_scope
from app.experiments.base import TemplateError, render
from app.experiments.registry import experiments, get_experiment, register_experiment
from app.experiments.sources import BEAT_TEMPLATE, SAMPLE_BEAT, beat_fields
from app.llm import registry
from app.llm.base import LLMError
from app.main import create_app
from app.models import Document, LLMCall, Run, User
from app.pipeline.audio_tags import AUDIO_SYSTEM, AUDIO_TEMPLATE, parse_lines, render_message
from app.pipeline.bench import coerce_value, load_values
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.script import _SCHEMA, _SYSTEM, ScriptInput, _beat_message
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf

PASSWORD = "experiment-password-1"
EMAIL = "experiments@kalliope.test"


# ---------------------------------------------------------------- rendering


def test_render_fills_placeholders_and_reports_unused_fields() -> None:
    rendered = render("Hi {{ name }}, {{name}}! {json: 1}", {"name": "Ada", "spare": "x"})
    assert rendered.text == "Hi Ada, Ada! {json: 1}"
    assert rendered.unused == ["spare"]


def test_render_refuses_missing_fields_unless_lenient() -> None:
    with pytest.raises(TemplateError) as raised:
        render("{{a}} {{b}}", {"a": "1"})
    assert raised.value.missing == ["b"]
    assert render("{{a}} {{b}}", {"a": "1"}, strict=False).text == "1 {{b}}"


# ----------------------------------------------------------------- registry


def test_registry_lists_direct_style_with_production_defaults() -> None:
    keys = [experiment.key for experiment in experiments()]
    assert "direct_style" in keys
    setup = get_experiment("direct_style").defaults()
    assert setup.system_prompt == _SYSTEM  # type: ignore[attr-defined]
    assert setup.json_schema == _SCHEMA  # type: ignore[attr-defined]
    assert setup.settings.model == "claude-opus-5"  # type: ignore[attr-defined]
    # The defaults run as they are: every placeholder has a field.
    assert get_experiment("direct_style").validate_setup(setup) == []


@pytest.mark.parametrize("key", ["bench", "runs", "outputs", "compare", "Bad-Key"])
def test_registry_refuses_keys_that_collide_with_routes(key: str) -> None:
    class Clash:
        pass

    clash: Any = Clash()
    clash.key = key
    with pytest.raises(ValueError, match="not allowed"):
        register_experiment(clash)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def signed_in(provider: StubProvider) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        with session_scope() as session:
            session.add(
                User(
                    email=EMAIL,
                    name="Experimente",
                    password_hash=hash_password(PASSWORD),
                    role="reviewer",
                )
            )
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture(scope="module")
def finished_run(
    signed_in: TestClient, tmp_path_factory: pytest.TempPathFactory, provider: StubProvider
) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("experiments") / "experiments.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)
    with session_scope() as session:
        document = Document(filename="experiments.pdf", sha256=digest, parse_status="pending")
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
        run_id = run.id
    worker.execute_run(run_id)
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.status == "completed", run.error
    return run_id


def _setup(**settings: Any) -> dict[str, Any]:
    setup = get_experiment("direct_style").defaults().model_dump(mode="json")
    setup["settings"].update(settings)
    return setup


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(200):
        body = client.get(f"/api/experiments/runs/{run_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"experiment run {run_id} did not finish: {body}")


# -------------------------------------------------------------------- parity


def test_default_template_renders_exactly_what_production_sends(
    signed_in: TestClient, finished_run: str
) -> None:
    from app.config import get_settings

    store = ArtifactStore(get_settings().artifacts_dir)
    with session_scope() as session:
        values, _, _ = load_values(
            session,
            store,
            run_id=finished_run,
            keys=["parsed", "outline", "format_spec", "audience_spec", "script"],
            include_payload=True,
        )
    data = {v.key: coerce_value(v.key, v.payload) for v in values}
    format_spec = data["format_spec"].model_copy(
        update={"opening": "Name the topic.", "closing": "Recap."}
    )
    outline = data["outline"]
    outline.beats[0] = outline.beats[0].model_copy(update={"summary": "What it covers."})
    script = data["script"]
    script_input = ScriptInput(
        parsed=data["parsed"],
        outline=outline,
        format_spec=format_spec,
        audience_spec=data["audience_spec"],
    )
    blocks = {b.id: b for b in script_input.parsed.blocks}
    total = len(outline.beats)
    assert total >= 2
    written = []
    for index, beat in enumerate(outline.beats):
        beat_blocks = [blocks[bid] for bid in beat.block_ids if bid in blocks]
        expected, _breaks = _beat_message(script_input, index, beat_blocks, written)
        fields = beat_fields(
            script_input.parsed, outline, format_spec, script_input.audience_spec, index, script
        )
        assert render(BEAT_TEMPLATE, fields).text == expected, beat.id
        segments = [s for s in script.segments if s.beat_id == beat.id]
        if segments:
            written.append((index, beat, segments))
    assert written, "the finished run wrote no segments to carry forward"


# ------------------------------------------------------------------------ API


def test_experiment_endpoints_require_authentication() -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/experiments").status_code == 401


def test_collection_and_detail(signed_in: TestClient) -> None:
    listed = signed_in.get("/api/experiments").json()
    assert any(item["key"] == "direct_style" for item in listed)
    detail = signed_in.get("/api/experiments/direct_style").json()
    assert detail["title"] == "Direkter Stil"
    assert detail["defaults"]["fields"] == SAMPLE_BEAT
    assert signed_in.get("/api/experiments/nope").status_code == 404


def test_load_fields_from_a_run(signed_in: TestClient, finished_run: str) -> None:
    body = signed_in.post(
        "/api/experiments/direct_style/source", json={"run_id": finished_run}
    ).json()
    assert body["beats"]
    assert body["fields"]["passages"].startswith("[")
    assert body["source"]["run_id"] == finished_run
    assert body["source"]["reference"], "the production script for the beat is attached"

    second = body["beats"][1]["id"]
    other = signed_in.post(
        "/api/experiments/direct_style/source",
        json={"run_id": finished_run, "beat_id": second},
    ).json()
    assert other["source"]["beat_id"] == second
    assert other["fields"]["beat_position"] == "2"

    missing = signed_in.post("/api/experiments/direct_style/source", json={"run_id": "nope"})
    assert missing.status_code == 422


def test_run_stores_the_answer_its_cost_and_what_was_sent(
    signed_in: TestClient, provider: StubProvider
) -> None:
    setup = _setup()
    setup["fields"]["anmerkung"] = "Sprechen Sie die Hörerin direkt an."
    setup["user_template"] += "\n\nNote from the editor: {{anmerkung}}"
    created = signed_in.post("/api/experiments/direct_style/runs", json={"setup": setup})
    assert created.status_code == 201, created.text
    body = _wait(signed_in, created.json()["id"])

    assert body["status"] == "completed", body["error"]
    assert body["output"]["payload"]["segments"], "parsed structured answer"
    call = body["calls"][0]
    assert (
        "Note from the editor: Sprechen Sie die Hörerin direkt an."
        in (call["messages"][0]["content"])
    )
    assert call["system"] == _SYSTEM
    assert body["total_cost_usd"] > 0
    assert body["items"] == [{"item": "main", "title": None, "meta": {}, "output_id": None}]
    with session_scope() as session:
        recorded = (
            session.query(LLMCall).filter(LLMCall.node_name == "experiment:direct_style").count()
        )
    assert recorded >= 1


def test_plain_text_runs_keep_settings_and_report_unused_fields(
    signed_in: TestClient, provider: StubProvider
) -> None:
    setup = _setup(temperature=0.4, effort="high", structured=False)
    setup["fields"]["spare"] = "never placed in the template"
    created = signed_in.post("/api/experiments/direct_style/runs", json={"setup": setup})
    body = _wait(signed_in, created.json()["id"])
    assert body["status"] == "completed", body["error"]
    assert "fields not used by the template: spare" in body["warnings"]
    sent = provider.calls[-1]
    assert (sent.temperature, sent.effort, sent.json_schema) == (0.4, "high", None)
    assert body["output"]["payload"] is None
    assert body["output"]["text"]


def test_setups_that_cannot_run_are_refused_before_queueing(signed_in: TestClient) -> None:
    setup = _setup()
    setup["user_template"] = "{{missing_field}}"
    refused = signed_in.post("/api/experiments/direct_style/runs", json={"setup": setup})
    assert refused.status_code == 422
    assert "missing_field" in refused.json()["detail"]

    setup = _setup()
    setup["json_schema"] = {"type": "array"}
    assert (
        signed_in.post("/api/experiments/direct_style/runs", json={"setup": setup}).status_code
        == 422
    )

    unknown = signed_in.post(
        "/api/experiments/direct_style/runs", json={"setup": _setup(model="claude-unknown")}
    )
    assert unknown.status_code == 422
    assert unknown.json()["title"] == "Unknown model"


def test_a_failing_provider_fails_the_run_with_its_message(
    signed_in: TestClient, provider: StubProvider
) -> None:
    provider.fail_with = LLMError("Anthropic request failed: overloaded")
    try:
        created = signed_in.post("/api/experiments/direct_style/runs", json={"setup": _setup()})
        body = _wait(signed_in, created.json()["id"])
    finally:
        provider.fail_with = None
    assert body["status"] == "failed"
    assert "overloaded" in body["error"]
    collect = signed_in.post(f"/api/experiments/runs/{body['id']}/save", json={})
    assert collect.status_code == 409


def test_collect_list_and_delete_outputs(signed_in: TestClient) -> None:
    created = signed_in.post("/api/experiments/direct_style/runs", json={"setup": _setup()})
    run = _wait(signed_in, created.json()["id"])

    saved = signed_in.post(
        f"/api/experiments/runs/{run['id']}/save", json={"item": "main", "label": "Direkt v1"}
    )
    assert saved.status_code == 201
    output = saved.json()
    assert output["label"] == "Direkt v1"
    assert output["output"]["payload"]["segments"]
    assert output["meta"]["model"] == "claude-opus-5"
    assert output["setup"]["system_prompt"] == _SYSTEM

    again = signed_in.post(f"/api/experiments/runs/{run['id']}/save", json={"item": "main"})
    assert again.json()["id"] == output["id"], "collecting twice returns the same output"
    assert (
        signed_in.post(f"/api/experiments/runs/{run['id']}/save", json={"item": "x"}).status_code
        == 422
    )

    refreshed = signed_in.get(f"/api/experiments/runs/{run['id']}").json()
    assert refreshed["items"][0]["output_id"] == output["id"]
    listed = signed_in.get("/api/experiments/direct_style/outputs").json()
    assert listed[0]["id"] == output["id"]
    history = signed_in.get("/api/experiments/direct_style/runs").json()
    assert any(item["id"] == run["id"] for item in history)
    stats = signed_in.get("/api/experiments/direct_style").json()["stats"]
    assert stats["saved_count"] >= 1 and stats["spent_usd"] > 0

    assert signed_in.delete(f"/api/experiments/outputs/{output['id']}").status_code == 204
    assert signed_in.delete(f"/api/experiments/outputs/{output['id']}").status_code == 404
    assert signed_in.get(f"/api/experiments/runs/{run['id']}").json()["status"] == "completed"


# ---------------------------------------------------------------- audio tags


def test_audio_tags_defaults_render_what_the_node_sends() -> None:
    experiment = get_experiment("audio_tags")
    setup: Any = experiment.defaults()
    assert experiment.validate_setup(setup) == []
    assert setup.system_prompt == AUDIO_SYSTEM
    assert setup.user_template == AUDIO_TEMPLATE
    lines, problems = parse_lines(setup.fields["lines"])
    assert problems == [] and len(lines) == 6
    fields = {**setup.fields, "lines": "\n".join(line.formatted() for line in lines)}
    assert render(setup.user_template, fields).text == render_message(fields)


def test_audio_tags_load_a_beat_from_a_run(signed_in: TestClient, finished_run: str) -> None:
    body = signed_in.post("/api/experiments/audio_tags/source", json={"run_id": finished_run})
    assert body.status_code == 200, body.text
    first = body.json()
    assert first["beats"] and first["source"]["beat_position"] == 1
    assert first["fields"]["lines"].startswith("[")
    assert first["fields"]["speakers"].startswith("- Moderator (")
    assert first["fields"]["context"] == ""
    assert "tag_language" not in first["fields"], "the tag settings stay as the person set them"

    second = first["beats"][1]["id"]
    other = signed_in.post(
        "/api/experiments/audio_tags/source", json={"run_id": finished_run, "beat_id": second}
    ).json()
    assert other["source"]["beat_id"] == second
    assert other["fields"]["context"].startswith("The line just before these")
    assert second in other["fields"]["lines"]

    missing = signed_in.post("/api/experiments/audio_tags/source", json={"run_id": "nope"})
    assert missing.status_code == 422


def test_audio_tags_run_checks_every_typed_line(signed_in: TestClient) -> None:
    setup = get_experiment("audio_tags").defaults().model_dump(mode="json")
    created = signed_in.post("/api/experiments/audio_tags/runs", json={"setup": setup})
    assert created.status_code == 201, created.text
    body = _wait(signed_in, created.json()["id"])

    assert body["status"] == "completed", body["error"]
    audio = body["output"]["audio_script"]
    assert [line["segment_id"] for line in audio["lines"]] == [f"l{i:03d}" for i in range(1, 7)]
    assert body["output"]["guard"]["passed"] == 6
    assert audio["lines"][0]["tagged"].startswith("[thoughtful] ")
    sent = body["calls"][0]["messages"][0]["content"]
    assert "[l001] Moderator: Wovon lebt eigentlich eine Pflanze" in sent
    assert "[l002] Expertin (claim): Von Licht" in sent


def test_audio_tags_refuses_lines_without_a_speaker(signed_in: TestClient) -> None:
    setup = get_experiment("audio_tags").defaults().model_dump(mode="json")
    setup["fields"]["lines"] = "Nur ein Satz ohne Sprecher"
    refused = signed_in.post("/api/experiments/audio_tags/runs", json={"setup": setup})
    assert refused.status_code == 422
    assert "Speaker: text" in refused.json()["detail"]
