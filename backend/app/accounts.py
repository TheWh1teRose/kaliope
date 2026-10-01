"""Account rules shared by the settings endpoints and the CLI.

Every signed-in user is an administrator of the one shared workspace: there
are no per-user documents, folders, pipelines or runs. Accounts exist so that
every edit carries a real identity, which is also why a removed account is
deactivated rather than deleted — edit events, revisions and comments keep
pointing at a row, and that row is shown as ``REMOVED_LABEL``.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import problem
from app.models import User

MIN_PASSWORD_LENGTH = 10

#: Shown wherever a removed account authored something.
REMOVED_LABEL = "Entfernter Nutzer"

#: Unusable as an argon2 hash, so ``verify_password`` always refuses it.
UNUSABLE_PASSWORD = "!removed"


def normalise_email(raw: str) -> str:
    """Trim and lower-case; refuse what cannot be a login address.

    Deliberately not a full RFC check, for the reason ``LoginRequest`` gives:
    internal deployments use addresses like ``admin@company.internal``.
    """
    email = raw.strip().lower()
    local, _, domain = email.partition("@")
    if not local or not domain or "@" in domain or any(c.isspace() for c in email):
        raise problem(422, "Invalid email", "Enter an email address such as name@example.org.")
    if len(email) > 320:
        raise problem(422, "Invalid email", "The email address is too long.")
    return email


def clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise problem(422, "Name required", "The name cannot be empty.")
    if len(name) > 200:
        raise problem(422, "Name too long", "The name can be at most 200 characters.")
    return name


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise problem(
            422,
            "Password too short",
            f"The password must be at least {MIN_PASSWORD_LENGTH} characters.",
            min_length=MIN_PASSWORD_LENGTH,
        )


def ensure_email_free(db: Session, email: str, *, exclude_id: str | None = None) -> None:
    query = select(User.id).where(func.lower(User.email) == email)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise problem(409, "Email in use", "Another account already uses this email address.")


def author_labels(db: Session) -> dict[str, str]:
    """User id to the label shown next to authored work."""
    return {
        row.id: row.email if row.active else REMOVED_LABEL for row in db.scalars(select(User)).all()
    }


class PasswordAttemptLimiter:
    """Counts wrong passwords per key inside a sliding window.

    In memory, which is enough because the service runs as one instance; a
    restart forgets the count, and that only ever errs on the side of letting a
    legitimate user retry.
    """

    def __init__(self, limit: int = 5, window_seconds: float = 15 * 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        return bucket

    def check(self, key: str) -> None:
        """Raise 429 while ``key`` is over the limit."""
        now = time.monotonic()
        with self._lock:
            bucket = self._recent(key, now)
            if len(bucket) >= self.limit:
                retry = max(1, int(self.window - (now - bucket[0])) + 1)
                raise problem(
                    429,
                    "Too many attempts",
                    "Too many wrong passwords. Try again later.",
                    retry_after_seconds=retry,
                )

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


#: Guards the current-password check on the profile's password change.
password_attempts = PasswordAttemptLimiter()
