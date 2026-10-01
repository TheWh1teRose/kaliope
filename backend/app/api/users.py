"""Profile and member endpoints.

Kalliope is an internal tool with one shared workspace, so every signed-in
user is an equal administrator: anyone may add a member, set a member's
password or remove one. Nothing here sends mail; whoever adds a member passes
the credentials on.

Removing a member deactivates the account instead of deleting it. Documents,
folders, revisions, runs and edit events keep their ``created_by``/``user_id``
pointing at a real row, and authorship shows as ``REMOVED_LABEL``.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import (
    UNUSABLE_PASSWORD,
    check_password_strength,
    clean_name,
    ensure_email_free,
    normalise_email,
    password_attempts,
)
from app.db import get_db
from app.errors import problem
from app.models import SessionToken, User
from app.schemas.api import (
    AccountUpdate,
    MemberCreate,
    MemberOut,
    PasswordChange,
    PasswordSet,
    UserOut,
)
from app.security import SESSION_COOKIE, current_user, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["users"])


# ------------------------------------------------------------------ profile


@router.get("/account", response_model=UserOut)
def read_account(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


@router.patch("/account", response_model=UserOut)
def update_account(
    payload: AccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> UserOut:
    if payload.name is not None:
        user.name = clean_name(payload.name)
    if payload.email is not None:
        email = normalise_email(payload.email)
        ensure_email_free(db, email, exclude_id=user.id)
        user.email = email
    db.commit()
    return _user_out(user)


@router.post("/account/password", status_code=204)
def change_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    # A stolen session must not become a way to guess the account's password.
    password_attempts.check(user.id)
    if not verify_password(user.password_hash, payload.current_password):
        password_attempts.fail(user.id)
        raise problem(
            403, "Wrong password", "The current password is incorrect.", field="current_password"
        )
    check_password_strength(payload.new_password)
    password_attempts.reset(user.id)

    user.password_hash = hash_password(payload.new_password)
    # Every other browser signed in as this user is signed out; this one stays.
    _end_sessions(db, user.id, keep=request.cookies.get(SESSION_COOKIE))
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ members


@router.get("/users", response_model=list[MemberOut])
def list_members(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[MemberOut]:
    rows = db.scalars(select(User).where(User.active.is_(True)).order_by(User.created_at)).all()
    return [_member_out(row, user) for row in rows]


@router.post("/users", response_model=MemberOut, status_code=201)
def create_member(
    payload: MemberCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> MemberOut:
    email = normalise_email(payload.email)
    name = clean_name(payload.name)
    check_password_strength(payload.password)
    ensure_email_free(db, email)

    member = User(
        email=email, name=name, password_hash=hash_password(payload.password), role="admin"
    )
    db.add(member)
    db.commit()
    return _member_out(member, user)


@router.put("/users/{user_id}/password", status_code=204)
def set_member_password(
    user_id: str,
    payload: PasswordSet,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    member = _require_member(db, user_id)
    if member.id == user.id:
        # Your own password goes through the profile, which asks for the
        # current one; otherwise this endpoint would skip that check.
        raise problem(409, "Use your profile", "Change your own password in your profile settings.")
    check_password_strength(payload.password)
    member.password_hash = hash_password(payload.password)
    _end_sessions(db, member.id)
    db.commit()
    return Response(status_code=204)


@router.delete("/users/{user_id}", status_code=204)
def remove_member(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    member = _require_member(db, user_id)
    if member.id == user.id:
        raise problem(409, "Cannot remove yourself", "Another member has to remove your account.")
    active = db.scalar(select(func.count()).select_from(User).where(User.active.is_(True))) or 0
    if active <= 1:
        raise problem(409, "Last member", "The last remaining member cannot be removed.")

    member.active = False
    member.password_hash = UNUSABLE_PASSWORD
    # Frees the address for a new account; the row stays for authorship.
    member.email = f"removed-{member.id}-{secrets.token_hex(4)}@removed.invalid"
    _end_sessions(db, member.id)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ helpers


def _require_member(db: Session, user_id: str) -> User:
    member = db.get(User, user_id)
    if member is None or not member.active:
        raise problem(404, "No such member", f"There is no active member '{user_id}'.")
    return member


def _end_sessions(db: Session, user_id: str, *, keep: str | None = None) -> None:
    statement = sa_delete(SessionToken).where(SessionToken.user_id == user_id)
    if keep:
        statement = statement.where(SessionToken.id != keep)
    db.execute(statement)


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, active=user.active)


def _member_out(member: User, viewer: User) -> MemberOut:
    return MemberOut(
        id=member.id,
        email=member.email,
        name=member.name,
        created_at=member.created_at.isoformat() if member.created_at else "",
        is_self=member.id == viewer.id,
    )
