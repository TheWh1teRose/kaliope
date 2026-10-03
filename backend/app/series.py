"""Series of episodes: the plan, each episode's inputs and the series checks.

A series is a planner run (``episode_index`` 0) and one ordinary run per
episode. This module turns the stored plan and the episode runs into the inputs
the nodes take — an ``EpisodeBrief`` for the early nodes and a
``SeriesContext`` for the script — and computes the series-level checks. It
holds no execution logic; the worker drives the stages.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Run, Series
from app.pipeline import catalogue
from app.pipeline.feedback import last_row_for_key
from app.pipeline.framework.artifacts import ArtifactStore, hash_payload
from app.pipeline.framework.registry import Flow, FlowNode, get_node
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import (
    EarlierEpisode,
    EpisodeBrief,
    EpisodeOutline,
    EpisodeSummary,
    Outline,
    Script,
    SeriesContext,
    SeriesPlan,
)

#: Series checks warn below this share of narratable words given to an episode.
MIN_ASSIGNED_SHARE = 0.9

#: Episode runs that finished their script; review keeps a run finished.
SCRIPT_DONE = frozenset({"completed", "in_review", "reviewed"})
#: Series states in which no worker job is meant to be running.
SERIES_IDLE = frozenset({"planned", "completed", "failed", "stopped"})
#: Nodes that pause for a person; a series cannot run an episode flow with one yet.
PAUSING_NODES = frozenset({"human_feedback"})


def series_channel(series_id: str) -> str:
    """The event-bus channel a series' events are published on."""
    return f"series:{series_id}"


def truncate_flow(flow: Flow, key: str) -> Flow:
    """The flow up to and including the last node that publishes ``key``."""
    last = -1
    for index, entry in enumerate(flow.nodes):
        if get_node(entry.node).produces == key:
            last = index
    if last < 0:
        raise KeyError(f"flow '{flow.id}' has no node that publishes '{key}'")
    nodes: list[FlowNode] = flow.nodes[: last + 1]
    return flow.model_copy(update={"nodes": nodes})


def load_plan(store: ArtifactStore, series: Series) -> SeriesPlan | None:
    if not series.plan_artifact_hash or not store.exists(series.plan_artifact_hash):
        return None
    return SeriesPlan.model_validate(store.get_raw(series.plan_artifact_hash))


def plan_run(session: Session, series_id: str) -> Run | None:
    """The newest planner run of a series."""
    return session.scalars(
        select(Run)
        .where(Run.series_id == series_id, Run.episode_index == 0)
        .order_by(Run.created_at.desc())
    ).first()


def episode_run_name(series_name: str | None, index: int) -> str | None:
    """``"<Name> Teil N"`` when the series was named; otherwise no name of its own."""
    if not series_name:
        return None
    return f"{series_name} Teil {index}"


def apply_episode_names(session: Session, series: Series) -> None:
    """Point every episode run at the series name. An unnamed series clears them."""
    for index, run in episode_runs(session, series.id).items():
        run.name = episode_run_name(series.name, index)


def episode_runs(session: Session, series_id: str) -> dict[int, Run]:
    """The newest run of every episode, keyed by its 1-based index."""
    rows = session.scalars(
        select(Run)
        .where(Run.series_id == series_id, Run.episode_index > 0)
        .order_by(Run.created_at)
    ).all()
    newest: dict[int, Run] = {}
    for row in rows:
        assert row.episode_index is not None
        newest[row.episode_index] = row
    return newest


def artifact_for(
    session: Session, store: ArtifactStore, run: Run, key: str, model: type[Any]
) -> Any | None:
    """The last artifact the run published under ``key``, or ``None``."""
    flow = catalogue.flows(session).get(run.flow_id)
    if flow is None:
        return None
    row = last_row_for_key(session, run_id=run.id, flow=flow, key=key)
    if row is None or not row.artifact_hash or not store.exists(row.artifact_hash):
        return None
    return model.model_validate(store.get_raw(row.artifact_hash))


def final_script(session: Session, store: ArtifactStore, run: Run) -> Script | None:
    """The episode's script as it stands, with the reviewer's edits applied."""
    from app.api.review import edited_texts

    script: Script | None = artifact_for(session, store, run, "script", Script)
    if script is None:
        return None
    edits = edited_texts(session, run.id)
    if not edits:
        return script
    return script.model_copy(
        update={
            "segments": [
                segment.model_copy(update={"text": edits.get(segment.id, segment.text)})
                for segment in script.segments
            ]
        }
    )


