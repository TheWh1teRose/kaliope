"""Data downloads: everything the pipeline graph shows of a run or a series, as one zip.

The script export hands over the finished text. This one hands over how it was
made — every node with its settings, what it was given, what it produced, what
it cost and what the gates found — so the whole run can be given to a model to
read. It reads only what the graph view already reads: the run's node rows,
its manifest and the artifact store. Nothing about the people involved goes in:
no account ids, names or e-mail addresses, and no reviewer comments.

A bundle holds, per run:

- ``run.md``: the run node by node, readable on its own;
- ``run.json``: the same, structured;
- ``nodes/NN-<node>.json``: each node's complete output;
- ``seeds/<key>.json``: each value the run started from;
- ``script.md``: the final script with the reviewers' edits, once there is one.

A series bundle has ``series.md`` and ``series.json`` (the plan: terms and
episode outlines) and one such folder for the planner and for each episode.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.review import segment_states
from app.api.runs import (
    _load_run_output,
    _seed_values,
    _to_markdown,
    build_run_graph,
    gate_details,
)
from app.config import get_settings
from app.db import get_db
from app.errors import ProblemException, problem
from app.models import Document, Run, Series, User
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.api import RunGraphNodeOut, RunGraphOut
from app.security import current_user
from app.series import episode_runs, load_plan, plan_run

router = APIRouter(prefix="/api", tags=["bundles"])

#: Bumped when the layout of a bundle changes, so a reader can tell versions apart.
BUNDLE_VERSION = 1
#: Longest node output written out in full inside ``run.md``. Larger outputs
#: are only referenced there; their file in ``nodes/`` is always complete.
_INLINE_CHARS = 20_000
#: Run settings that name a person rather than describe the run.
_PERSONAL_KEYS = frozenset({"stopped_by"})

_RUN_README = """# Kalliope: Daten eines Laufs

Dieses Archiv enthält alles, was die Pipeline-Ansicht dieses Laufs zeigt, zum
Weitergeben an ein KI-Modell. Ein Lauf macht aus einem Dokument ein Skript; er
besteht aus Knoten (nodes), die nacheinander laufen. Jeder Knoten liest Werte
früherer Knoten oder Startwerte (seeds) und veröffentlicht genau einen Wert.

- `run.md` – der Lauf Knoten für Knoten: Einstellungen, Eingaben, Ausgabe,
  Status, Modell, Tokens, Kosten, Dauer, Fehler und Gate-Befunde.
- `run.json` – dieselben Angaben strukturiert. Werte stehen in eigenen Dateien;
  `file` verweist auf sie.
- `nodes/NN-<knoten>.json` – die vollständige Ausgabe jedes Knotens.
- `seeds/<schlüssel>.json` – die Startwerte des Laufs.
- `script.md` – das fertige Skript mit den Änderungen der Prüfer, falls es eins gibt.

Eingaben eines Knotens sind keine Kopien: `nodes/02-outline.json` ist sowohl die
Ausgabe von `outline` als auch die Eingabe jedes Knotens, der `outline` liest.
"""

_SERIES_README = """# Kalliope: Daten einer Serie

Dieses Archiv enthält alles, was die Pipeline-Ansicht dieser Serie zeigt, zum
Weitergeben an ein KI-Modell. Eine Serie teilt ein Dokument in Folgen: Ein
Planungslauf schreibt den Serienplan (Begriffe und Folgen-Gliederung), danach
macht je ein eigener Lauf pro Folge das Skript.

- `series.md` – der Serienplan lesbar: Bogen, Begriffe, jede Folge mit Ziel,
  Zusammenfassung und Lernzielen, dazu Status und Kosten jeder Folge.
- `series.json` – Serie und Plan strukturiert.
- `plan/` – der Planungslauf.
- `episode-NN/` – der Lauf jeder Folge.

Jeder Lauf-Ordner ist gleich aufgebaut:

- `run.md` – der Lauf Knoten für Knoten: Einstellungen, Eingaben, Ausgabe,
  Status, Modell, Tokens, Kosten, Dauer, Fehler und Gate-Befunde.
