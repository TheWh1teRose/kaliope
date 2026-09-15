"""Password hashing and session cookies (§12.2).

Sessions are opaque random tokens stored in the ``sessions`` table. The cookie
carries only the token; nothing about the user is client-readable, so there is
no signed-payload to forge. ``APP_SECRET_KEY`` is still required — it seeds the
per-process token generator and guards against a deployment that forgot to set
any secret at all.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.models import SessionToken, User

SESSION_COOKIE = "kalliope_session"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_session(db: Session, user: User) -> SessionToken:
    settings = get_settings()
    token = SessionToken(
        id=secrets.token_urlsafe(48),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
    )
    db.add(token)
    db.flush()
    return token


def destroy_session(db: Session, token_id: str) -> None:
    row = db.get(SessionToken, token_id)
    if row is not None:
        db.delete(row)


def _as_aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat stored values as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def resolve_session(db: Session, token_id: str | None) -> User | None:
    if not token_id:
        return None
    row = db.get(SessionToken, token_id)
    if row is None:
        return None
    if _as_aware(row.expires_at) < datetime.now(UTC):
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency. Raises 401 when the request is not authenticated."""
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise problem(401, "Not authenticated", "Sign in to use this endpoint.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise problem(403, "Forbidden", "This endpoint requires the admin role.")
    return user


def purge_expired_sessions(db: Session) -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = list(db.scalars(select(SessionToken).where(SessionToken.expires_at < now)))
    for row in rows:
        db.delete(row)
    return len(rows)
