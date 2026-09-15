"""Editing pipelines and formats, and the versioning that goes with it (§11).

The two properties that matter are that the history is never rewritten and that
an edit reaches the runner. Everything else here guards the checks that stop a
broken pipeline from being saved in the first place.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import create_app
from app.models import User
from app.pipeline import catalogue
from app.pipeline.framework.registry import discover_flows, flow_directory, get_node
from app.pipeline.framework.spec import node_doc, node_params, prune_defaults
from app.pipeline.validation import REQUIRED_OUTPUT_KEYS, validate_flow
from app.security import hash_password

PASSWORD = "authoring-password-1"
EMAIL = "author@kalliope.test"

BASELINE_NODES = [
    {"node": "ingest", "config": {}},
    {"node": "content_budget", "config": {"min_compression": 2.5}},
    {"node": "select", "config": {"model": "claude-opus-5"}},
    {"node": "outline", "config": {"model": "claude-opus-5"}},
    {"node": "script", "config": {"model": "claude-opus-5", "temperature": 0.7}},
]
OBJECTIVES_NODES = [
    {"node": "ingest", "config": {}},
    {"node": "content_budget", "config": {"min_compression": 2.5}},
    {"node": "objectives", "config": {"model": "claude-opus-5"}},
    {"node": "objective_select", "config": {"model": "claude-opus-5"}},
    {"node": "objective_outline", "config": {"model": "claude-opus-5"}},
    {"node": "script", "config": {"model": "claude-opus-5", "temperature": 0.7}},
    {"node": "ai_critic", "config": {"model": "claude-opus-5"}},
    {"node": "human_feedback", "config": {}},
    {"node": "apply_notes", "config": {"model": "claude-opus-5"}},
]
ALL_GATES = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]


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
                name="Author",
                password_hash=hash_password(PASSWORD),
                role="admin",
            )
        )
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture(scope="module", autouse=True)
def _leave_the_baseline_as_it_shipped(signed_in: TestClient) -> Iterator[None]:
    """Put ``baseline_v0`` back to its shipped revision when this module is done.

    The suite shares one database and later modules execute the baseline flow
    end to end. An edit left behind here would fail one of those for a reason
    nothing in that test mentions.
    """
    yield
    signed_in.post("/api/pipelines/baseline_v0/versions/1/restore")


def _draft(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Draft",
        "version": "1.0",
        "description": None,
        "nodes": [dict(entry) for entry in BASELINE_NODES],
        "gates": list(ALL_GATES),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------- node specs


def test_every_node_documents_itself(signed_in: TestClient) -> None:
    """A node in the editor explains what it does without anyone reading Python."""
    body = signed_in.get("/api/nodes").json()
    assert {n["name"] for n in body["nodes"]} >= {
        "ingest",
        "content_budget",
        "objectives",
        "objective_select",
        "objective_outline",
        "select",
        "outline",
        "script",
    }
    for node in body["nodes"]:
        assert node["doc"]["summary"].strip(), node["name"]
        assert node["doc"]["detail"], node["name"]
        assert node["doc"]["failure_modes"], node["name"]
        assert set(node["doc"]["inputs"]) <= set(node["consumes"]), node["name"]
    assert body["required_outputs"] == list(REQUIRED_OUTPUT_KEYS)
    assert {seed["key"] for seed in body["seeds"]} == {
        "document_ref",
        "target_minutes",
        "format_spec",
        "audience_spec",
    }


def test_generation_nodes_expose_their_prompt() -> None:
    """The system prompt is a parameter, not a constant only the code can see."""
    for name in (
        "objectives",
        "objective_select",
        "objective_outline",
        "select",
        "outline",
        "script",
    ):
        node = get_node(name)
        prompt = next((p for p in node_params(node) if p.key == "system_prompt"), None)
        assert prompt is not None, name
        assert prompt.type == "prompt"
        assert isinstance(prompt.default, str) and len(prompt.default) > 200


def test_a_default_is_not_written_into_the_definition() -> None:
    """Saving a value equal to the default must not invalidate the node's cache."""
    node = get_node("content_budget")
    assert prune_defaults(node, {"min_compression": 2.5}) == {}
    assert prune_defaults(node, {"min_compression": 4.0}) == {"min_compression": 4.0}


