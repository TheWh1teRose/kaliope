"""Authentication endpoints (§11, §12.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.models import User
from app.schemas.api import LoginRequest, UserOut
from app.security import (
    SESSION_COOKIE,
    create_session,
    current_user,
    destroy_session,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserOut:
    user = db.scalars(select(User).where(User.email == payload.email.lower())).first()
    # The same message for an unknown account and a wrong password: the login
    # form must not double as a directory of who has an account.
    invalid = problem(401, "Invalid credentials", "The email or password is incorrect.")
    if user is None or not user.active:
        raise invalid
    if not verify_password(user.password_hash, payload.password):
        raise invalid

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    token = create_session(db, user)
    db.commit()

    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token.id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_ttl_days * 24 * 3600,
        path="/",
    )
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, active=user.active)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    token_id = request.cookies.get(SESSION_COOKIE)
    if token_id:
        destroy_session(db, token_id)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, active=user.active)
