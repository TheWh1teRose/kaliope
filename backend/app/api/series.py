"""Series endpoints: start a series, approve or redo its plan, watch it, export it.

A series is a planner run and one ordinary run per episode (see
``app.series``). Everything per episode — review, gates, the run page, the
Markdown export — goes through the run endpoints unchanged; this module adds
what spans the episodes.
"""

from __future__ import annotations

import io
import json
import queue
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.documents import load_parsed_document
from app.api.review import edited_texts
from app.api.runs import _load_run_output, _to_markdown, _topology, build_run_graph
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.events import bus
from app.lang.resources import words_per_minute
from app.models import Document, Run, Series, User
from app.naming import clean_name
from app.pipeline import catalogue
from app.pipeline.catalogue import CatalogueError
from app.pipeline.formats import DEFAULT_AUDIENCE
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.registry import Flow, flow_purpose
from app.pipeline.nodes.content_budget import DEFAULT_MIN_COMPRESSION
from app.pipeline.nodes.series_plan import MAX_EPISODES, MIN_EPISODES
from app.schemas.api import (
    CreateSeriesRequest,
    DocumentBudgetOut,
    NameUpdate,
    ReplanRequest,
    RunGraphNodeOut,
    RunGraphOut,
    SeriesEpisodeOut,
    SeriesGraphEpisodeOut,
    SeriesGraphOut,
    SeriesOut,
)
from app.security import current_user
from app.series import (
    PAUSING_NODES,
    SERIES_IDLE,
    apply_episode_names,
    episode_run_name,
    episode_runs,
    load_plan,
    plan_run,
    series_channel,
    stage_progress,
)
from app.worker import worker

router = APIRouter(prefix="/api", tags=["series"])

_HEARTBEAT_SECONDS = 15.0
#: Series events after which the stream closes itself.
_TERMINAL_EVENTS = frozenset({"series.completed", "series.failed", "series.planned"})


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


# ------------------------------------------------------------------ budget