def test_doc_falls_back_to_the_docstring() -> None:
    class Undocumented:
        """A node that declares no doc."""

        name = "undocumented"

    assert node_doc(Undocumented()).summary == "A node that declares no doc."


# --------------------------------------------------------------- validation


def test_a_node_before_its_producer_is_refused() -> None:
    nodes = [{"node": "select", "config": {}}, {"node": "ingest", "config": {}}]
    result = validate_flow(nodes, ALL_GATES)
    assert not result.valid
    assert any("parsed" in message for message in result.errors)


def test_a_flow_that_never_produces_a_script_is_refused() -> None:
    result = validate_flow([{"node": "ingest", "config": {}}], ALL_GATES)
    assert not result.valid
    assert any("script" in message for message in result.errors)


def test_the_baseline_shape_validates() -> None:
    result = validate_flow(BASELINE_NODES, ALL_GATES)
    assert result.valid, result.errors
    wiring = {c.node: c for c in result.nodes}
    assert wiring["select"].wiring[0].source == "ingest"
    assert any(w.from_seed for w in wiring["content_budget"].wiring)


def test_the_objectives_shape_validates() -> None:
    result = validate_flow(OBJECTIVES_NODES, ALL_GATES)
    assert result.valid, result.errors
    wiring = {c.node: c for c in result.nodes}
    keys = {w.key: w for w in wiring["objective_outline"].wiring}
    assert keys["objectives"].source == "objectives"
    assert keys["selection"].source == "objective_select"
    assert keys["budget"].source == "content_budget"
    notes = {w.key: w for w in wiring["human_feedback"].wiring}
    assert notes["notes"].source == "ai_critic"
    apply_keys = {w.key: w for w in wiring["apply_notes"].wiring}
    assert apply_keys["notes"].source == "human_feedback"
    assert apply_keys["script"].source == "script"
    assert wiring["apply_notes"].produces == "script"


def test_every_shipped_flow_validates() -> None:
    for flow in discover_flows(flow_directory()).values():
        result = validate_flow(
            [{"node": n.node, "config": n.config} for n in flow.nodes],
            flow.gates,
        )
        assert result.valid, (flow.id, result.errors)


def test_a_duplicated_node_is_refused() -> None:
    result = validate_flow([*BASELINE_NODES, {"node": "script", "config": {}}], ALL_GATES)
    assert not result.valid
    assert any("more than once" in message for message in result.errors)


def test_an_unknown_gate_is_refused() -> None:
    result = validate_flow(BASELINE_NODES, ["G0", "G99"])
    assert not result.valid
    assert any("G99" in message for message in result.errors)


def test_an_undeclared_config_key_warns_but_runs() -> None:
    nodes = [dict(entry) for entry in BASELINE_NODES]
    nodes[1] = {"node": "content_budget", "config": {"min_compresion": 3}}
    result = validate_flow(nodes, ALL_GATES)
    assert result.valid
    assert any("min_compresion" in message for message in result.warnings)


def test_validate_endpoint_matches(signed_in: TestClient) -> None:
    response = signed_in.post("/api/pipelines/validate", json=_draft())
    assert response.status_code == 200
    assert response.json()["valid"] is True


# ------------------------------------------------------- pipeline lifecycle