def context_hash(context: SeriesContext) -> str:
    return hash_payload(context.model_dump(mode="json"))


def series_context_for(
    session: Session, store: ArtifactStore, series_id: str, plan: SeriesPlan, index: int
) -> SeriesContext:
    runs = episode_runs(session, series_id)
    outlines: list[EpisodeOutline] = []
    earlier: list[EarlierEpisode] = []
    for episode in plan.episodes:
        run = runs.get(episode.index)
        if run is None or episode.index == index:
            continue
        outline = artifact_for(session, store, run, "outline", Outline)
        if outline is not None:
            outlines.append(
                EpisodeOutline(
                    index=episode.index,
                    title=episode.title,
                    beats=[(beat.title, beat.summary or "") for beat in outline.beats],
                )
            )
        if episode.index < index:
            script = final_script(session, store, run)
            if script is not None:
                earlier.append(
                    EarlierEpisode(
                        index=episode.index,
                        title=episode.title,
                        lines=[f"{s.speaker}: {s.text}" for s in script.segments],
                    )
                )
    return SeriesContext(
        series_title=plan.title,
        through_line=plan.through_line,
        terms=plan.terms,
        episode_index=index,
        episodes=[
            EpisodeSummary(index=e.index, title=e.title, role=e.role, summary=e.summary)
            for e in plan.episodes
        ],
        outlines=outlines,
        earlier=earlier,
    )


def brief_for(plan: SeriesPlan, index: int) -> EpisodeBrief:
    return EpisodeBrief.from_plan(plan, index)


def coverage_check(
    parsed: ParsedDocument, plan: SeriesPlan, scripts: dict[int, Script]
) -> dict[str, Any]:
    """S1: how much of the document the series covers.

    ``assigned_share`` is the share of narratable words the plan gave to some
    episode. Per section it lists the episodes that hold its passages and
    whether any episode's script cites it. Warns below 90 % assigned or when a
    section with assigned passages is cited by no script.
    """
    narratable = parsed.narratable_blocks()
    total = sum(b.word_count() for b in narratable) or 1
    home: dict[str, int] = {}
    for episode in plan.episodes:
        for block_id in episode.block_ids:
            home[block_id] = episode.index
    assigned = sum(b.word_count() for b in narratable if b.id in home)
    cited: set[str] = {
        anchor.block_id
        for script in scripts.values()
        for segment in script.segments
        for anchor in segment.anchors
    }

    titles = {s.id: s.title for s in (parsed.sections or [])}
    sections: list[dict[str, Any]] = []
    by_section: dict[str | None, list[str]] = {}
    for block in narratable:
        by_section.setdefault(block.section_id, []).append(block.id)
    uncited: list[str] = []
    for section_id, ids in by_section.items():
        episodes = sorted({home[i] for i in ids if i in home})
        was_cited = any(i in cited for i in ids)
        title = titles.get(section_id or "", "") or "(ohne Abschnitt)"
        sections.append(
            {"title": title, "episodes": episodes, "cited": was_cited, "blocks": len(ids)}
        )
        if episodes and scripts and not was_cited:
            uncited.append(title)

    share = round(assigned / total, 3)
    status = "pass" if share >= MIN_ASSIGNED_SHARE and not uncited else "warn"
    return {
        "id": "S1",
        "name": "coverage",
        "status": status,
        "assigned_share": share,
        "cited_blocks": len(cited & set(home)),
        "assigned_blocks": len(home),
        "unassigned": [u.model_dump(mode="json") for u in plan.unassigned],
        "uncited_sections": uncited,
        "sections": sections,
    }


def stage_progress(session: Session, series: Series) -> dict[str, int]:
    """Counts for the progress strip: outlines done and scripts done."""
    runs = episode_runs(session, series.id)
    flows = catalogue.flows(session)
    outlined = 0
    written = 0
    for run in runs.values():
        if run.status in SCRIPT_DONE:
            written += 1
            outlined += 1
            continue
        flow = flows.get(run.flow_id)
        if flow is not None and last_row_for_key(session, run_id=run.id, flow=flow, key="outline"):
            outlined += 1
    return {"outlined": outlined, "written": written, "episodes": len(runs)}
