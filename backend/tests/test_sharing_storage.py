"""Release safeguards: existing data, origin validation and access-log secrecy."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.types import Message, Receive, Scope, Send
from uvicorn.protocols.utils import get_path_with_query_string

from app.config import Settings
from app.sharing_privacy import SharingPrivacyMiddleware


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://user:password@example.test",
        "https://example.test/path",
        "https://example.test?secret=value",
        "https://example.test#fragment",
        "https://example.test:99999",
        "https://example.test:invalid",
        "https://example.test\\other.test",
        "https://example%2etest",
        "https://example.test bad.test",
    ],
)
def test_invalid_public_origins_fail_configuration(origin: str) -> None:
    with pytest.raises(ValueError):
        Settings(review_public_origin=origin)


def test_origin_is_explicit_and_has_no_trailing_slash() -> None:
    assert Settings(review_public_origin="").review_public_origin == ""
    assert (
        Settings(review_public_origin="https://example.test/").review_public_origin
        == "https://example.test"
    )


@pytest.mark.parametrize("prefix", ["/r/", "/api/public/review-links/"])
def test_bearer_path_routes_intact_but_uvicorn_access_log_is_redacted(prefix: str) -> None:
    token = "a" * 43
    path = prefix + token
    scope: Scope = {
        "type": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"episode=2&secret=also-hidden",
    }
    sent: list[Message] = []

    async def app(routed: Scope, receive: Receive, send: Send) -> None:
        assert routed["path"] == path
        assert routed["query_string"] == b"episode=2&secret=also-hidden"
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"unavailable"})

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(SharingPrivacyMiddleware(app)(scope, receive, send))
    logged = get_path_with_query_string(scope)
    assert token not in logged and "secret" not in logged
    assert "redacted" in logged
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"private, no-store"
    assert headers[b"referrer-policy"] == b"no-referrer"


def test_review_link_migration_preserves_existing_data_and_enforces_single_active(
    tmp_path: Path,
) -> None:
    backend = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "APP_SECRET_KEY": "test-secret-key-not-for-production",
        "REVIEW_PUBLIC_ORIGIN": "",
    }

    def migrate(direction: str, revision: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", direction, revision],
            cwd=backend,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    migrate("upgrade", "b7d2e9a4c615")
    with sqlite3.connect(tmp_path / "kalliope.db") as db:
        db.execute("CREATE TABLE migration_sentinel (value TEXT)")
        db.execute("INSERT INTO migration_sentinel VALUES ('preserved')")
    migrate("upgrade", "head")
    with sqlite3.connect(tmp_path / "kalliope.db") as db:
        assert db.execute("SELECT value FROM migration_sentinel").fetchone() == ("preserved",)
        columns = {row[1] for row in db.execute("PRAGMA table_info(review_links)")}
        assert "token_digest" in columns and "token" not in columns
        values = (
            "runs",
            "run",
            "owner",
            "2026-01-01",
            "2026-01-31",
            "snapshot",
        )
        statement = (
            "INSERT INTO review_links "
            "(id, token_digest, target_kind, target_id, created_by, created_at, "
            "expires_at, snapshot_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        db.execute(statement, ("first", "digest1", *values))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement, ("second", "digest2", *values))
        db.execute("UPDATE review_links SET revoked_at='2026-01-02' WHERE id='first'")
        db.execute(statement, ("second", "digest2", *values))
    migrate("downgrade", "b7d2e9a4c615")
    with sqlite3.connect(tmp_path / "kalliope.db") as db:
        assert db.execute("SELECT value FROM migration_sentinel").fetchone() == ("preserved",)
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='review_links'").fetchall()