def test_the_shipped_pipeline_is_listed_and_readable(signed_in: TestClient) -> None:
    listing = signed_in.get("/api/pipelines").json()
    baseline = next(p for p in listing if p["id"] == "baseline_v0")
    assert baseline["origin"] == "file"
    assert baseline["revision"] >= 1
    assert baseline["valid"] is True

    detail = signed_in.get("/api/pipelines/baseline_v0").json()
    assert [n["node"] for n in detail["definition"]["nodes"]] == [
        "ingest",
        "content_budget",
        "select",
        "outline",
        "script",
    ]

    objectives = next(p for p in listing if p["id"] == "objectives_v0")
    assert objectives["origin"] == "file"
    assert objectives["valid"] is True
    objectives_detail = signed_in.get("/api/pipelines/objectives_v0").json()
    assert [n["node"] for n in objectives_detail["definition"]["nodes"]] == [
        "ingest",
        "content_budget",
        "objectives",
        "objective_select",
        "objective_outline",
        "script",
        "ai_critic",
        "human_feedback",
        "apply_notes",
    ]


def test_editing_a_pipeline_appends_a_revision_and_reaches_the_runner(
    signed_in: TestClient,
) -> None:
    before = signed_in.get("/api/pipelines/baseline_v0").json()
    draft = before["definition"]
    draft["nodes"][1]["config"]["min_compression"] = 3.4
    draft["note"] = "Kompression angehoben"

    saved = signed_in.put("/api/pipelines/baseline_v0", json=draft)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == before["revision"] + 1
    assert body["definition"]["nodes"][1]["config"]["min_compression"] == 3.4

    # What the worker would execute, not just what the API echoed back.
    with session_scope() as session:
        flow = catalogue.get_flow(session, "baseline_v0")
    entry = next(n for n in flow.nodes if n.node == "content_budget")
    assert entry.config["min_compression"] == 3.4

    history = signed_in.get("/api/pipelines/baseline_v0/versions").json()
    assert [row["revision"] for row in history] == list(range(len(history), 0, -1))
    assert history[0]["note"] == "Kompression angehoben"
    assert history[0]["created_by_email"] == EMAIL


def test_restoring_appends_rather_than_rewinds(signed_in: TestClient) -> None:
    history = signed_in.get("/api/pipelines/baseline_v0/versions").json()
    latest = history[0]["revision"]

    restored = signed_in.post("/api/pipelines/baseline_v0/versions/1/restore")
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["revision"] == latest + 1

    after = signed_in.get("/api/pipelines/baseline_v0/versions").json()
    assert len(after) == len(history) + 1
    assert after[0]["restored_from"] == 1
    # The revision that was restored over is still readable.
    assert any(row["revision"] == latest for row in after)

    first = signed_in.get("/api/pipelines/baseline_v0/versions/1").json()
    assert first["spec"]["nodes"][1]["config"]["min_compression"] == 2.5


