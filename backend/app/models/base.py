from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Opaque primary key. UUID4 hex keeps ids URL-safe and copy-pasteable."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)
