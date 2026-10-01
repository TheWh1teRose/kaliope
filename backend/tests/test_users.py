"""Profile and member management: every signed-in user is an equal admin."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts import REMOVED_LABEL, author_labels, password_attempts
from app.db import session_scope
from app.main import create_app
from app.models import Folder, SessionToken, User
from app.models.base import new_id
from app.security import hash_password

PASSWORD = "settings-password-1"


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    password_attempts.reset()
    yield
    password_attempts.reset()


def _make_user(email: str, password: str = PASSWORD, name: str = "Someone") -> str:
    with session_scope() as session:
        user = User(email=email, name=name, password_hash=hash_password(password), role="reviewer")
        session.add(user)
        session.flush()
        return user.id


def _sign_in(email: str, password: str = PASSWORD) -> TestClient:
    client = TestClient(create_app())
    client.__enter__()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def owner() -> Iterator[tuple[TestClient, str, str]]:
    """A fresh signed-in user with a unique address, plus their id."""
    email = f"owner-{new_id()}@kalliope.test"
    user_id = _make_user(email, name="Owner")
    client = _sign_in(email)
    yield client, user_id, email
    client.__exit__(None, None, None)


def _no_hash(payload: object) -> None:
    text = str(payload)
    assert "password_hash" not in text
    assert "$argon2" not in text


# ------------------------------------------------------------------ access


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/account", None),
        ("patch", "/api/account", {"name": "x"}),
        ("post", "/api/account/password", {"current_password": "x", "new_password": "y" * 12}),
        ("get", "/api/users", None),
        ("post", "/api/users", {"email": "a@b.c", "name": "A", "password": "y" * 12}),
        ("put", "/api/users/abc/password", {"password": "y" * 12}),
        ("delete", "/api/users/abc", None),
    ],
)
def test_every_endpoint_requires_a_session(method: str, path: str, body: object) -> None:
    with TestClient(create_app()) as anonymous:
        kwargs = {"json": body} if body is not None else {}
        response = anonymous.request(method.upper(), path, **kwargs)
    assert response.status_code == 401


# ----------------------------------------------------------------- profile


def test_profile_reads_and_updates_name_and_email(owner: tuple[TestClient, str, str]) -> None:
    client, user_id, _ = owner
    me = client.get("/api/account")
    assert me.status_code == 200
    assert me.json()["id"] == user_id
    _no_hash(me.json())

    new_email = f"  Renamed-{user_id}@Kalliope.TEST "
    updated = client.patch("/api/account", json={"name": "  Neu  ", "email": new_email})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Neu"
    assert updated.json()["email"] == new_email.strip().lower()
    _no_hash(updated.json())

    # Same session still works, and the new address signs in.
    assert client.get("/api/auth/me").json()["email"] == new_email.strip().lower()
    second = _sign_in(new_email.strip().upper())
    second.__exit__(None, None, None)


def test_profile_rejects_a_taken_email_case_insensitively(
    owner: tuple[TestClient, str, str],
) -> None:
    client, _, _ = owner
    _make_user("taken-profile@kalliope.test")
    response = client.patch("/api/account", json={"email": "TAKEN-profile@kalliope.test"})
    assert response.status_code == 409
    assert response.json()["title"] == "Email in use"


def test_profile_rejects_an_empty_name_and_a_bad_email(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    assert client.patch("/api/account", json={"name": "   "}).status_code == 422
    assert client.patch("/api/account", json={"email": "no-at-sign"}).status_code == 422


def test_password_change_keeps_this_session_and_ends_the_others(
    owner: tuple[TestClient, str, str],
) -> None:
    client, user_id, email = owner
    other = _sign_in(email)
    try:
        response = client.post(
            "/api/account/password",
            json={"current_password": PASSWORD, "new_password": "brand-new-password"},
        )
        assert response.status_code == 204, response.text
        assert client.get("/api/account").status_code == 200
        assert other.get("/api/account").status_code == 401
    finally:
        other.__exit__(None, None, None)

    with session_scope() as session:
        tokens = session.scalars(select(SessionToken).where(SessionToken.user_id == user_id)).all()
        assert len(tokens) == 1

    with TestClient(create_app()) as fresh:
        old = fresh.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert old.status_code == 401
        new = fresh.post("/api/auth/login", json={"email": email, "password": "brand-new-password"})
        assert new.status_code == 200


def test_password_change_rejects_a_wrong_current_password(
    owner: tuple[TestClient, str, str],
) -> None:
    client, _, _ = owner
    response = client.post(
        "/api/account/password",
        json={"current_password": "not-the-password", "new_password": "brand-new-password"},
    )
    assert response.status_code == 403
    assert response.json()["field"] == "current_password"


def test_password_change_rejects_a_weak_password(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    response = client.post(
        "/api/account/password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert response.status_code == 422
    assert response.json()["min_length"] == 10


def test_password_change_is_throttled_after_repeated_wrong_guesses(
    owner: tuple[TestClient, str, str],
) -> None:
    client, _, _ = owner
    body = {"current_password": "guess-guess", "new_password": "brand-new-password"}
    for _ in range(password_attempts.limit):
        assert client.post("/api/account/password", json=body).status_code == 403
    blocked = client.post("/api/account/password", json=body)
    assert blocked.status_code == 429
    # Even the right password is refused while the window is closed.
    right = {"current_password": PASSWORD, "new_password": "brand-new-password"}
    assert client.post("/api/account/password", json=right).status_code == 429


# ----------------------------------------------------------------- members


def test_members_list_never_carries_a_hash(owner: tuple[TestClient, str, str]) -> None:
    client, user_id, _ = owner
    response = client.get("/api/users")
    assert response.status_code == 200
    _no_hash(response.json())
    me = [row for row in response.json() if row["id"] == user_id]
    assert me and me[0]["is_self"] is True
    assert set(me[0]) == {"id", "email", "name", "created_at", "is_self"}


def test_create_member_who_can_then_sign_in(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    response = client.post(
        "/api/users",
        json={"email": " New.Member@Kalliope.test ", "name": "Neu", "password": "member-pass-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    _no_hash(body)
    assert body["email"] == "new.member@kalliope.test"
    assert body["is_self"] is False

    member = _sign_in("new.member@kalliope.test", "member-pass-1")
    try:
        # An equal admin: the new member can manage members too.
        assert member.get("/api/users").status_code == 200
    finally:
        member.__exit__(None, None, None)


def test_create_member_rejects_a_duplicate_email(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    _make_user("dupe@kalliope.test")
    response = client.post(
        "/api/users", json={"email": "DUPE@kalliope.test", "name": "X", "password": "member-pass-1"}
    )
    assert response.status_code == 409


def test_a_lost_race_on_the_email_index_is_a_409(
    owner: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests that both passed the pre-check: the unique index decides."""
    import app.api.users as users_api

    client, _, _ = owner
    _make_user("raced@kalliope.test")
    monkeypatch.setattr(users_api, "ensure_email_free", lambda *_a, **_k: None)
    created = client.post(
        "/api/users",
        json={"email": "raced@kalliope.test", "name": "X", "password": "member-pass-1"},
    )
    assert created.status_code == 409
    assert created.json()["title"] == "Email in use"
    profile = client.patch("/api/account", json={"email": "raced@kalliope.test"})
    assert profile.status_code == 409
    # The failed write left the session usable and the profile unchanged.
    assert client.get("/api/account").json()["email"] != "raced@kalliope.test"


