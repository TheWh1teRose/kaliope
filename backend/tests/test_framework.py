"""Flow framework acceptance criteria (§13.4, AC-FW-1…5)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.llm import registry
from app.models import Document, LLMCall, Run, RunNode
from app.pipeline.formats import DEFAULT_AUDIENCE, get_format
from app.pipeline.framework.registry import Flow, FlowNode, discover_flows
from app.worker import _flow_directory, worker
from tests.support import StubProvider, write_learning_pdf


@pytest.fixture(scope="module")
def provider() -> Iterator[StubProvider]:
    stub = StubProvider()
    registry.register_provider("anthropic", stub)
    yield stub
    registry.reset_providers()


@pytest.fixture(scope="module")
def document(tmp_path_factory: pytest.TempPathFactory) -> str:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    source = write_learning_pdf(tmp_path_factory.mktemp("pdf") / "learning.pdf")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (settings.uploads_dir / f"{digest}.pdf").write_bytes(payload)

    with session_scope() as session:
        row = Document(filename="learning.pdf", sha256=digest, parse_status="pending")
        session.add(row)
        session.flush()
        document_id = row.id
    return document_id


@pytest.fixture(scope="module")
def parsed_document_id(document: str, provider: StubProvider) -> str:
    worker.parse_document(document)
    with session_scope() as session:
        row = session.get(Document, document)
        assert row is not None and row.parse_status == "parsed", row.parse_error
    return document


def start_run(document_id: str, **config: object) -> str:
    format_spec = get_format("two_host_dialogue")
    with session_scope() as session:
        run = Run(
            document_id=document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            config_json={"target_minutes": 15, **config},
            format_spec_json=format_spec.model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        return run.id


def load_run(run_id: str) -> Run:
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
        return run


def test_ac_fw_1_identical_rerun_is_all_cache_hits(
    parsed_document_id: str, provider: StubProvider
) -> None:
    """AC-FW-1: an identical re-run is all cache hits, zero LLM calls, zero cost."""
    first = start_run(parsed_document_id)
    worker.execute_run(first)
    assert load_run(first).status == "completed", load_run(first).error

    calls_before = len(provider.calls)
    second = start_run(parsed_document_id)
    worker.execute_run(second)
    run = load_run(second)

    assert run.status == "completed"
    assert run.manifest_json is not None
    assert all(node["cache_hit"] for node in run.manifest_json["nodes"])
    assert run.manifest_json["llm_calls"] == 0
    assert run.total_cost_usd == 0.0
    assert len(provider.calls) == calls_before


def test_ac_fw_2_config_change_invalidates_node_and_descendants(
    parsed_document_id: str, provider: StubProvider
) -> None:
    """AC-FW-2: changing one node's config invalidates that node and its descendants only.

    Descendants re-run when the changed node's *output* differs. Caching is
    content-addressed, so a config change that happens to produce an identical
    artifact legitimately leaves downstream work reusable — that is a stronger
    guarantee than key-chaining, not a weaker one.
    """
    baseline = start_run(parsed_document_id)
    worker.execute_run(baseline)

    changed = start_run(
        parsed_document_id,
        node_config={"outline": {"model": "claude-sonnet-5"}},
    )
    worker.execute_run(changed)

    run = load_run(changed)
    assert run.manifest_json is not None
    states = {node["name"]: node["cache_hit"] for node in run.manifest_json["nodes"]}

    assert states["ingest"] is True, "an unrelated upstream node was invalidated"
    assert states["content_budget"] is True, "an unrelated upstream node was invalidated"
    assert states["select"] is True, "an unrelated upstream node was invalidated"
    assert states["outline"] is False, "the node whose config changed was not invalidated"
    assert states["script"] is False, "a descendant of the changed node was not invalidated"


def test_ac_fw_3_manifest_cost_equals_llm_calls(parsed_document_id: str) -> None:
    """AC-FW-3: manifest cost equals the sum of the run's ``llm_calls`` rows."""
    run_id = start_run(parsed_document_id, force=True)
    worker.execute_run(run_id)

    with session_scope() as session:
        recorded = (
            session.scalar(
                select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(
                    LLMCall.run_id == run_id
                )
            )
            or 0.0
        )
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "completed", run.error
        assert abs(run.total_cost_usd - recorded) < 1e-9
        assert run.total_cost_usd > 0.0


def test_ac_fw_4_new_flow_yaml_needs_no_python_change(
    parsed_document_id: str, tmp_path: Path
) -> None:
    """AC-FW-4: a new flow over existing nodes runs with no Python change."""
    yaml_text = """
id: ingest_only_v0
version: "1.0"
description: Parses and budgets, nothing else.
nodes:
  - node: ingest
  - node: content_budget
    config: {min_compression: 2.0}
gates: []
"""
    flow_path = _flow_directory() / "ingest_only_v0.yaml"
    flow_path.write_text(yaml_text, encoding="utf-8")
    try:
        flows = discover_flows(_flow_directory())
        assert "ingest_only_v0" in flows

        format_spec = get_format("two_host_dialogue")
        with session_scope() as session:
            run = Run(
                document_id=parsed_document_id,
                flow_id="ingest_only_v0",
                flow_version="1.0",
                config_json={"target_minutes": 12},
                format_spec_json=format_spec.model_dump(mode="json"),
                audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
                status="queued",
            )
            session.add(run)
            session.flush()
            run_id = run.id

        worker.execute_run(run_id)
        run = load_run(run_id)
        # No script node ran, so post-processing has nothing to store; the flow
        # itself must still execute both of its nodes.
        assert run.manifest_json is not None
        assert [n["name"] for n in run.manifest_json["nodes"]] == ["ingest", "content_budget"]
    finally:
        flow_path.unlink(missing_ok=True)


def test_ac_fw_5_mid_flow_exception_preserves_artifacts(
    parsed_document_id: str, provider: StubProvider
) -> None:
    """AC-FW-5: a mid-flow exception leaves earlier artifacts intact and stores a traceback."""
    # Prime the cache so ingest and content_budget are guaranteed hits.
    warm = start_run(parsed_document_id)
    worker.execute_run(warm)

    provider.fail_with = RuntimeError("provider exploded")
    try:
        run_id = start_run(parsed_document_id, node_config={"select": {"max_prompt_chars": 123456}})
        worker.execute_run(run_id)
    finally:
        provider.fail_with = None

    run = load_run(run_id)
    assert run.status == "failed"
    assert run.error and "provider exploded" in run.error
    assert "Traceback" in run.error

    with session_scope() as session:
        nodes = {
            row.node_name: row
            for row in session.scalars(select(RunNode).where(RunNode.run_id == run_id)).all()
        }
    assert nodes["ingest"].artifact_hash, "an earlier artifact was lost"
    assert nodes["content_budget"].artifact_hash, "an earlier artifact was lost"
    assert nodes["select"].artifact_hash is None
    assert nodes["select"].error
    assert "outline" not in nodes and "script" not in nodes


def test_flow_definition_rejects_unknown_nodes(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(
        'id: broken\nversion: "1.0"\nnodes:\n  - node: does_not_exist\n', encoding="utf-8"
    )
    from app.pipeline.framework.registry import load_flow

    with pytest.raises(KeyError, match="does_not_exist"):
        load_flow(path)


def test_flow_model_round_trips() -> None:
    flow = Flow(id="x", version="1.0", nodes=[FlowNode(node="ingest")])
    assert flow.nodes[0].config == {}
