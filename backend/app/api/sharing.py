"""Authenticated owner sharing, bearer-link reading, and feedback on that stand."""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.audio import _mix, _spoken_script
from app.config import get_settings
from app.db import get_db
from app.errors import ProblemException, problem
from app.ingestion import anchors as anchor_tools
from app.ingestion.extract import page_size, render_page_png
from app.models import AudioTake, Document, ReviewLink, Run, Series, User
from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import Script
from app.schemas.sharing import (
    CreatedLink,
    CreateShare,
    EpisodeOptions,
    FeedbackState,
    LinkMetadata,
    MarkIn,
    OwnerLinks,
    ReaderAudio,
    ReaderEpisode,
    ReaderPage,
    ReaderSegment,
    ReaderSnapshot,
    ShareOptions,
    ShareSelection,
    SheetIn,
    TakeOption,
)
from app.security import _as_aware, require_admin
from app.series import episode_runs, load_plan
from app.sharing_feedback import (
    key_from,
    limit_writes,
    save_mark,
    save_sheet,
    state,
)
from app.sharing_feedback import (
    summary as feedback_summary,
)

router = APIRouter(prefix="/api", tags=["review-links"])
Kind = Literal["runs", "series"]
READY = {"completed", "in_review", "reviewed"}
# Invalid requests alone are limited. Bounded memory, no token values or DB writes.
_invalid_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_invalid_lock = Lock()


def _mutation(request: Request, user: User = Depends(require_admin)) -> User:
    origin = request.headers.get("origin")
    own = urlsplit(str(request.base_url))
    allowed = f"{own.scheme}://{own.netloc}"
    if (origin and origin != allowed) or request.headers.get("sec-fetch-site") == "cross-site":
        raise problem(403, "Forbidden", "Use the application to manage review links.")
    return user


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


def _target(db: Session, kind: Kind, target_id: str) -> tuple[str, list[tuple[int, str, Run]]]:
    if kind == "runs":
        run = db.get(Run, target_id)
        if run is None:
            raise problem(404, "Not found", "No such run.")
        if run.status not in READY:
            raise problem(409, "Noch kein fertiges Skript", "Der Durchlauf muss fertig sein.")
        title = run.name or run.document.title or "Podcast"
        return title, [(1, title, run)]
    series = db.get(Series, target_id)
    if series is None:
        raise problem(404, "Not found", "No such series.")
    if series.status != "completed":
        raise problem(409, "Serie noch nicht fertig", "Alle geplanten Skripte müssen fertig sein.")
    plan = load_plan(_store(), series)
    if plan is None or not plan.episodes:
        raise problem(409, "Kein Serienplan", "Die Serie hat keine fertigen Folgen.")
    runs = episode_runs(db, target_id)
    result = []
    for episode in plan.episodes:
        run = runs.get(episode.index)
        if run is None or run.status not in READY:
            raise problem(
                409, "Serie unvollständig", f"Folge {episode.index} ist noch nicht fertig."
            )
        result.append((episode.index, episode.title, run))
    return series.name or plan.title or "Podcast-Serie", result


def _script(db: Session, run: Run) -> Script:
    script = _spoken_script(db, run, _store())
    if not script.segments or not any(s.text.strip() for s in script.segments):
        raise problem(409, "Leeres Skript", "Ein leeres Skript kann nicht geteilt werden.")
    return script


def _eligible(take: AudioTake, run: Run) -> bool:
    return (
        take.run_id == run.id
        and take.status == "completed"
        and (take.request_json or {}).get("scope") == "full"
    )


def _take_content(take: AudioTake, run: Run) -> tuple[Script, dict[str, Any]]:
    store = _store()
    mix = _mix(take, store)
    if not _eligible(take, run) or mix is None:
        raise problem(409, "Keine fertige Aufnahme", "Wähle eine fertige vollständige Aufnahme.")
    if (
        not math.isfinite(mix.duration_s)
        or mix.duration_s <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", mix.blob)
        or mix.suffix != ".mp3"
        or not store.has_blob(mix.blob, mix.suffix)
        or not store.exists(take.script_hash)
    ):
        raise problem(409, "Aufnahme fehlt", "Die ausgewählte Aufnahme ist nicht verfügbar.")
    script = Script.model_validate(store.get_raw(take.script_hash))
    if not script.segments or not any(s.text.strip() for s in script.segments):
        raise problem(409, "Leeres Skript", "Die Aufnahme hat kein lesbares Skript.")
    return script, {"blob": mix.blob, "duration_s": mix.duration_s}


