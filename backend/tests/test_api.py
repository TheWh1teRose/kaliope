"""Console-facing acceptance criteria (§13.4, AC-UI-1…5) exercised over the API.

The console is a thin client over these endpoints: if a citation resolves to
the right rectangle here, and an edit without a reason code is rejected here,
the workspace cannot do otherwise.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.llm import registry
from app.main import create_app
from app.models import Document, Run, User
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.security import hash_password
from app.worker import worker
from tests.support import StubProvider, write_learning_pdf

PASSWORD = "review-password-1"


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def client(provider: StubProvider) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def account() -> str:
    with session_scope() as session:
        user = User(
            email="reviewer@kalliope.test",
            name="Reviewer",
            password_hash=hash_password(PASSWORD),
            role="reviewer",
        )
        session.add(user)
        session.flush()
        return user.id


@pytest.fixture(scope="module")
def signed_in(client: TestClient, account: str) -> TestClient:
    response = client.post(
        "/api/auth/login", json={"email": "reviewer@kalliope.test", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture(scope="module")
def reviewed_run(
    signed_in: TestClient, tmp_path_factory: pytest.TempPathFactory, provider: StubProvider
) -> tuple[str, str]:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("api") / "api.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)

    with session_scope() as session:
        document = Document(filename="api.pdf", sha256=digest, parse_status="pending")
        session.add(document)
        session.flush()
        document_id = document.id

    worker.parse_document(document_id)

    format_spec = get_format("two_host_dialogue")
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": 15},
            format_spec_json=format_spec.model_dump(mode="json"),
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
    return document_id, run_id


# ------------------------------------------------------------------- auth


def test_endpoints_require_authentication() -> None:
    with TestClient(create_app()) as anonymous:
        response = anonymous.get("/api/documents")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Not authenticated"


def test_login_rejects_a_wrong_password(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"email": "reviewer@kalliope.test", "password": "wrong"}
    )
    assert response.status_code == 401
    # Same wording as an unknown account: the form is not a directory.
    assert response.json()["title"] == "Invalid credentials"


def test_upload_rejects_a_non_pdf(signed_in: TestClient) -> None:
    response = signed_in.post(
        "/api/documents", files={"file": ("notes.txt", b"plain text", "text/plain")}
    )
    assert response.status_code == 415
    assert "magic bytes" in response.json()["detail"]


def test_health_reports_the_flow_catalogue(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "baseline_v0" in body["flows"]
    assert "objectives_v0" in body["flows"]


# ------------------------------------------------------------------ AC-UI-1


def test_ac_ui_1_reviewer_sees_segments_with_citations_and_findings(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """AC-UI-1: a completed run exposes every segment with citations and gate findings."""
    _, run_id = reviewed_run
    script = signed_in.get(f"/api/runs/{run_id}/script").json()

    assert script["segments"], "the run produced no segments"
    assert script["language"] == "de"
    assert script["format_spec"]["speakers"]

    claims = [s for s in script["segments"] if s["kind"] == "claim"]
    assert claims, "no claim segments to review"
    assert all(s["anchors"] for s in claims), "a claim reached the console without a citation"

    gates = signed_in.get(f"/api/runs/{run_id}/gates").json()
    assert len(gates) == 9
    # Findings that name a segment are attached to it, so the reviewer sees them
    # in place rather than in a separate list.
    targeted = {v["target_id"] for gate in gates for v in gate["violations"] if v.get("target_id")}
    if targeted:
        attached = {s["id"] for s in script["segments"] if s["violations"]}
        assert targeted & attached


# ------------------------------------------------------------------ AC-UI-2


def test_ac_ui_2_citation_resolves_to_a_rectangle_on_the_right_page(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """AC-UI-2: clicking a citation highlights the correct rectangle on the correct page."""
    document_id, run_id = reviewed_run
    script = signed_in.get(f"/api/runs/{run_id}/script").json()
    structure = signed_in.get(f"/api/documents/{document_id}/structure").json()
    blocks = {b["id"]: b for b in structure["blocks"]}

    anchor = next(a for s in script["segments"] for a in s["anchors"])
    assert anchor["resolved"] is True
    assert anchor["rects"], "the citation resolved to no rectangle"

    block = blocks[anchor["block_id"]]
    rect = anchor["rects"][0]
    assert rect["page"] == block["page"], "the highlight landed on the wrong page"

    # The rectangle lies inside the block's own bounding box on that page.
    page_box = next(b for p, b in block["bboxes"] if p == rect["page"])
    x0, y0, x1, y1 = rect["bbox"]
    assert page_box[0] - 1 <= x0 <= x1 <= page_box[2] + 1
    assert page_box[1] - 1 <= y0 <= y1 <= page_box[3] + 1

    # And the quoted text is exactly the anchored characters.
    assert anchor["text"] == block["text"][anchor["char_start"] : anchor["char_end"]]

    image = signed_in.get(f"/api/documents/{document_id}/pages/{rect['page']}/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


# ------------------------------------------------------------------ AC-UI-3


def test_ac_ui_3_edit_without_a_reason_code_is_impossible(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """AC-UI-3: saving an edit without a reason code is rejected by the API."""
    _, run_id = reviewed_run
    signed_in.post(f"/api/runs/{run_id}/review/start")
    script = signed_in.get(f"/api/runs/{run_id}/script").json()
    segment_id = script["segments"][0]["id"]

    rejected = signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment_id,
            "action": "edit",
            "text_after": "Neuer Text",
        },
    )
    assert rejected.status_code == 422
    assert "reason_code" in json.dumps(rejected.json())

    # A flag needs one too.
    assert (
        signed_in.post(
            f"/api/runs/{run_id}/review/events",
            json={"target_type": "segment", "target_id": segment_id, "action": "flag"},
        ).status_code
        == 422
    )

    # OTHER additionally requires a note.
    assert (
        signed_in.post(
            f"/api/runs/{run_id}/review/events",
            json={
                "target_type": "segment",
                "target_id": segment_id,
                "action": "edit",
                "reason_code": "OTHER",
                "text_after": "Neuer Text",
            },
        ).status_code
        == 422
    )

    accepted = signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment_id,
            "action": "edit",
            "reason_code": "CLUMSY_LANGUAGE",
            "text_before": script["segments"][0]["text"],
            "text_after": "Neuer, klarer formulierter Text.",
        },
    )
    assert accepted.status_code == 201

    # Accepting needs no reason: it asserts no defect.
    assert (
        signed_in.post(
            f"/api/runs/{run_id}/review/events",
            json={
                "target_type": "segment",
                "target_id": script["segments"][1]["id"],
                "action": "accept",
            },
        ).status_code
        == 201
    )


# ------------------------------------------------------------ AC-UI-4, UI-5


def test_ac_ui_5_zone_relabel_is_reviewable(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """AC-UI-5: block zone labels are reviewable and the correction takes effect."""
    document_id, _ = reviewed_run
    structure = signed_in.get(f"/api/documents/{document_id}/structure").json()
    block = structure["blocks"][1]
    original = block["zone"]
    new_zone = "reference" if original != "reference" else "navigation"

    response = signed_in.patch(
        f"/api/documents/{document_id}/blocks/{block['id']}/zone",
        json={"zone": new_zone, "reason_code": "ZONE_WRONG", "note": "Literaturangabe"},
    )
    assert response.status_code == 200

    updated = signed_in.get(f"/api/documents/{document_id}/structure").json()
    changed = next(b for b in updated["blocks"] if b["id"] == block["id"])
    assert changed["zone"] == new_zone
    assert changed["zone_overridden_from"] == original

    rejected = signed_in.patch(
        f"/api/documents/{document_id}/blocks/{block['id']}/zone", json={"zone": "not_a_zone"}
    )
    assert rejected.status_code == 422


def test_ac_ui_4_jsonl_export_has_one_line_per_event(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """AC-UI-4: the export returns one line per event with reviewer, reason and texts."""
    export = signed_in.get("/api/exports/edit-events.jsonl")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in export.text.splitlines() if line.strip()]
    assert lines, "the export is empty"
    events = [json.loads(line) for line in lines]

    required = {
        "id",
        "run_id",
        "document_id",
        "target_type",
        "target_id",
        "user_id",
        "user_email",
        "action",
        "reason_code",
        "note",
        "text_before",
        "text_after",
        "created_at",
    }
    for event in events:
        assert required <= set(event)

    edits = [e for e in events if e["action"] == "edit"]
    assert edits, "no edit event reached the export"
    assert edits[0]["user_email"] == "reviewer@kalliope.test"
    assert edits[0]["reason_code"] == "CLUMSY_LANGUAGE"
    assert edits[0]["text_before"] and edits[0]["text_after"]

    # AC-UI-5: the zone relabel is in the same stream.
    relabels = [e for e in events if e["target_type"] == "block_zone"]
    assert relabels, "a zone relabel did not reach the export"
    assert relabels[-1]["action"] == "relabel"
    assert relabels[-1]["text_before"] != relabels[-1]["text_after"]


def test_review_completion_stores_a_new_artifact_and_keeps_the_original(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    """§9.5: completing a review never overwrites the generated script."""
    _, run_id = reviewed_run
    before = signed_in.get(f"/api/runs/{run_id}/artifacts/script").json()

    summary = signed_in.post(
        f"/api/runs/{run_id}/review/complete", json={"segments": {}, "note": "fertig"}
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["edited_script_artifact"]
    assert body["event_count"] >= 1
    assert body["reason_code_counts"]

    after = signed_in.get(f"/api/runs/{run_id}/artifacts/script").json()
    assert before == after, "the original script artifact was mutated"

    run = signed_in.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "reviewed"


def test_markdown_export_carries_citations_as_footnotes(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    response = signed_in.get(f"/api/runs/{run_id}/export", params={"format": "md"})
    assert response.status_code == 200
    body = response.text
    assert body.startswith("# ")
    assert "[^1]" in body
    assert "Block `b" in body


# --------------------------------------------------------------- folders


def test_folders_nest_and_hold_documents(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    document_id, _ = reviewed_run

    kurs = signed_in.post("/api/folders", json={"name": "Kurs A"})
    assert kurs.status_code == 201, kurs.text
    kurs_id = kurs.json()["id"]

    modul = signed_in.post("/api/folders", json={"name": "Modul 1", "parent_id": kurs_id})
    assert modul.status_code == 201
    modul_id = modul.json()["id"]
    assert modul.json()["depth"] == 1
    assert modul.json()["path"] == ["Kurs A", "Modul 1"]

    # A sibling name collision is refused; the same name deeper down is not.
    assert signed_in.post("/api/folders", json={"name": "kurs a"}).status_code == 409
    assert (
        signed_in.post("/api/folders", json={"name": "Kurs A", "parent_id": modul_id}).status_code
        == 201
    )

    moved = signed_in.post(f"/api/documents/{document_id}/move", json={"folder_id": modul_id})
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == modul_id

    in_folder = signed_in.get("/api/documents", params={"folder_id": modul_id}).json()
    assert [d["id"] for d in in_folder] == [document_id]
    assert not signed_in.get("/api/documents", params={"folder_id": "root"}).json()

    tree = signed_in.get("/api/folders").json()
    top = next(f for f in tree if f["id"] == kurs_id)
    assert top["document_count"] == 0
    assert top["total_document_count"] == 1, "the count does not roll up from subfolders"


def test_a_folder_cannot_contain_itself(signed_in: TestClient) -> None:
    outer = signed_in.post("/api/folders", json={"name": "Zyklus"}).json()
    inner = signed_in.post("/api/folders", json={"name": "Innen", "parent_id": outer["id"]}).json()

    refused = signed_in.post(f"/api/folders/{outer['id']}/move", json={"parent_id": inner["id"]})
    assert refused.status_code == 422
    assert "subfolder" in refused.json()["detail"]


def test_deleting_a_folder_never_deletes_a_document(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    document_id, _ = reviewed_run
    parent = signed_in.post("/api/folders", json={"name": "Ablage"}).json()
    child = signed_in.post(
        "/api/folders", json={"name": "Unterordner", "parent_id": parent["id"]}
    ).json()
    signed_in.post(f"/api/documents/{document_id}/move", json={"folder_id": child["id"]})

    blocked = signed_in.delete(f"/api/folders/{parent['id']}")
    assert blocked.status_code == 409
    assert "cascade" in blocked.json()["detail"]

    deleted = signed_in.delete(f"/api/folders/{parent['id']}", params={"cascade": True})
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_folders": 2, "moved_documents": 1, "moved_to": None}

    document = signed_in.get(f"/api/documents/{document_id}").json()
    assert document["folder_id"] is None, "the document should have moved up, not vanished"


# ------------------------------------------------------- node introspection


def test_run_graph_shows_the_wiring_and_where_a_node_stands(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    graph = signed_in.get(f"/api/runs/{run_id}/graph").json()

    names = [n["name"] for n in graph["nodes"]]
    assert names == ["ingest", "content_budget", "select", "outline", "script"]
    assert all(n["status"] in {"ok", "cached"} for n in graph["nodes"]), graph["nodes"]
    assert graph["failed_node"] is None

    # Every edge either comes from a node that publishes the key, or is a seed.
    produced = {n["produces"]["key"]: n["name"] for n in graph["nodes"]}
    seeds = {s["key"] for s in graph["seeds"]}
    for edge in graph["edges"]:
        if edge["from_node"] is None:
            assert edge["key"] in seeds
        else:
            assert produced[edge["key"]] == edge["from_node"]

    # The script node declares the fields of what it publishes, and the gates
    # that report on it are named against it.
    script_node = next(n for n in graph["nodes"] if n["name"] == "script")
    assert "segments" in {f["name"] for f in script_node["produces"]["fields"]}
    assert "G1" in script_node["checked_by"]
    assert script_node["output_summary"]["segments"]["count"] > 0
    assert {f["gate"] for f in script_node["findings"]} >= {"G1", "G2"}


def test_the_graph_names_the_node_a_failed_run_broke_on(signed_in: TestClient) -> None:
    """A failed run has to say *where* it failed, not only that it did."""
    from app.config import get_settings

    get_settings().ensure_dirs()
    with session_scope() as session:
        # Parsed as far as the database is concerned, but the artifact it points
        # at was never written and the source PDF is not on disk either — so the
        # ingest node has nothing to work from and fails the run at step one.
        document = Document(
            filename="gone.pdf",
            sha256="0" * 64,
            parse_status="parsed",
            parse_version=1,
            parsed_artifact_hash="f" * 64,
        )
        session.add(document)
        session.flush()
        run = Run(
            document_id=document.id,
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

    graph = signed_in.get(f"/api/runs/{run_id}/graph").json()
    assert graph["status"] == "failed"
    assert graph["failed_node"] == "ingest"

    states = {n["name"]: n["status"] for n in graph["nodes"]}
    assert states["ingest"] == "failed"
    assert all(
        states[name] == "blocked" for name in ["content_budget", "select", "outline", "script"]
    )

    broken = next(n for n in graph["nodes"] if n["name"] == "ingest")
    assert broken["error"], "the failing node carries no message"
    assert broken["artifact_hash"] is None

    # And the node view of the same failure agrees with the graph.
    io = signed_in.get(f"/api/runs/{run_id}/nodes/ingest/io").json()
    assert io["status"] == "failed" and io["error"]
    assert io["output"] is None
    assert signed_in.get(f"/api/runs/{run_id}/nodes/script/io").json()["status"] == "blocked"


def test_node_io_resolves_inputs_to_the_upstream_artifact(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    graph = signed_in.get(f"/api/runs/{run_id}/graph").json()
    outline_hash = next(n for n in graph["nodes"] if n["name"] == "outline")["artifact_hash"]

    io = signed_in.get(f"/api/runs/{run_id}/nodes/script/io").json()
    assert io["status"] in {"ok", "cached"}

    by_key = {value["key"]: value for value in io["inputs"]}
    assert by_key["outline"]["produced_by"] == "outline"
    assert by_key["outline"]["artifact_hash"] == outline_hash
    assert by_key["outline"]["preview"]

    # Seeds have no producing node but are still shown with their value.
    assert by_key["format_spec"]["produced_by"] is None
    assert by_key["format_spec"]["available"] is True

    assert io["output"]["key"] == "script"
    assert io["output"]["summary"]["segments"]["count"] > 0

    assert signed_in.get(f"/api/runs/{run_id}/nodes/nope/io").status_code == 404


# ------------------------------------------------------------ gate detail


def test_gate_detail_pairs_the_rule_with_what_was_measured(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    detail = signed_in.get(f"/api/runs/{run_id}/gates/detail").json()
    assert len(detail) == 9

    by_id = {entry["spec"]["id"]: entry for entry in detail}
    for entry in detail:
        spec = entry["spec"]
        assert spec["rule"] and spec["method"], f"{spec['id']} describes nothing"
        assert spec["severity"] in {"fail", "warn"}
        assert spec["uses_model"] is False, "no gate in this build may call a model"

    # A passing gate still reports what it measured.
    g1 = by_id["G1"]
    assert g1["report"]["measurements"]["anchors_checked"] > 0
    assert g1["spec"]["inspects"] == ["script", "ingest"]

    g4 = by_id["G4"]["report"]["measurements"]
    assert g4["band_low"] <= g4["target_words"] <= g4["band_high"]

    # And a skipped gate keeps the reason it skipped.
    for entry in detail:
        if entry["report"]["status"] == "skipped":
            assert entry["report"]["skip_reason"]

    catalogue = signed_in.get("/api/gates").json()
    assert {g["id"] for g in catalogue} == set(by_id)


# ----------------------------------------------------------- undo and tags


def test_undo_takes_back_one_review_action_at_a_time(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    script = signed_in.get(f"/api/runs/{run_id}/script").json()
    segment = script["segments"][2]
    original = segment["text"]

    def state() -> dict[str, object]:
        body = signed_in.get(f"/api/runs/{run_id}/script").json()
        return next(s for s in body["segments"] if s["id"] == segment["id"])

    assert state()["undoable"] is False
    assert (
        signed_in.post(
            f"/api/runs/{run_id}/review/events",
            json={"target_type": "segment", "target_id": segment["id"], "action": "undo"},
        ).status_code
        == 409
    ), "undo with nothing to take back must not invent an event"

    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment["id"],
            "action": "edit",
            "reason_code": "CLUMSY_LANGUAGE",
            "text_before": original,
            "text_after": "Umformuliert.",
        },
    )
    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={"target_type": "segment", "target_id": segment["id"], "action": "accept"},
    )
    after_both = state()
    assert after_both["accepted"] is True and after_both["edited"] is True
    assert after_both["text"] == "Umformuliert."
    assert after_both["original_text"] == original

    # One undo takes back the accept and leaves the edit standing.
    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={"target_type": "segment", "target_id": segment["id"], "action": "undo"},
    )
    after_one = state()
    assert after_one["accepted"] is False
    assert after_one["edited"] is True and after_one["text"] == "Umformuliert."

    # The second takes back the edit, and the generated text returns.
    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={"target_type": "segment", "target_id": segment["id"], "action": "undo"},
    )
    after_two = state()
    assert after_two["edited"] is False
    assert after_two["text"] == original
    assert after_two["undoable"] is False

    # Nothing was deleted: all four actions are still in the event stream.
    events = signed_in.get(f"/api/runs/{run_id}/review/events").json()
    mine = [e for e in events if e["target_id"] == segment["id"]]
    assert [e["action"] for e in mine] == ["edit", "accept", "undo", "undo"]

    # And an undone edit does not reach the stored script or the export.
    signed_in.post(f"/api/runs/{run_id}/review/complete", json={"segments": {}})
    assert "Umformuliert." not in signed_in.get(f"/api/runs/{run_id}/export").text


def test_segments_carry_their_tags_and_comments(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    script = signed_in.get(f"/api/runs/{run_id}/script").json()
    segment_id = script["segments"][3]["id"]

    for tag in ("  Nachrecherche ", "Intro", "Nachrecherche"):
        assert (
            signed_in.post(
                f"/api/runs/{run_id}/review/events",
                json={
                    "target_type": "segment",
                    "target_id": segment_id,
                    "action": "tag",
                    "text_after": tag,
                },
            ).status_code
            == 201
        )
    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment_id,
            "action": "comment",
            "note": "Übergang wirkt abrupt.",
        },
    )

    body = signed_in.get(f"/api/runs/{run_id}/script").json()
    segment = next(s for s in body["segments"] if s["id"] == segment_id)
    assert segment["tags"] == ["Nachrecherche", "Intro"], "a repeated tag must not duplicate"
    assert [c["note"] for c in segment["comments"]] == ["Übergang wirkt abrupt."]
    assert segment["comments"][0]["user_email"] == "reviewer@kalliope.test"

    signed_in.post(
        f"/api/runs/{run_id}/review/events",
        json={
            "target_type": "segment",
            "target_id": segment_id,
            "action": "untag",
            "text_after": "Intro",
        },
    )
    body = signed_in.get(f"/api/runs/{run_id}/script").json()
    assert next(s for s in body["segments"] if s["id"] == segment_id)["tags"] == ["Nachrecherche"]
    assert signed_in.get(f"/api/runs/{run_id}/review/tags").json() == ["Nachrecherche"]

    # A tag with no name, and a comment with no text, carry nothing.
    for payload in (
        {"action": "tag", "text_after": "   "},
        {"action": "comment", "note": ""},
    ):
        assert (
            signed_in.post(
                f"/api/runs/{run_id}/review/events",
                json={"target_type": "segment", "target_id": segment_id, **payload},
            ).status_code
            == 422
        )


def test_sse_stream_replays_a_finished_run(
    signed_in: TestClient, reviewed_run: tuple[str, str]
) -> None:
    _, run_id = reviewed_run
    with signed_in.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunk = next(response.iter_text())
        assert chunk.startswith(": connected")