def test_creating_a_pipeline(signed_in: TestClient) -> None:
    payload = _draft(name="Ohne Lesbarkeitsprüfung", gates=["G0", "G1", "G2"])
    payload["id"] = "no_readability_v0"
    payload["note"] = "Angelegt für den Vergleich"

    response = signed_in.post("/api/pipelines", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == "user"
    assert body["revision"] == 1
    assert body["gates"] == ["G0", "G1", "G2"]

    # It is runnable: /api/flows is what the new-run form reads.
    flows = {flow["id"] for flow in signed_in.get("/api/flows").json()}
    assert "no_readability_v0" in flows


def test_a_duplicate_id_is_refused(signed_in: TestClient) -> None:
    payload = _draft()
    payload["id"] = "baseline_v0"
    response = signed_in.post("/api/pipelines", json=payload)
    assert response.status_code == 409


def test_an_unusable_id_is_refused(signed_in: TestClient) -> None:
    payload = _draft()
    payload["id"] = "Nicht Erlaubt!"
    assert signed_in.post("/api/pipelines", json=payload).status_code == 409


def test_archiving_takes_a_pipeline_out_of_circulation(signed_in: TestClient) -> None:
    assert (
        signed_in.post("/api/pipelines/no_readability_v0/archive", json={"archived": True}).json()[
            "archived"
        ]
        is True
    )
    flows = {flow["id"] for flow in signed_in.get("/api/flows").json()}
    assert "no_readability_v0" not in flows
    assert "no_readability_v0" not in {p["id"] for p in signed_in.get("/api/pipelines").json()}
    assert "no_readability_v0" in {
        p["id"] for p in signed_in.get("/api/pipelines?include_archived=true").json()
    }

    signed_in.post("/api/pipelines/no_readability_v0/archive", json={"archived": False})
    assert "no_readability_v0" in {flow["id"] for flow in signed_in.get("/api/flows").json()}


def test_a_prompt_edit_survives_the_round_trip(signed_in: TestClient) -> None:
    detail = signed_in.get("/api/pipelines/baseline_v0").json()
    draft = detail["definition"]
    entry = next(n for n in draft["nodes"] if n["node"] == "script")
    entry["config"]["system_prompt"] = "Schreibe kurz. Zitiere jede Aussage.\n"
    draft["note"] = "Prompt gekürzt"

    assert signed_in.put("/api/pipelines/baseline_v0", json=draft).status_code == 200
    with session_scope() as session:
        flow = catalogue.get_flow(session, "baseline_v0")
    saved = next(n for n in flow.nodes if n.node == "script")
    assert saved.config["system_prompt"].startswith("Schreibe kurz.")


# ------------------------------------------------------------------ formats


def test_editing_a_format_is_versioned(signed_in: TestClient) -> None:
    detail = signed_in.get("/api/format-specs/two_host_dialogue").json()
    spec = detail["spec"]
    spec["target_minutes"] = 22
    spec["speakers"][0]["voice_note"] = "Fragt kurz und konkret."

    response = signed_in.put(
        "/api/format-specs/two_host_dialogue",
        json={"spec": spec, "note": "Länger, Moderator präzisiert"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == detail["revision"] + 1

    listed = signed_in.get("/api/formats").json()
    assert next(f for f in listed if f["id"] == "two_host_dialogue")["target_minutes"] == 22

    history = signed_in.get("/api/format-specs/two_host_dialogue/versions").json()
    assert history[0]["note"] == "Länger, Moderator präzisiert"

    signed_in.post("/api/format-specs/two_host_dialogue/versions/1/restore")
    restored = signed_in.get("/api/format-specs/two_host_dialogue").json()
    assert restored["spec"]["target_minutes"] == 15


def test_a_format_without_speakers_is_refused(signed_in: TestClient) -> None:
    detail = signed_in.get("/api/format-specs/two_host_dialogue").json()
    spec = dict(detail["spec"])
    spec["speakers"] = []
    response = signed_in.put("/api/format-specs/two_host_dialogue", json={"spec": spec})
    assert response.status_code == 422


def test_duplicate_speaker_names_are_refused(signed_in: TestClient) -> None:
    detail = signed_in.get("/api/format-specs/two_host_dialogue").json()
    spec = dict(detail["spec"])
    spec["speakers"] = [
        {"id": "a", "name": "Moderator", "role": "fragt", "voice_note": None},
        {"id": "b", "name": "moderator", "role": "erklärt", "voice_note": None},
    ]
    response = signed_in.put("/api/format-specs/two_host_dialogue", json={"spec": spec})
    assert response.status_code == 422


def test_creating_a_format(signed_in: TestClient) -> None:
    spec = {
        "id": "solo_lecture",
        "name": "Vortrag",
        "speakers": [
            {"id": "narrator", "name": "Sprecherin", "role": "erklärt", "voice_note": None}
        ],
        "register": "sachlich",
        "target_minutes": 10,
        "opening": None,
        "closing": None,
        "beats_hint": None,
    }
    response = signed_in.post("/api/format-specs", json={"spec": spec})
    assert response.status_code == 201, response.text
    assert response.json()["revision"] == 1
    assert "solo_lecture" in {f["id"] for f in signed_in.get("/api/formats").json()}


# ------------------------------------------------------------------- guards


def test_authoring_requires_a_session(client: TestClient) -> None:
    fresh = TestClient(client.app)
    assert fresh.get("/api/pipelines").status_code == 401
    assert fresh.get("/api/nodes").status_code == 401
    assert fresh.put("/api/pipelines/baseline_v0", json=_draft()).status_code == 401