def _content(db: Session, kind: Kind, target_id: str, payload: ShareSelection) -> dict[str, Any]:
    _, runs = _target(db, kind, target_id)
    choices = {choice.index: choice for choice in payload.episodes}
    if len(choices) != len(payload.episodes) or set(choices) != {i for i, _, _ in runs}:
        raise problem(409, "Folgen geändert", "Bitte lade die vollständige Serienauswahl erneut.")
    if not payload.title.strip() or any(not c.title.strip() for c in payload.episodes):
        raise problem(422, "Titel fehlt", "Gib einen Titel für die geteilten Inhalte an.")
    episodes = []
    for index, _, run in runs:
        choice = choices[index]
        audio = None
        if choice.take_id:
            take = db.get(AudioTake, choice.take_id)
            if take is None:
                raise problem(409, "Aufnahme fehlt", "Bitte wähle eine verfügbare Aufnahme.")
            script, audio = _take_content(take, run)
        else:
            if not payload.acknowledge_missing_audio:
                raise problem(
                    409, "Audio fehlt", "Bestätige, dass du auch Skripte ohne Audio teilst."
                )
            script = _script(db, run)
        segments, pages = _freeze(db, run, script)
        episodes.append(
            {
                "index": index,
                "title": choice.title.strip(),
                "segments": segments,
                "audio": audio,
                "pages": pages,
            }
        )
    return {"schema_version": 2, "title": payload.title.strip(), "episodes": episodes}


def _parsed(db: Session, run: Run, script: Script) -> ParsedDocument | None:
    if not any(segment.anchors for segment in script.segments):
        return None
    document = db.get(Document, run.document_id)
    if document is None or not document.parsed_artifact_hash:
        raise problem(409, "Quelle fehlt", "Die belegten Seiten können nicht eingefroren werden.")
    try:
        return ParsedDocument.model_validate(_store().get_raw(document.parsed_artifact_hash))
    except (KeyError, ValueError, TypeError, OSError):
        raise problem(
            409, "Quelle fehlt", "Die belegten Seiten können nicht eingefroren werden."
        ) from None


