"""Deployment acceptance criteria (§13.4, AC-DEP-2, AC-DEP-3).

AC-DEP-1 (``docker build`` succeeds and one ``docker run`` starts the system)
needs a Docker daemon and is verified by ``make docker && make docker-run``
rather than by the unit suite. What is checked here is everything that governs
whether that container comes up: the schema on an empty ``/data``, the
preservation of an existing one, and failing fast on a missing key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.config import ConfigurationError, Settings, set_settings
from app.db import get_engine, reset_engine, session_scope
from app.migrations import current_revision, ensure_schema
from app.models import Document

ROOT = Path(__file__).resolve().parents[2]


def test_ac_dep_3_missing_secret_key_fails_fast() -> None:
    """AC-DEP-3: a missing required key fails at boot with a clear message."""
    settings = Settings(app_secret_key="", data_dir=Path("/tmp"))
    with pytest.raises(ConfigurationError) as exc:
        settings.validate_required()
    message = str(exc.value)
    assert "APP_SECRET_KEY" in message
    assert ".env.example" in message


def test_short_secret_key_is_rejected_with_guidance() -> None:
    settings = Settings(app_secret_key="short", data_dir=Path("/tmp"))
    with pytest.raises(ConfigurationError) as exc:
        settings.validate_required()
    assert "token_urlsafe" in str(exc.value)


def test_ac_dep_2_empty_data_dir_creates_the_schema(tmp_path: Path) -> None:
    """AC-DEP-2 (first half): an empty /data gets the full schema."""
    previous = None
    try:
        from app.config import get_settings

        previous = get_settings()
        settings = Settings(app_secret_key="k" * 32, data_dir=tmp_path / "fresh")
        set_settings(settings)
        reset_engine()

        ensure_schema()

        tables = set(inspect(get_engine()).get_table_names())
        expected = {
            "users",
            "sessions",
            "documents",
            "flows",
            "runs",
            "run_nodes",
            "artifacts",
            "llm_calls",
            "gate_results",
            "segments",
            "edit_events",
            "review_sessions",
        }
        assert expected <= tables
        assert current_revision() is not None, "the migration history was not recorded"
    finally:
        set_settings(previous)
        reset_engine()


def test_ac_dep_2_existing_data_dir_is_preserved(tmp_path: Path) -> None:
    """AC-DEP-2 (second half): an existing /data keeps its rows across a restart."""
    previous = None
    try:
        from app.config import get_settings

        previous = get_settings()
        settings = Settings(app_secret_key="k" * 32, data_dir=tmp_path / "persistent")
        set_settings(settings)
        reset_engine()
        ensure_schema()

        with session_scope() as session:
            document = Document(filename="kept.pdf", sha256="a" * 64, parse_status="parsed")
            session.add(document)
            session.flush()
            document_id = document.id

        # Simulate a container restart against the same volume.
        reset_engine()
        ensure_schema()

        with session_scope() as session:
            assert session.get(Document, document_id) is not None
    finally:
        set_settings(previous)
        reset_engine()


def test_settings_derive_every_path_from_data_dir(tmp_path: Path) -> None:
    """§2: one mounted volume holds everything stateful."""
    settings = Settings(app_secret_key="k" * 32, data_dir=tmp_path)
    for path in (
        settings.db_path,
        settings.artifacts_dir,
        settings.uploads_dir,
        settings.renders_dir,
    ):
        assert tmp_path in path.parents or path.parent == tmp_path
    assert settings.database_url.endswith(str(settings.db_path))


def test_env_example_documents_every_setting() -> None:
    """``.env.example`` lists every variable (§12.1)."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z_]+)=", text, flags=re.MULTILINE))
    required = {
        "APP_SECRET_KEY",
        "DATA_DIR",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "DEFAULT_MODEL",
        "ZONE_MODEL",
        "MAX_CONCURRENT_RUNS",
        "LOG_LEVEL",
    }
    assert required <= documented, f"undocumented: {required - documented}"

    # And nothing the app reads is missing from the file.
    fields = {name.upper() for name in Settings.model_fields if name != "testing"}
    assert fields <= documented, f"settings not in .env.example: {fields - documented}"


def test_entrypoint_refuses_to_start_without_a_secret() -> None:
    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "APP_SECRET_KEY" in script
    assert "alembic upgrade head" in script
    assert script.startswith("#!/bin/sh")


def test_dockerfile_matches_the_deployment_contract() -> None:
    """§12.3: multi-stage, non-root, one volume, healthcheck."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "node:22-alpine" in dockerfile
    assert "python:3.12-slim" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile
    assert "USER kalliope" in dockerfile


def test_default_model_env_var_is_actually_used() -> None:
    """§12.1: DEFAULT_MODEL must resolve, not sit unread in .env.example."""
    import logging

    from app.config import Settings, get_settings, set_settings
    from app.pipeline.framework.artifacts import ArtifactStore
    from app.pipeline.framework.node import NodeContext

    previous = get_settings()
    try:
        set_settings(
            Settings(app_secret_key="k" * 32, data_dir=previous.data_dir, default_model="from-env")
        )
        ctx = NodeContext(
            run_id="r",
            llm=None,  # type: ignore[arg-type]
            artifacts=ArtifactStore(previous.artifacts_dir),
            config={},
            logger=logging.getLogger("test"),
        )
        assert ctx.model("node-fallback") == "from-env"
        ctx.config = {"model": "from-flow"}
        assert ctx.model("node-fallback") == "from-flow"
    finally:
        set_settings(previous)
