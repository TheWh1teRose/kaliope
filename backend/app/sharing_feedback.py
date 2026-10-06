"""Feedback bound to one review link and its frozen snapshot.

The share token is the grant. The browser key is hashed before storage, and
nothing here writes a workspace review session. Callers must not log the
comment text or the raw key.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import problem
from app.models import ReviewFeedback, ReviewFeedbackMark, ReviewLink
from app.schemas.sharing import (
    FeedbackState,
    FeedbackSummary,
    MarkedLine,
    MarkIn,
    MarkState,
    ResponseSummary,
    SheetIn,
)

MAX_KEYS = 20
WRITE_LIMIT = 60
_KEY = re.compile(r"^[0-9a-f]{64}$")
_write_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_write_lock = Lock()


def reviewer_hash(raw: str) -> str:
    if not _KEY.fullmatch(raw or ""):
        raise problem(
            422,
            "Rückmeldung unvollständig",
            "Dieser Browser kann so nicht gespeichert werden.",
        )
    return hashlib.sha256(raw.encode()).hexdigest()


def key_from(request: Request) -> str:
    return reviewer_hash(request.headers.get("x-reviewer-key", ""))


def limit_writes(request: Request) -> None:
    """In-process write cap. It resets when this process restarts."""
    peer = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _write_lock:
        started, count = _write_attempts.get(peer, (now, 0))
        if now - started >= 60:
            started, count = now, 0
        count += 1
        _write_attempts[peer] = (started, count)
        _write_attempts.move_to_end(peer)
        if len(_write_attempts) > 1024:
            _write_attempts.popitem(last=False)
        limited = count > WRITE_LIMIT
    if limited:
        raise problem(
            429,
            "Zu viele Rückmeldungen",
            "Bitte warte einen Moment und versuche es erneut.",
        )


def _blank(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _stars(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        raise problem(422, "Sterne ungültig", "Sterne gehen in halben Schritten von 0,5 bis 5.")
    halves = round(value * 2)
    if abs(value * 2 - halves) > 1e-6 or halves < 1 or halves > 10:
        raise problem(422, "Sterne ungültig", "Sterne gehen in halben Schritten von 0,5 bis 5.")
    return halves / 2


def _segments(content: dict[str, Any], episode: int) -> list[dict[str, Any]] | None:
    for item in content.get("episodes") or []:
        if item.get("index") == episode:
            segments = item.get("segments") or []
            return segments if isinstance(segments, list) else []
    return None


def _line(content: dict[str, Any] | None, episode: int, ordinal: int) -> tuple[str, str]:
    if not content:
        return "", ""
    segments = _segments(content, episode)
    if segments is None or ordinal >= len(segments):
        return "", ""
    segment = segments[ordinal]
    return str(segment.get("speaker") or ""), str(segment.get("text") or "")


def _writer(db: Session, link: ReviewLink, key_hash: str) -> ReviewFeedback:
    row = db.scalars(
        select(ReviewFeedback).where(
            ReviewFeedback.review_link_id == link.id,
            ReviewFeedback.key_hash == key_hash,
        )
    ).first()
    if row is not None:
        return row
    count = db.scalar(
        select(func.count())
        .select_from(ReviewFeedback)
        .where(ReviewFeedback.review_link_id == link.id)
    )
    if (count or 0) >= MAX_KEYS:
        raise problem(
            409,
            "Keine weitere Rückmeldung",
            "Für diesen Link sind schon genug Rückmeldungen da.",
        )
    row = ReviewFeedback(
        review_link_id=link.id,
        snapshot_hash=link.snapshot_hash,
        key_hash=key_hash,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _touch(row: ReviewFeedback) -> None:
    row.updated_at = datetime.now(UTC)


def save_mark(
    db: Session, link: ReviewLink, key_hash: str, content: dict[str, Any], payload: MarkIn
) -> None:
    segments = _segments(content, payload.episode)
    if segments is None or payload.ordinal >= len(segments):
        raise problem(422, "Zeile unbekannt", "Diese Zeile gehört nicht zu diesem Stand.")
    comment = _blank(payload.comment)
    if payload.reaction is None and not comment:
        row = db.scalars(
            select(ReviewFeedback).where(
                ReviewFeedback.review_link_id == link.id,
                ReviewFeedback.key_hash == key_hash,
            )
        ).first()
        if row is None:
            return
        mark = db.scalars(
            select(ReviewFeedbackMark).where(
                ReviewFeedbackMark.feedback_id == row.id,
                ReviewFeedbackMark.episode_index == payload.episode,
                ReviewFeedbackMark.ordinal == payload.ordinal,
            )
        ).first()
        if mark is not None:
            db.delete(mark)
            _touch(row)
        return
    if payload.slop and payload.reaction not in {"dislike", "horrible"}:
        raise problem(422, "Slop passt nicht", "KI-Slop gibt es nur zu einer negativen Markierung.")
    if comment and len(comment) > 500:
        raise problem(422, "Kommentar zu lang", "Ein Kommentar hat höchstens 500 Zeichen.")
    row = _writer(db, link, key_hash)
    mark = db.scalars(
        select(ReviewFeedbackMark).where(
            ReviewFeedbackMark.feedback_id == row.id,
            ReviewFeedbackMark.episode_index == payload.episode,
            ReviewFeedbackMark.ordinal == payload.ordinal,
        )
    ).first()
    if mark is None:
        mark = ReviewFeedbackMark(
            feedback_id=row.id,
            episode_index=payload.episode,
            ordinal=payload.ordinal,
            reaction=payload.reaction or "",
        )
        db.add(mark)
    # Empty string is an unrated comment in the existing non-null column.
    # Public responses expose null; legacy saved reactions remain unchanged.
    mark.reaction = payload.reaction or ""
    mark.slop = payload.slop if payload.reaction in {"dislike", "horrible"} else False
    mark.comment = comment
    _touch(row)


def save_sheet(db: Session, link: ReviewLink, key_hash: str, payload: SheetIn) -> None:
    label = _blank(payload.label)
    if label and len(label) > 40:
        raise problem(422, "Name zu lang", "Der Kurzname hat höchstens 40 Zeichen.")
    worked = _blank(payload.worked)
    did_not = _blank(payload.did_not)
    stars = _stars(payload.stars)
    existing = db.scalars(
        select(ReviewFeedback).where(
            ReviewFeedback.review_link_id == link.id,
            ReviewFeedback.key_hash == key_hash,
        )
    ).first()
    if existing is None and label is None and stars is None and worked is None and did_not is None:
        return
    row = existing or _writer(db, link, key_hash)
    row.label = label
    row.stars = stars
    row.worked = worked
    row.did_not = did_not
    _touch(row)


def state(db: Session, link: ReviewLink, key_hash: str) -> FeedbackState:
    row = db.scalars(
        select(ReviewFeedback).where(
            ReviewFeedback.review_link_id == link.id,
            ReviewFeedback.key_hash == key_hash,
        )
    ).first()
    if row is None:
        return FeedbackState()
    marks = db.scalars(
        select(ReviewFeedbackMark)
        .where(ReviewFeedbackMark.feedback_id == row.id)
        .order_by(ReviewFeedbackMark.episode_index, ReviewFeedbackMark.ordinal)
    ).all()
    return FeedbackState(
        label=row.label,
        stars=row.stars,
        worked=row.worked,
        did_not=row.did_not,
        marks=[
            MarkState(
                episode=mark.episode_index,
                ordinal=mark.ordinal,
                reaction=mark.reaction or None,  # type: ignore[arg-type]
                slop=mark.slop,
                comment=mark.comment,
            )
            for mark in marks
        ],
    )


def _counts(marks: list[ReviewFeedbackMark]) -> dict[str, int]:
    totals = {"impressed": 0, "dislike": 0, "horrible": 0, "slop": 0}
    for mark in marks:
        if mark.reaction in totals:
            totals[mark.reaction] += 1
        if mark.slop and mark.reaction in {"dislike", "horrible"}:
            totals["slop"] += 1
    return totals


def summary(db: Session, link: ReviewLink, content: dict[str, Any] | None) -> FeedbackSummary:
    rows = db.scalars(
        select(ReviewFeedback)
        .where(ReviewFeedback.review_link_id == link.id)
        .order_by(ReviewFeedback.created_at, ReviewFeedback.id)
    ).all()
    responses: list[ResponseSummary] = []
    lines: list[MarkedLine] = []
    totals = {"impressed": 0, "dislike": 0, "horrible": 0, "slop": 0}
    for index, row in enumerate(rows, start=1):
        marks = db.scalars(
            select(ReviewFeedbackMark)
            .where(ReviewFeedbackMark.feedback_id == row.id)
            .order_by(ReviewFeedbackMark.episode_index, ReviewFeedbackMark.ordinal)
        ).all()
        counts = _counts(list(marks))
        for key, value in counts.items():
            totals[key] += value
        responses.append(
            ResponseSummary(
                index=index,
                label=row.label,
                stars=row.stars,
                worked=row.worked,
                did_not=row.did_not,
                impressed=counts["impressed"],
                dislike=counts["dislike"],
                horrible=counts["horrible"],
                slop=counts["slop"],
            )
        )
        for mark in marks:
            speaker, text = _line(content, mark.episode_index, mark.ordinal)
            lines.append(
                MarkedLine(
                    response=index,
                    label=row.label,
                    key=f"e{mark.episode_index}-s{mark.ordinal:02d}",
                    speaker=speaker,
                    text=text,
                    reaction=mark.reaction or None,  # type: ignore[arg-type]
                    slop=mark.slop,
                    comment=mark.comment,
                )
            )
    return FeedbackSummary(
        responses=responses,
        impressed=totals["impressed"],
        dislike=totals["dislike"],
        horrible=totals["horrible"],
        slop=totals["slop"],
        lines=lines,
    )