def _freeze(
    db: Session, run: Run, script: Script
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parsed = _parsed(db, run, script)
    cited: set[int] = set()
    segments: list[dict[str, Any]] = []
    for ordinal, segment in enumerate(script.segments):
        citations: list[dict[str, Any]] = []
        if parsed is not None:
            for anchor in segment.anchors:
                outcome = anchor_tools.resolve(parsed, anchor)
                if not outcome.resolved or not outcome.rects:
                    continue
                rects = [
                    {"page": rect.page, "bbox": [float(value) for value in rect.bbox]}
                    for rect in outcome.rects
                ]
                citations.append({"rects": rects})
                cited.update(rect.page for rect in outcome.rects)
        segments.append(
            {
                "ordinal": ordinal,
                "speaker": segment.speaker,
                "text": segment.text,
                "citations": citations,
            }
        )
    return segments, _pages(db, run, parsed, cited)


def _pages(
    db: Session, run: Run, parsed: ParsedDocument | None, cited: set[int]
) -> list[dict[str, Any]]:
    if not cited or parsed is None:
        return []
    document = db.get(Document, run.document_id)
    source = get_settings().uploads_dir / f"{document.sha256}.pdf" if document else None
    if document is None or source is None or not source.is_file():
        raise problem(409, "Quelle fehlt", "Die PDF der belegten Seiten liegt nicht mehr vor.")
    frozen: list[dict[str, Any]] = []
    for page in sorted(cited):
        if page < 0 or page >= parsed.page_count:
            raise problem(409, "Quelle fehlt", "Eine belegte Seite liegt außerhalb des Dokuments.")
        try:
            png = render_page_png(source, page, dpi=130)
            width, height = (
                parsed.page_sizes[page]
                if page < len(parsed.page_sizes)
                else page_size(source, page)
            )
        except Exception:
            raise problem(
                409, "Quelle fehlt", "Eine belegte Seite konnte nicht eingefroren werden."
            ) from None
        frozen.append(
            {
                "page": page,
                "width": float(width),
                "height": float(height),
                "image": _store().put_blob(png, ".png"),
            }
        )
    return frozen


def _reader(
    content: dict[str, Any], created: datetime, expires: datetime, token: str
) -> ReaderSnapshot:
    return ReaderSnapshot(
        title=content["title"],
        created_at=_as_aware(created).isoformat(),
        expires_at=_as_aware(expires).isoformat(),
        episodes=[
            ReaderEpisode(
                index=e["index"],
                title=e["title"],
                segments=[
                    ReaderSegment.model_validate({**s, "ordinal": s.get("ordinal", index)})
                    for index, s in enumerate(e.get("segments") or [])
                ],
                audio=ReaderAudio(
                    url=f"/api/public/review-links/{token}/episodes/{e['index']}/audio",
                    duration_s=e["audio"]["duration_s"],
                )
                if e["audio"]
                else None,
                pages=[
                    ReaderPage(
                        page=page["page"],
                        width=page["width"],
                        height=page["height"],
                        url=(
                            f"/api/public/review-links/{token}/episodes/{e['index']}"
                            f"/pages/{page['image']}"
                        ),
                    )
                    for page in (e.get("pages") or [])
                ],
            )
            for e in content["episodes"]
        ],
    )


def _snapshot(row: ReviewLink) -> dict[str, Any] | None:
    try:
        content = _store().get_raw(row.snapshot_hash)
    except (KeyError, ValueError, TypeError, OSError):
        return None
    return content if isinstance(content, dict) else None


def _metadata(db: Session, row: ReviewLink) -> LinkMetadata:
    status: Literal["active", "expired", "revoked"] = "active"
    if row.revoked_at:
        status = "revoked"
    elif _as_aware(row.expires_at) <= datetime.now(UTC):
        status = "expired"
    return LinkMetadata(
        id=row.id,
        created_at=_as_aware(row.created_at).isoformat(),
        expires_at=_as_aware(row.expires_at).isoformat(),
        status=status,
        feedback=feedback_summary(db, row, _snapshot(row)),
    )


@router.get("/{kind}/{target_id}/review-link/options", response_model=ShareOptions)
def options(
    kind: Kind, target_id: str, db: Session = Depends(get_db), _user: User = Depends(require_admin)
) -> ShareOptions:
    title, runs = _target(db, kind, target_id)
    episodes = []
    for index, name, run in runs:
        current = _script(db, run)
        current_hash = hash_payload(
            {"kind": "script_reviewed", "payload": current.model_dump(mode="json")}
        )
        takes = db.scalars(
            select(AudioTake)
            .where(AudioTake.run_id == run.id)
            .order_by(AudioTake.created_at.desc())
        ).all()
        available = []
        for take in takes:
            if not _eligible(take, run):
                continue
            try:
                _, audio = _take_content(take, run)
            except (ProblemException, ValueError, TypeError, KeyError, OSError):
                # A missing/corrupt old recording cannot hide another ready take.
                continue
            available.append(
                TakeOption(
                    id=take.id,
                    created_at=_as_aware(take.created_at).isoformat(),
                    duration_s=audio["duration_s"],
                    older_script=take.script_hash != current_hash,
                )
            )
        episodes.append(EpisodeOptions(index=index, title=name, takes=available))
    return ShareOptions(title=title, episodes=episodes)


@router.get("/{kind}/{target_id}/review-link", response_model=OwnerLinks)
def owner_links(
    kind: Kind, target_id: str, db: Session = Depends(get_db), _user: User = Depends(require_admin)
) -> OwnerLinks:
    target = db.get(Run if kind == "runs" else Series, target_id)
    if target is None:
        raise problem(404, "Not found", "No such target.")
    rows = db.scalars(
        select(ReviewLink)
        .where(ReviewLink.target_kind == kind, ReviewLink.target_id == target_id)
        .order_by(ReviewLink.created_at.desc())
        .limit(20)
    ).all()
    return OwnerLinks(
        configured=bool(get_settings().review_public_origin),
        links=[_metadata(db, row) for row in rows],
    )


@router.post("/{kind}/{target_id}/review-link/preview")
def preview(
    kind: Kind,
    target_id: str,
    payload: ShareSelection,
    db: Session = Depends(get_db),
    _user: User = Depends(_mutation),
) -> dict[str, Any]:
    content = _content(db, kind, target_id, payload)
    now = datetime.now(UTC)
    # Preview audio and page images stay authenticated: public URLs are never granted here.
    stored = _store().put_raw("review_snapshot", content)
    view = _reader(content, now, now + timedelta(days=30), "preview")
    for episode in view.episodes:
        for page in episode.pages:
            image = page.url.rsplit("/", 1)[-1]
            page.url = f"/api/review-snapshots/{stored.hash}/pages/{image}"
    choices = {c.index: c for c in payload.episodes}
    for episode in view.episodes:
        choice = choices[episode.index]
        if episode.audio and choice.take_id:
            episode.audio.url = f"/api/audio/takes/{choice.take_id}/mix"
    return {"snapshot": view.model_dump(), "key": hash_payload(content)}


@router.post("/{kind}/{target_id}/review-link", response_model=CreatedLink, status_code=201)
def create(
    kind: Kind,
    target_id: str,
    payload: CreateShare,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(_mutation),
) -> CreatedLink:
    origin = get_settings().review_public_origin
    if not origin:
        raise problem(
            409,
            "Freigabe nicht konfiguriert",
            "Eine geprüfte öffentliche HTTPS-Adresse muss zuerst konfiguriert werden.",
        )
    # Serialize replacement, including validation, across SQLite sessions.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    content = _content(db, kind, target_id, payload)
    if hash_payload(content) != payload.preview_key:
        raise problem(
            409, "Inhalte geändert", "Prüfe die Vorschau erneut, bevor du den Link erstellst."
        )
    current = db.scalars(
        select(ReviewLink).where(
            ReviewLink.target_kind == kind,
            ReviewLink.target_id == target_id,
            ReviewLink.revoked_at.is_(None),
        )
    ).first()
    now = datetime.now(UTC)
    if current and _as_aware(current.expires_at) > now and payload.replace_link_id != current.id:
        raise problem(
            409, "Link bereits aktiv", "Bestätige ausdrücklich, dass du diesen Link ersetzt."
        )
    snapshot = _store().put_raw("review_snapshot", content)
    token = secrets.token_urlsafe(32)
    if current:
        current.revoked_at = now
        db.flush()
    row = ReviewLink(
        target_kind=kind,
        target_id=target_id,
        created_by=user.id,
        created_at=now,
        expires_at=now + timedelta(days=30),
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        snapshot_hash=snapshot.hash,
    )
    db.add(row)
    db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CreatedLink(link=_metadata(db, row), url=f"{origin}/r/{token}")


@router.delete("/review-links/{link_id}", status_code=204)
def revoke(
    link_id: str, db: Session = Depends(get_db), _user: User = Depends(_mutation)
) -> Response:
    row = db.get(ReviewLink, link_id)
    if row is None:
        raise problem(404, "Not found", "No such review link.")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
    return Response(status_code=204)


def _grant(db: Session, token: str, request: Request) -> ReviewLink:
    row = None
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        row = db.scalars(
            select(ReviewLink).where(
                ReviewLink.token_digest == hashlib.sha256(token.encode()).hexdigest()
            )
        ).first()
    if row and row.revoked_at is None and _as_aware(row.expires_at) > datetime.now(UTC):
        return row
    peer = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _invalid_lock:
        started, count = _invalid_attempts.get(peer, (now, 0))
        if now - started >= 60:
            started, count = now, 0
        _invalid_attempts[peer] = (started, count + 1)
        _invalid_attempts.move_to_end(peer)
        if len(_invalid_attempts) > 1024:
            _invalid_attempts.popitem(last=False)
    raise problem(
        429 if count >= 30 else 404,
        "Review-Link nicht verfügbar",
        "Bitte frage nach einem neuen Link.",
    )


@router.get("/public/review-links/{token}", response_model=ReaderSnapshot)
def read_share(token: str, request: Request, db: Session = Depends(get_db)) -> ReaderSnapshot:
    row = _grant(db, token, request)
    try:
        content = _store().get_raw(row.snapshot_hash)
        return _reader(content, row.created_at, row.expires_at, token)
    except (KeyError, ValueError, TypeError, OSError):
        raise problem(
            404, "Review-Link nicht verfügbar", "Bitte frage nach einem neuen Link."
        ) from None


@router.get("/public/review-links/{token}/episodes/{index}/audio")
def share_audio(
    token: str, index: int, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    row = _grant(db, token, request)
    try:
        content = _store().get_raw(row.snapshot_hash)
        episode = next(e for e in content["episodes"] if e["index"] == index)
        blob = episode["audio"]["blob"]
        if not re.fullmatch(r"[0-9a-f]{64}", blob):
            raise ValueError("Invalid stored blob")
        path = _store().blob_path(blob, ".mp3")
        if not path.is_file():
            raise ValueError("Missing audio")
    except (KeyError, ValueError, TypeError, OSError, StopIteration):
        raise problem(404, "Audio nicht verfügbar", "Bitte versuche es erneut.") from None
    return FileResponse(
        path, media_type="audio/mpeg", filename="podcast.mp3", content_disposition_type="inline"
    )


def _listed_image(content: dict[str, Any], image: str, episode_index: int | None = None) -> bool:
    if not re.fullmatch(r"[0-9a-f]{64}", image):
        return False
    for episode in content.get("episodes") or []:
        if episode_index is not None and episode.get("index") != episode_index:
            continue
        for page in episode.get("pages") or []:
            if page.get("image") == image:
                return True
    return False


def _png(image: str) -> FileResponse:
    path = _store().blob_path(image, ".png")
    if not path.is_file():
        raise problem(404, "Seite nicht verfügbar", "Bitte frage nach einem neuen Link.")
    return FileResponse(
        path, media_type="image/png", filename="page.png", content_disposition_type="inline"
    )


@router.get("/public/review-links/{token}/episodes/{index}/pages/{image}")
def share_page(
    token: str, index: int, image: str, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    row = _grant(db, token, request)
    try:
        content = _store().get_raw(row.snapshot_hash)
    except (KeyError, ValueError, TypeError, OSError):
        raise problem(404, "Seite nicht verfügbar", "Bitte frage nach einem neuen Link.") from None
    if not isinstance(content, dict) or not _listed_image(content, image, index):
        raise problem(404, "Seite nicht verfügbar", "Bitte frage nach einem neuen Link.")
    return _png(image)


@router.get("/review-snapshots/{digest}/pages/{image}")
def preview_page(
    digest: str,
    image: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> FileResponse:
    del db
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise problem(404, "Seite nicht verfügbar", "Diese Vorschauseite gibt es nicht.")
    try:
        if _store().kind_of(digest) != "review_snapshot":
            raise KeyError(digest)
        content = _store().get_raw(digest)
    except (KeyError, ValueError, TypeError, OSError):
        raise problem(404, "Seite nicht verfügbar", "Diese Vorschauseite gibt es nicht.") from None
    if not isinstance(content, dict) or not _listed_image(content, image):
        raise problem(404, "Seite nicht verfügbar", "Diese Vorschauseite gibt es nicht.")
    return _png(image)


@router.get("/public/review-links/{token}/feedback", response_model=FeedbackState)
def read_feedback(token: str, request: Request, db: Session = Depends(get_db)) -> FeedbackState:
    row = _grant(db, token, request)
    return state(db, row, key_from(request))


@router.put("/public/review-links/{token}/feedback/marks", response_model=FeedbackState)
def write_mark(
    token: str, payload: MarkIn, request: Request, db: Session = Depends(get_db)
) -> FeedbackState:
    row = _grant(db, token, request)
    limit_writes(request)
    key = key_from(request)
    content = _snapshot(row)
    if content is None:
        raise problem(404, "Review-Link nicht verfügbar", "Bitte frage nach einem neuen Link.")
    save_mark(db, row, key, content, payload)
    db.commit()
    return state(db, row, key)


@router.put("/public/review-links/{token}/feedback/sheet", response_model=FeedbackState)
def write_sheet(
    token: str, payload: SheetIn, request: Request, db: Session = Depends(get_db)
) -> FeedbackState:
    row = _grant(db, token, request)
    limit_writes(request)
    key = key_from(request)
    save_sheet(db, row, key, payload)
    db.commit()
    return state(db, row, key)