def test_create_member_rejects_a_weak_password(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    response = client.post(
        "/api/users", json={"email": "weak@kalliope.test", "name": "X", "password": "123456789"}
    )
    assert response.status_code == 422
    assert response.json()["title"] == "Password too short"


def test_set_member_password_signs_them_out(owner: tuple[TestClient, str, str]) -> None:
    client, _, _ = owner
    member_id = _make_user("reset-me@kalliope.test")
    member = _sign_in("reset-me@kalliope.test")
    try:
        assert (
            client.put(f"/api/users/{member_id}/password", json={"password": "short"}).status_code
            == 422
        )
        response = client.put(
            f"/api/users/{member_id}/password", json={"password": "a-fresh-password"}
        )
        assert response.status_code == 204
        assert member.get("/api/account").status_code == 401
    finally:
        member.__exit__(None, None, None)
    again = _sign_in("reset-me@kalliope.test", "a-fresh-password")
    again.__exit__(None, None, None)


def test_set_own_password_goes_through_the_profile(owner: tuple[TestClient, str, str]) -> None:
    client, user_id, _ = owner
    response = client.put(f"/api/users/{user_id}/password", json={"password": "a-fresh-password"})
    assert response.status_code == 409


def test_cannot_remove_yourself(owner: tuple[TestClient, str, str]) -> None:
    client, user_id, _ = owner
    response = client.delete(f"/api/users/{user_id}")
    assert response.status_code == 409
    assert response.json()["title"] == "Cannot remove yourself"


def test_remove_member_keeps_their_work_and_frees_the_email(
    owner: tuple[TestClient, str, str],
) -> None:
    client, _, _ = owner
    member_id = _make_user("leaving@kalliope.test", name="Leaving")
    member = _sign_in("leaving@kalliope.test")
    with session_scope() as session:
        folder = Folder(name=f"Left behind {member_id}", created_by=member_id)
        session.add(folder)
        session.flush()
        folder_id = folder.id
    try:
        assert client.delete(f"/api/users/{member_id}").status_code == 204
        assert member.get("/api/account").status_code == 401
    finally:
        member.__exit__(None, None, None)

    ids = [row["id"] for row in client.get("/api/users").json()]
    assert member_id not in ids
    with session_scope() as session:
        row = session.get(User, member_id)
        assert row is not None and row.active is False
        assert author_labels(session)[member_id] == REMOVED_LABEL
        assert session.get(Folder, folder_id) is not None

    with TestClient(create_app()) as fresh:
        refused = fresh.post(
            "/api/auth/login", json={"email": "leaving@kalliope.test", "password": PASSWORD}
        )
        assert refused.status_code == 401

    # The address is free for a new account.
    again = client.post(
        "/api/users",
        json={"email": "leaving@kalliope.test", "name": "Back", "password": "member-pass-1"},
    )
    assert again.status_code == 201
    assert client.delete(f"/api/users/{member_id}").status_code == 404


def test_the_last_member_cannot_be_removed(owner: tuple[TestClient, str, str]) -> None:
    client, user_id, _ = owner
    # Deactivate everyone else so the owner is the only active member.
    with session_scope() as session:
        others = session.scalars(
            select(User).where(User.active.is_(True), User.id != user_id)
        ).all()
        saved = [row.id for row in others]
        for row in others:
            row.active = False
    try:
        # Over HTTP this is also self-removal; call the endpoint directly as
        # an (inactive) other caller to reach the last-member rule itself.
        response = client.delete(f"/api/users/{user_id}")
        assert response.status_code == 409
        from app.api.users import remove_member

        caller = User(email="phantom@kalliope.test", name="P", password_hash="!", active=False)
        with session_scope() as session:
            with pytest.raises(Exception) as caught:
                remove_member(user_id, db=session, user=caller)
            assert getattr(caught.value, "title", "") == "Last member"
            owner_row = session.get(User, user_id)
            assert owner_row is not None and owner_row.active is True
    finally:
        with session_scope() as session:
            for row_id in saved:
                row = session.get(User, row_id)
                if row is not None:
                    row.active = True