- `run.json` – dieselben Angaben strukturiert; `file` verweist auf Werte-Dateien.
- `nodes/NN-<knoten>.json` – die vollständige Ausgabe jedes Knotens.
- `seeds/<schlüssel>.json` – die Startwerte des Laufs, etwa der Ausschnitt des
  Plans für diese Folge (`episode_brief`) und was sie von den anderen Folgen
  sieht (`series_context`).
- `script.md` – das fertige Skript mit den Änderungen der Prüfer, falls es eins gibt.
"""


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


@router.get("/runs/{run_id}/bundle")
def run_bundle(
    run_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> Response:
    """One run's data, node by node, as a zip."""
    run = db.get(Run, run_id)
    if run is None:
        raise problem(404, "No such run", f"Run '{run_id}' does not exist.")
    if run.trashed_at is not None:
        raise problem(409, "Run is in the trash", "Restore the run before downloading its data.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", _RUN_README)
        write_run(archive, "", db, run)
    return _zip(buffer, f"kalliope-lauf-{_slug(run.name or '') or run.id}-daten.zip")


@router.get("/series/{series_id}/bundle")
def series_bundle(
    series_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> Response:
    """A series' plan and the data of its planner and of every episode, as a zip."""
    series = db.get(Series, series_id)
    if series is None:
        raise problem(404, "No such series", f"Series '{series_id}' does not exist.")
    if series.trashed_at is not None:
        raise problem(
            409, "Series is in the trash", "Restore the series before downloading its data."
        )
    store = _store()
    plan = load_plan(store, series)
    planner = plan_run(db, series.id)
    runs = episode_runs(db, series.id)
    document = db.get(Document, series.document_id)

    episodes: list[dict[str, Any]] = []
    for episode in plan.episodes if plan is not None else []:
        run = runs.get(episode.index)
        episodes.append(
            {
                "index": episode.index,
                "title": episode.title,
                "name": run.name if run is not None else None,
                "run_id": run.id if run is not None else None,
                "status": run.status if run is not None else "pending",
                "total_cost_usd": run.total_cost_usd if run is not None else 0.0,
                "folder": f"episode-{episode.index:02d}/" if run is not None else None,
            }
        )
    every_run = [planner, *runs.values()] if planner is not None else list(runs.values())
    data = {
        "bundle_version": BUNDLE_VERSION,
        "kind": "series",
        "series": {
            "id": series.id,
            "name": series.name,
            "status": series.status,
            "error": series.error,
            "flow_id": series.flow_id,
            "flow_version": series.flow_version,
            "plan_flow_id": series.plan_flow_id,
            "document": _document(document),
            "request": _without_personal(series.request_json),
            "format_spec": series.format_spec_json,
            "audience_spec": series.audience_spec_json,
            "created_at": _iso(series.created_at),
            "started_at": _iso(series.started_at),
            "finished_at": _iso(series.finished_at),
            "total_cost_usd": round(sum(r.total_cost_usd for r in every_run), 8),
            "checks": series.checks_json or {},
        },
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "plan_run": (
            {"run_id": planner.id, "status": planner.status, "folder": "plan/"}
            if planner is not None
            else None
        ),
        "episodes": episodes,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", _SERIES_README)
        archive.writestr("series.json", _dump(data))
        archive.writestr("series.md", _series_markdown(data))
        if planner is not None:
            write_run(archive, "plan/", db, planner)
        for index in sorted(runs):
            write_run(archive, f"episode-{index:02d}/", db, runs[index])
    stem = _slug(series.name or (plan.title if plan else "")) or series.id
    return _zip(buffer, f"kalliope-serie-{stem}-daten.zip")


# -------------------------------------------------------------------- a run


def write_run(archive: zipfile.ZipFile, prefix: str, db: Session, run: Run) -> None:
    """Write one run's files into ``archive`` under ``prefix``."""
    store = _store()
    graph = build_run_graph(db, run)
    node_config = (run.config_json or {}).get("node_config") or {}
    records = {
        record.get("name"): record
        for record in ((run.manifest_json or {}).get("nodes") or [])
        if isinstance(record, dict)
    }
    document = db.get(Document, run.document_id)

    seeds: list[dict[str, Any]] = []
    for key, value in _seed_values(db, run).items():
        path = f"seeds/{key}.json"
        archive.writestr(prefix + path, _dump(value))
        seeds.append({"key": key, "file": path})
    seed_files = {seed["key"]: seed["file"] for seed in seeds}

    files: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for position, node in enumerate(graph.nodes, start=1):
        payload = (
            store.get_raw(node.artifact_hash)
            if node.artifact_hash and store.exists(node.artifact_hash)
            else None
        )
        output_file = None
        if payload is not None:
            output_file = f"nodes/{position:02d}-{node.name}.json"
            archive.writestr(prefix + output_file, _dump(payload))
            files[node.name] = output_file
        nodes.append(
            {
                "position": position,
                "name": node.name,
                "version": node.version,
                "description": node.description,
                "status": node.status,
                "config": {
                    **node.config,
                    **(node_config.get(node.name) or {}),
                },
                "inputs": [
                    {
                        "key": port.key,
                        "model": port.model,
                        "from_node": port.produced_by,
                        "file": (
                            files.get(port.produced_by)
                            if port.produced_by
                            else seed_files.get(port.key)
                        ),
                    }
                    for port in node.consumes
                ],
                "output": {
                    "key": node.produces.key,
                    "model": node.produces.model,
                    "artifact_hash": node.artifact_hash,
                    "file": output_file,
                },
                "cache_hit": node.cache_hit,
                "model_id": node.model_id,
                "tokens_in": node.tokens_in,
                "tokens_out": node.tokens_out,
                "cost_usd": node.cost_usd,
                "wall_ms": node.wall_ms,
                "started_at": node.started_at,
                "finished_at": node.finished_at,
                "error": node.error,
                "warnings": list((records.get(node.name) or {}).get("warnings") or []),
                "gates": node.findings,
                "_payload": payload,
            }
        )

    script = _script(db, run)
    if script is not None:
        archive.writestr(prefix + "script.md", script["markdown"])

    data = {
        "bundle_version": BUNDLE_VERSION,
        "kind": "run",
        "run": {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "error": run.error,
            "flow_id": run.flow_id,
            "flow_version": run.flow_version,
            "series_id": run.series_id,
            "episode_index": run.episode_index,
            "document": _document(document),
            "settings": _without_personal(run.config_json),
            "format_spec": run.format_spec_json,
            "audience_spec": run.audience_spec_json,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "total_cost_usd": run.total_cost_usd,
            "failed_node": graph.failed_node,
        },
        "seeds": seeds,
        "edges": [edge.model_dump() for edge in graph.edges],
        "nodes": [{k: v for k, v in node.items() if k != "_payload"} for node in nodes],
        "gates": [detail.model_dump(mode="json") for detail in gate_details(db, run)],
        "script": (
            {"file": "script.md", "segments": script["segments"]} if script is not None else None
        ),
    }
    archive.writestr(prefix + "run.json", _dump(data))
    archive.writestr(prefix + "run.md", _run_markdown(data, nodes, graph))


def _script(db: Session, run: Run) -> dict[str, Any] | None:
    """The run's script with the reviewers' edits, as Markdown and as segments."""
    try:
        parsed, script = _load_run_output(db, run)
    except ProblemException:
        # No script yet, or the document is gone: the rest of the run still exports.
        return None
    states = segment_states(db, run.id)
    # The same surviving edits the review page and the script export apply.
    edits = {segment_id: state.text for segment_id, state in states.items() if state.text}
    segments = []
    for segment in script.segments:
        state = states.get(segment.id)
        edited = segment.id in edits
        segments.append(
            {
                "id": segment.id,
                "speaker": segment.speaker,
                "kind": segment.kind,
                "beat_id": segment.beat_id,
                "text": edits.get(segment.id, segment.text),
                "edited": edited,
                "original_text": segment.text if edited else None,
                "accepted": bool(state and state.accepted),
                "flagged": bool(state and state.flagged),
            }
        )
    document = db.get(Document, run.document_id)
    return {
        "markdown": _to_markdown(run, document, parsed, script, edits),
        "segments": segments,
    }


# ----------------------------------------------------------------- markdown


def _run_markdown(data: dict[str, Any], nodes: list[dict[str, Any]], graph: RunGraphOut) -> str:
    run = data["run"]
    document = run["document"] or {}
    lines = [
        f"# Lauf {run['name'] or document.get('title') or run['id']}",
        "",
        f"- Run: `{run['id']}`",
        f"- Flow: `{run['flow_id']}` v{run['flow_version']}",
        f"- Status: {run['status']}",
        f"- Dokument: {document.get('title') or '–'} (`{document.get('id') or '–'}`)",
        f"- Kosten: ${run['total_cost_usd']:.4f}",
        f"- Gestartet: {run['started_at'] or '–'} · Beendet: {run['finished_at'] or '–'}",
    ]
    if run["episode_index"]:
        lines.append(f"- Folge {run['episode_index']} der Serie `{run['series_id']}`")
    if data["script"]:
        lines.append("- Fertiges Skript: `script.md`")
    if run["error"]:
        lines += ["", "## Fehler des Laufs", "", _fence(run["error"], "text")]
    lines += ["", "## Einstellungen", "", _fence(_dump(run["settings"]), "json")]
    if data["seeds"]:
        lines += ["", "## Startwerte", ""]
        lines += [f"- `{seed['key']}` → `{seed['file']}`" for seed in data["seeds"]]
    lines += ["", "## Ablauf", ""]
    lines += [
        f"{node['position']}. `{node['name']}` – {node['status']}"
        + (
            f" ← {', '.join(sorted({p['from_node'] or 'seed' for p in node['inputs']}))}"
            if node["inputs"]
            else ""
        )
        for node in nodes
    ]
    for node, shown in zip(nodes, graph.nodes, strict=True):
        lines += ["", *_node_markdown(node, shown)]
    gates = [detail for detail in data["gates"] if detail.get("report")]
    if gates:
        lines += ["", "## Gates", ""]
        for detail in gates:
            report = detail["report"]
            lines.append(
                f"- **{detail['spec']['id']} {detail['spec']['name']}**: {report['status']}"
                + (f" ({report['skip_reason']})" if report.get("skip_reason") else "")
            )
            lines += [f"  - {v['message']}" for v in report.get("violations") or []]
    return "\n".join(lines) + "\n"


def _node_markdown(node: dict[str, Any], shown: RunGraphNodeOut) -> list[str]:
    lines = [f"## {node['position']}. {node['name']} (v{node['version']})", ""]
    if node["description"]:
        lines += [node["description"], ""]
    duration = f"{node['wall_ms']} ms" if node["wall_ms"] is not None else "–"
    lines += [
        f"- Status: {node['status']}" + (" (aus dem Cache)" if node["cache_hit"] else ""),
        f"- Modell: {node['model_id'] or '–'}",
        f"- Tokens: {node['tokens_in']} ein / {node['tokens_out']} aus",
        f"- Kosten: ${node['cost_usd']:.4f}",
        f"- Dauer: {duration}",
    ]
    if node["config"]:
        lines += ["", "### Einstellungen", "", _fence(_dump(node["config"]), "json")]
    if node["inputs"]:
        lines += ["", "### Eingaben", ""]
        for port in node["inputs"]:
            source = f"von `{port['from_node']}`" if port["from_node"] else "Startwert"
            where = f" → `{port['file']}`" if port["file"] else " (nicht vorhanden)"
            lines.append(f"- `{port['key']}` ({port['model']}), {source}{where}")
    output = node["output"]
    lines += ["", "### Ausgabe", ""]
    if node["_payload"] is None:
        lines.append(f"`{output['key']}` ({output['model']}): keine Ausgabe.")
    else:
        text = _dump(node["_payload"])
        lines.append(f"`{output['key']}` ({output['model']}) → `{output['file']}`")
        if len(text) <= _INLINE_CHARS:
            lines += ["", _fence(text, "json")]
        else:
            lines += [
                "",
                f"Zu lang zum Einbetten ({len(text):,} Zeichen); vollständig in der Datei. "
                "Überblick:",
                "",
                _fence(_dump(shown.output_summary), "json"),
            ]
    if node["warnings"]:
        lines += ["", "### Warnungen", ""]
        lines += [f"- {warning}" for warning in node["warnings"]]
    if node["gates"]:
        lines += ["", "### Gate-Befunde", ""]
        lines += [
            f"- {finding['gate']} {finding['name']}: {finding['status']}, "
            f"{finding['violations']} Verstöße"
            for finding in node["gates"]
        ]
    if node["error"]:
        lines += ["", "### Fehler", "", _fence(node["error"], "text")]
    return lines


def _series_markdown(data: dict[str, Any]) -> str:
    series = data["series"]
    plan = data["plan"]
    title = series["name"] or (plan["title"] if plan else None) or series["id"]
    lines = [
        f"# Serie {title}",
        "",
        f"- Serie: `{series['id']}`",
        f"- Status: {series['status']}",
        f"- Flow der Folgen: `{series['flow_id']}` v{series['flow_version']}; "
        f"Planung: `{series['plan_flow_id']}`",
        f"- Dokument: {(series['document'] or {}).get('title') or '–'}",
        f"- Kosten gesamt: ${series['total_cost_usd']:.4f}",
    ]
    if data["plan_run"]:
        lines.append(f"- Planungslauf: `{data['plan_run']['folder']}`")
    if series["error"]:
        lines += ["", "## Fehler", "", _fence(series["error"], "text")]
    lines += ["", "## Anfrage", "", _fence(_dump(series["request"]), "json")]
    if plan is None:
        lines += ["", "Es gibt noch keinen Serienplan."]
        return "\n".join(lines) + "\n"

    lines += ["", f"## Plan: {plan['title']}", ""]
    if plan.get("through_line"):
        lines += [plan["through_line"], ""]
    if plan.get("terms"):
        lines += ["### Begriffe", ""]
        lines += [
            f"- **{term['term']}** (ab Folge {term['first_episode']})"
            + (f": {term['gloss']}" if term.get("gloss") else "")
            for term in plan["terms"]
        ]
        lines.append("")
    if plan.get("warnings"):
        lines += ["### Warnungen", ""]
        lines += [f"- {warning}" for warning in plan["warnings"]]
        lines.append("")
    status = {episode["index"]: episode for episode in data["episodes"]}
    for episode in plan["episodes"]:
        run = status.get(episode["index"]) or {}
        lines += [f"### Folge {episode['index']}: {episode['title']}", ""]
        lines.append(
            f"- Status: {run.get('status', 'pending')} · Kosten: "
            f"${run.get('total_cost_usd', 0.0):.4f}"
            + (f" · Daten: `{run['folder']}`" if run.get("folder") else "")
        )
        if episode.get("role"):
            lines.append(f"- Rolle: {episode['role']}")
        lines.append(
            f"- Länge: {episode['target_minutes']} min (Stoff für "
            f"{episode['supportable_minutes']} min)"
        )
        for label, key in (
            ("Zusammenfassung", "summary"),
            ("Rückblick", "recap"),
            ("Ausblick", "preview"),
        ):
            if episode.get(key):
                lines.append(f"- {label}: {episode[key]}")
        if episode.get("goals"):
            lines.append("- Lernziele:")
            lines += [f"  - {_goal(goal)}" for goal in episode["goals"]]
        if episode.get("objective_refs"):
            lines.append("- Ziele aus dem Dokument:")
            lines += [f"  - {ref}" for ref in episode["objective_refs"]]
        lines.append(f"- Passagen: {', '.join(episode.get('block_ids') or []) or '–'}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ helpers


def _goal(goal: dict[str, Any]) -> str:
    return str(goal.get("text") or _dump(goal))


def _document(document: Document | None) -> dict[str, Any] | None:
    if document is None:
        return None
    return {
        "id": document.id,
        "title": document.title or document.filename,
        "filename": document.filename,
        "language": document.language,
    }


def _without_personal(values: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (values or {}).items() if k not in _PERSONAL_KEYS}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _fence(text: str, language: str) -> str:
    # A fence longer than any backtick run inside keeps the block closed.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _slug(text: str) -> str:
    lowered = text.lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(umlaut, plain)
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")[:48]


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _zip(buffer: io.BytesIO, filename: str) -> Response:
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
