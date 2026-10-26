"""SQLAlchemy engine and session management.

SQLite is the only relational store (§2). The journal mode and foreign-key
enforcement are set on every connection; both are per-connection PRAGMAs in
SQLite, so they belong on the ``connect`` event rather than in a migration.

The journal mode is ``SQLITE_JOURNAL_MODE`` (default WAL). WAL does not work on
a Cloud Storage FUSE mount, so the container image sets DELETE (see README).

On that mount a connection's file handle goes stale once another writer replaces
the file (a second instance during a deploy, for one). SQLite then reports a
disk I/O error on every read through it, so such an error drops the pooled
connections and the next request opens fresh ones.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger("kalliope")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _set_journal_mode(cursor: sqlite3.Cursor, mode: str) -> None:
    """Switch the database to ``mode``, folding any leftover WAL into it first.

    A database that was ever in WAL mode keeps that flag in its header, so a
    ``-wal`` file left on the volume holds committed transactions that SQLite
    replays on open. Leaving WAL checkpoints them into the main file and
    removes ``-wal``/``-shm``; do that explicitly and refuse to carry on if it
    does not complete, rather than serve a database that is missing writes.
    """
    current = str(cursor.execute("PRAGMA journal_mode").fetchone()[0]).upper()
    if current == "WAL" and mode != "WAL":
        busy, _, _ = cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError("could not checkpoint the existing write-ahead log")
    actual = str(cursor.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).upper()
    if actual != mode:
        raise RuntimeError(f"SQLite journal_mode is {actual}, expected {mode}")


def configure_journal(dbapi_connection: Any, _record: Any = None) -> None:
    """Apply the journal mode and the durability setting that goes with it.

    Also used by Alembic's engine: the entrypoint migrates before the app
    starts, so it is the first to open the file and must not touch a leftover
    WAL in the wrong mode.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    mode = get_settings().sqlite_journal_mode
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=10000")
    _set_journal_mode(cursor, mode)
    # NORMAL only risks the latest commits after a power loss in WAL mode; the
    # rollback-journal modes need FULL to stay consistent.
    cursor.execute(f"PRAGMA synchronous={'NORMAL' if mode == 'WAL' else 'FULL'}")
    cursor.close()


def _configure_connection(dbapi_connection: Any, record: Any) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    configure_journal(dbapi_connection, record)
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def stale_file(exc: BaseException | None) -> bool:
    """Whether SQLite failed on the file itself rather than on the statement."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF == sqlite3.SQLITE_IOERR
    return "disk I/O error" in str(exc)


def _drop_stale_connections(context: ExceptionContext) -> None:
    if stale_file(context.original_exception):
        logger.warning(
            "sqlite file error, dropping pooled connections: %s", context.original_exception
        )
        # Every pooled handle predates the replaced file, not only this one.
        # The hook may set both flags; the type stub's slots do not say so.
        context.is_disconnect = True  # type: ignore[misc]
        context.invalidate_pool_on_disconnect = True  # type: ignore[misc]


def watch_engine(engine: Engine) -> Engine:
    """The per-connection PRAGMAs, and recovery from a stale database file."""
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "handle_error", _drop_stale_connections)
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = create_engine(
            settings.database_url,
            future=True,
            # The background worker touches the DB from pool threads.
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        watch_engine(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def reset_engine() -> None:
    """Drop the cached engine. Tests call this after repointing DATA_DIR."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    """Create the schema directly. Used by tests and by `cli init-db`."""
    from app.models import Base

    Base.metadata.create_all(get_engine())
