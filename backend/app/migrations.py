"""Schema bootstrap.

Alembic owns the schema. The container's entrypoint runs ``alembic upgrade
head`` before uvicorn, and this module makes the application safe either way:
an empty ``/data`` gets the full schema, an existing one is upgraded, and a
database created by ``create_all`` in a test or an early build is stamped so it
joins the migration history instead of colliding with it (AC-DEP-2).
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command
from app.config import get_settings
from app.db import get_engine

logger = logging.getLogger(__name__)


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def current_revision() -> str | None:
    with get_engine().connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def ensure_schema() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    engine = get_engine()
    tables = set(inspect(engine).get_table_names())

    if not tables:
        logger.info("empty database at %s; applying migrations", settings.db_path)
        command.upgrade(_alembic_config(), "head")
        return

    if "alembic_version" not in tables:
        # Created outside Alembic (an early build, or a test using create_all).
        # Stamping records where it already is so the next migration applies
        # cleanly rather than trying to recreate existing tables.
        logger.info("database exists without a migration history; stamping head")
        command.stamp(_alembic_config(), "head")
        return

    command.upgrade(_alembic_config(), "head")
