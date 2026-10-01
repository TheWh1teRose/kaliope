"""``SQLITE_JOURNAL_MODE``: WAL by default, rollback journal on Cloud Run.

A Cloud Storage FUSE mount cannot host a write-ahead log (stale file handle on
``kalliope.db-wal``), so the service sets ``SQLITE_JOURNAL_MODE=DELETE``. These
tests cover the setting, the durability pragma that goes with it, and a ``-wal``
left on the volume by an earlier WAL run being folded in rather than dropped.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import Settings, get_settings, set_settings
from app.db import get_engine, reset_engine

DB_NAME = "kalliope.db"


@contextmanager
def _data_dir(path: Path, **overrides: object) -> Iterator[Settings]:
    previous = get_settings()
    settings = Settings(app_secret_key="k" * 32, data_dir=path, **overrides)  # type: ignore[arg-type]
    settings.ensure_dirs()
    set_settings(settings)
    reset_engine()
    try:
        yield settings
    finally:
        set_settings(previous)
        reset_engine()


def _pragma(name: str) -> object:
    with get_engine().connect() as connection:
        return connection.execute(text(f"PRAGMA {name}")).scalar()


def _leave_committed_wal(data_dir: Path) -> None:
    """Make ``data_dir`` look like a WAL service was killed with writes unflushed."""
    source = data_dir.parent / "source"
    source.mkdir()
    writer = sqlite3.connect(source / DB_NAME)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE kept (n INTEGER)")
        writer.executemany("INSERT INTO kept VALUES (?)", [(1,), (2,), (3,)])
        writer.commit()
        # Copy while the writer is still open, so the rows live only in -wal.
        shutil.copy(source / DB_NAME, data_dir / DB_NAME)
        shutil.copy(source / f"{DB_NAME}-wal", data_dir / f"{DB_NAME}-wal")
    finally:
        writer.close()
    assert (data_dir / f"{DB_NAME}-wal").stat().st_size > 0


def test_default_journal_mode_is_wal() -> None:
    assert Settings(app_secret_key="k" * 32).sqlite_journal_mode == "WAL"


def test_journal_mode_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "delete")
    assert Settings(app_secret_key="k" * 32).sqlite_journal_mode == "DELETE"


@pytest.mark.parametrize("mode", ["OFF", "MEMORY", "nonsense", ""])
def test_unsafe_or_unknown_journal_modes_are_rejected(mode: str) -> None:
    with pytest.raises(ValueError):
        Settings(app_secret_key="k" * 32, sqlite_journal_mode=mode)


def test_wal_keeps_the_existing_pragmas(tmp_path: Path) -> None:
    with _data_dir(tmp_path / "wal"):
        assert str(_pragma("journal_mode")).upper() == "WAL"
        assert _pragma("synchronous") == 1  # NORMAL
        assert _pragma("foreign_keys") == 1


def test_delete_mode_uses_a_rollback_journal_and_full_sync(tmp_path: Path) -> None:
    data_dir = tmp_path / "delete"
    with _data_dir(data_dir, sqlite_journal_mode="DELETE"):
        assert str(_pragma("journal_mode")).upper() == "DELETE"
        assert _pragma("synchronous") == 2  # FULL
        assert _pragma("foreign_keys") == 1
        with get_engine().begin() as connection:
            connection.execute(text("CREATE TABLE t (n INTEGER)"))
            connection.execute(text("INSERT INTO t VALUES (1)"))
        assert not (data_dir / f"{DB_NAME}-wal").exists()
        assert not (data_dir / f"{DB_NAME}-shm").exists()


def test_existing_wal_is_checkpointed_not_dropped(tmp_path: Path) -> None:
    data_dir = tmp_path / "leftover"
    data_dir.mkdir()
    _leave_committed_wal(data_dir)

    with _data_dir(data_dir, sqlite_journal_mode="DELETE"):
        with get_engine().connect() as connection:
            rows = connection.execute(text("SELECT n FROM kept ORDER BY n")).scalars().all()
        assert rows == [1, 2, 3]
        assert str(_pragma("journal_mode")).upper() == "DELETE"

    assert not (data_dir / f"{DB_NAME}-wal").exists()
    assert not (data_dir / f"{DB_NAME}-shm").exists()
    # The rows are in the main file itself now.
    check = sqlite3.connect(data_dir / DB_NAME)
    try:
        assert check.execute("SELECT count(*) FROM kept").fetchone() == (3,)
    finally:
        check.close()


def test_alembic_converts_a_leftover_wal_before_the_app_starts(tmp_path: Path) -> None:
    """The entrypoint runs ``alembic upgrade head`` first, on its own engine."""
    from alembic import command
    from app.migrations import _alembic_config

    data_dir = tmp_path / "migrate"
    data_dir.mkdir()
    _leave_committed_wal(data_dir)

    with _data_dir(data_dir, sqlite_journal_mode="DELETE"):
        command.upgrade(_alembic_config(), "head")
        assert not (data_dir / f"{DB_NAME}-wal").exists()
        # Header bytes 18-19 are the file-format versions: 2 means WAL, 1 rollback.
        assert (data_dir / DB_NAME).read_bytes()[18:20] == b"\x01\x01"
        with get_engine().connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM kept")).scalar() == 3
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 0