@router.get("/documents/{document_id}/budget", response_model=DocumentBudgetOut)
def document_budget(
    document_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> DocumentBudgetOut:
    """How many minutes the document supports, as the content budget counts them."""
    document = _require_document(db, document_id)
    parsed = load_parsed_document(document)
    narratable = parsed.narratable_word_count()
    wpm = words_per_minute(parsed.language)
    return DocumentBudgetOut(
        document_id=document_id,
        narratable_words=narratable,
        words_per_minute=wpm,
        min_compression=DEFAULT_MIN_COMPRESSION,
        max_supportable_minutes=round(narratable / wpm / DEFAULT_MIN_COMPRESSION, 2),
    )


# ------------------------------------------------------------------ series


@router.post("/series", response_model=SeriesOut, status_code=201)
def create_series(
    payload: CreateSeriesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> SeriesOut:
    document = _require_document(db, payload.document_id)
    if document.parse_status != "parsed":
        raise problem(
            409,
            "Document is not parsed",
            f"Parse status is '{document.parse_status}'. Wait for parsing to finish first.",
        )
    flow = _flow(db, payload.flow_id)
    if flow_purpose(flow) != "episode":
        raise problem(422, "Not an episode flow", f"'{flow.id}' makes no episode script.")
    pausing = sorted({n.node for n in flow.nodes} & PAUSING_NODES)
    if pausing:
        raise problem(
            422,
            "Flow not supported in a series yet",
            f"'{flow.id}' pauses for notes ({', '.join(pausing)}); a series cannot run it yet.",
        )
    plan_flow = _flow(db, payload.plan_flow_id)
    if flow_purpose(plan_flow) != "series_plan":
        raise problem(422, "Not a planner flow", f"'{plan_flow.id}' does not plan a series.")
    try:
        format_spec = catalogue.get_format_spec(db, payload.format_id)
    except CatalogueError as exc:
        raise problem(404, "No such format", str(exc)) from exc

    minutes = payload.minutes_per_episode or format_spec.target_minutes
    _check_request(payload.episodes, minutes)

    series = Series(
        document_id=document.id,
        flow_id=flow.id,
        flow_version=flow.version,
        plan_flow_id=plan_flow.id,
        request_json={
            "episodes": payload.episodes,
            "minutes_per_episode": minutes,
            "hint": (payload.hint or "").strip() or None,
            "approved": False,
            "language": payload.language,
            "force": payload.force,
        },
        format_spec_json=format_spec.model_dump(mode="json"),
        audience_spec_json=(payload.audience_spec or DEFAULT_AUDIENCE).model_dump(mode="json"),
        status="queued",
        created_by=user.id,
        name=clean_name(payload.name),
    )
    db.add(series)
    db.flush()
    _add_plan_run(db, series, plan_flow)
    db.commit()
    worker.submit_series(series.id)
    return _series_out(db, series)


@router.get("/series", response_model=list[SeriesOut])
def list_series(
    document_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[SeriesOut]:
    statement = select(Series).order_by(Series.created_at.desc()).limit(max(1, min(limit, 500)))
    if document_id:
        statement = statement.where(Series.document_id == document_id)
    return [_series_out(db, row) for row in db.scalars(statement).all()]


@router.get("/series/{series_id}", response_model=SeriesOut)
def get_series(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> SeriesOut:
    return _series_out(db, _require_series(db, series_id))


@router.patch("/series/{series_id}", response_model=SeriesOut)
def rename_series(
    series_id: str,
    payload: NameUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> SeriesOut:
    """Change only the display name, and retitle the episode runs from it."""
    series = _require_series(db, series_id)
    series.name = clean_name(payload.name)
    apply_episode_names(db, series)
    db.commit()
    return _series_out(db, series)


@router.post("/series/{series_id}/approve", response_model=SeriesOut)
def approve_plan(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> SeriesOut:
    """Release a series held at its plan: the episodes are outlined and written."""
    series = _require_series(db, series_id)
    if series.status != "planned":
        raise problem(409, "Not waiting for approval", f"The series is '{series.status}'.")
    request = dict(series.request_json or {})
    request["approved"] = True
    series.request_json = request
    series.status = "queued"
    db.commit()
    bus.clear(series_channel(series.id))
    worker.submit_series(series.id)
    return _series_out(db, series)


@router.post("/series/{series_id}/replan", response_model=SeriesOut)
def replan(
    series_id: str,
    payload: ReplanRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> SeriesOut:
    """Plan again with another count, length or hint, while the series waits at its plan."""
    series = _require_series(db, series_id)
    has_episodes = bool(episode_runs(db, series_id))
    if series.status not in {"planned", "failed"} or has_episodes:
        raise problem(
            409,
            "Plan is final",
            "A series can be planned again only while it waits at its plan, before any "
            "episode has started.",
        )
    if worker.series_active(series_id):
        raise problem(409, "Series is running", "Wait for the current step to finish.")
    request = dict(series.request_json or {})
    if payload.episodes is not None:
        request["episodes"] = payload.episodes if payload.episodes > 0 else None
    if payload.minutes_per_episode is not None:
        request["minutes_per_episode"] = payload.minutes_per_episode
    if payload.hint is not None:
        request["hint"] = payload.hint.strip() or None
    _check_request(request.get("episodes"), int(request["minutes_per_episode"]))
    request["approved"] = False
    series.request_json = request
    series.status = "queued"
    series.error = None
    series.finished_at = None
    _add_plan_run(db, series, _flow(db, series.plan_flow_id))
    db.commit()
    bus.clear(series_channel(series.id))
    worker.submit_series(series.id)
    return _series_out(db, series)


@router.post("/series/{series_id}/resume", response_model=SeriesOut)
def resume_series(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> SeriesOut:
    """Continue a series that failed or was cut off; finished steps are not repeated."""
    series = _require_series(db, series_id)
    if series.status in {"completed", "planned"}:
        raise problem(409, "Nothing to resume", f"The series is '{series.status}'.")
    if worker.series_active(series_id):
        raise problem(409, "Series is running", "It is being worked on right now.")
    series.status = "queued"
    series.error = None
    series.finished_at = None
    db.commit()
    bus.clear(series_channel(series.id))
    worker.submit_series(series.id)
    return _series_out(db, series)


@router.get("/series/{series_id}/graph", response_model=SeriesGraphOut)
def series_graph(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> SeriesGraphOut:
    """The whole pipeline: the planner run's nodes and every episode's nodes."""
    series = _require_series(db, series_id)
    planner = plan_run(db, series_id)
    plan = load_plan(_store(), series)
    runs = episode_runs(db, series_id)
    episode_flow = catalogue.flows(db).get(series.flow_id)
    episodes: list[SeriesGraphEpisodeOut] = []
    if plan is not None:
        for episode in plan.episodes:
            run = runs.get(episode.index)
            if run is not None:
                graph = build_run_graph(db, run)
            elif episode_flow is not None:
                graph = _pending_graph(episode_flow)
            else:
                continue
            episodes.append(
                SeriesGraphEpisodeOut(
                    index=episode.index,
                    title=episode.title,
                    run_id=run.id if run is not None else None,
                    graph=graph,
                )
            )
    return SeriesGraphOut(
        series_id=series_id,
        status=series.status,
        plan=build_run_graph(db, planner) if planner is not None else None,
        episodes=episodes,
    )


@router.get("/series/{series_id}/events")
def series_events(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> StreamingResponse:
    """Every event of the series and of its runs, each tagged with its episode."""
    series = _require_series(db, series_id)
    idle = series.status in SERIES_IDLE and not worker.series_active(series_id)
    status = series.status
    channel = series_channel(series_id)
    subscriber, replay = bus.subscribe(channel)

    def stream() -> Iterator[str]:
        try:
            yield ": connected\n\n"
            for event in replay:
                yield event.to_sse()
                if event.type in _TERMINAL_EVENTS:
                    return
            if idle:
                yield f'event: series.{status}\ndata: {{"series_id": "{series_id}"}}\n\n'
                return
            while True:
                try:
                    event = subscriber.get(timeout=_HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield event.to_sse()
                if event.type in _TERMINAL_EVENTS:
                    return
        finally:
            bus.unsubscribe(channel, subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/series/{series_id}/export")
def export_series(
    series_id: str,
    format: str = "zip",
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> Response:
    """One Markdown file per finished episode, with the reviewers' edits applied."""
    if format != "zip":
        raise problem(422, "Unsupported format", "Only 'zip' is supported in this build.")
    series = _require_series(db, series_id)
    plan = load_plan(_store(), series)
    runs = episode_runs(db, series_id)
    document = db.get(Document, series.document_id)
    titles = {e.index: e.title for e in plan.episodes} if plan is not None else {}
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in sorted(runs):
            run = runs[index]
            if run.status not in {"completed", "in_review", "reviewed"}:
                continue
            parsed, script = _load_run_output(db, run)
            body = _to_markdown(run, document, parsed, script, edited_texts(db, run.id))
            label = run.name or titles.get(index, "")
            heading = f"# Folge {index}: {label}".rstrip(": ")
            name = f"folge-{index:02d}-{_slug(label)}.md".replace("-.md", ".md")
            archive.writestr(name, f"{heading}\n\n{body}")
            written += 1
        if plan is not None:
            archive.writestr("serie.json", json.dumps(plan.model_dump(mode="json"), indent=2))
    if not written:
        raise problem(409, "Nothing to export", "No episode of this series has a script yet.")
    stem = _slug(series.name or "") or series_id
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kalliope-serie-{stem}.zip"'},
    )


# ----------------------------------------------------------------- helpers


def _check_request(episodes: int | None, minutes: int) -> None:
    if minutes <= 0:
        raise problem(422, "Invalid length", "minutes_per_episode must be greater than zero.")
    if episodes is not None and not MIN_EPISODES <= episodes <= MAX_EPISODES:
        raise problem(
            422,
            "Invalid episode count",
            f"A series has {MIN_EPISODES} to {MAX_EPISODES} episodes; leave it empty to let "
            "the planner choose.",
        )


def _add_plan_run(db: Session, series: Series, plan_flow: Flow) -> Run:
    request = dict(series.request_json or {})
    minutes = int(request["minutes_per_episode"])
    episodes = request.get("episodes") or MIN_EPISODES
    flow_row = catalogue.flow_row(db, plan_flow.id)
    run = Run(
        document_id=series.document_id,
        flow_id=plan_flow.id,
        flow_version=plan_flow.version,
        flow_revision=flow_row.revision if flow_row else 0,
        config_json={
            "target_minutes": minutes * episodes,
            "language": request.get("language"),
            "force": bool(request.get("force")),
            "series_request": {
                "episodes": request.get("episodes"),
                "minutes_per_episode": minutes,
                "hint": request.get("hint"),
            },
        },
        format_spec_json=series.format_spec_json,
        audience_spec_json=series.audience_spec_json,
        status="queued",
        created_by=series.created_by,
        series_id=series.id,
        episode_index=0,
    )
    db.add(run)
    db.flush()
    return run


def _pending_graph(flow: Flow) -> RunGraphOut:
    nodes, edges, seeds = _topology(flow, present_seeds=("episode_brief", "series_context"))
    return RunGraphOut(
        run_id="",
        flow_id=flow.id,
        flow_version=flow.version,
        status="pending",
        nodes=[RunGraphNodeOut(**node.model_dump(), status="pending") for node in nodes],
        edges=edges,
        seeds=seeds,
    )


def _series_out(db: Session, series: Series) -> SeriesOut:
    store = _store()
    plan = load_plan(store, series)
    planner = plan_run(db, series.id)
    runs = episode_runs(db, series.id)
    document = db.get(Document, series.document_id)
    all_runs = db.scalars(select(Run).where(Run.series_id == series.id)).all()
    episodes: list[SeriesEpisodeOut] = []
    if plan is not None:
        for episode in plan.episodes:
            run = runs.get(episode.index)
            episodes.append(
                SeriesEpisodeOut(
                    index=episode.index,
                    title=episode.title,
                    name=(
                        run.name
                        if run is not None
                        else episode_run_name(series.name, episode.index)
                    ),
                    role=episode.role,
                    target_minutes=episode.target_minutes,
                    run_id=run.id if run is not None else None,
                    status=run.status if run is not None else "pending",
                    total_cost_usd=run.total_cost_usd if run is not None else 0.0,
                    error=_first_line(run.error) if run is not None else None,
                )
            )
    return SeriesOut(
        id=series.id,
        document_id=series.document_id,
        document_title=(document.title or document.filename) if document else None,
        name=series.name,
        flow_id=series.flow_id,
        flow_version=series.flow_version,
        plan_flow_id=series.plan_flow_id,
        status=series.status,
        error=series.error,
        created_at=_iso(series.created_at) or "",
        started_at=_iso(series.started_at),
        finished_at=_iso(series.finished_at),
        request=dict(series.request_json or {}),
        plan=plan.model_dump(mode="json") if plan is not None else None,
        checks=dict(series.checks_json or {}),
        plan_run_id=planner.id if planner is not None else None,
        plan_run_status=planner.status if planner is not None else None,
        plan_cost_usd=sum(r.total_cost_usd for r in all_runs if r.episode_index == 0),
        total_cost_usd=round(sum(r.total_cost_usd for r in all_runs), 8),
        episodes=episodes,
        progress=stage_progress(db, series),
        active=worker.series_active(series.id),
    )


def _flow(db: Session, flow_id: str) -> Flow:
    try:
        return catalogue.get_flow(db, flow_id)
    except CatalogueError as exc:
        raise problem(404, "No such flow", str(exc)) from exc


def _require_document(db: Session, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise problem(404, "No such document", f"Document '{document_id}' does not exist.")
    return document


def _require_series(db: Session, series_id: str) -> Series:
    series = db.get(Series, series_id)
    if series is None:
        raise problem(404, "No such series", f"Series '{series_id}' does not exist.")
    return series


def _slug(text: str) -> str:
    lowered = text.lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(umlaut, plain)
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")[:48]


def _first_line(error: str | None) -> str | None:
    """The message of a stored error, without the traceback after it."""
    return (error or "").split("\n\n")[0] or None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
