"""The catalogue of pipelines and format specifications (§6.5, §7.5, §11).

Flows ship as YAML and formats ship as Python constants; both can be edited in
the console, and every edit is a new revision that is never overwritten. This
module is the single place that resolves "what does flow X look like right now",
so the worker, the API and the CLI cannot disagree about it.

Resolution order, for both kinds:

1. what the build shipped — the YAML files, the built-in formats;
2. overlaid with what the database says, when a row has been edited;
3. minus anything archived.

A build that nobody has edited therefore behaves exactly as it did before this
module existed, and a flow file dropped into ``pipeline/flows`` at runtime is
still picked up without a restart.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Flow as FlowRow
from app.models import FlowVersion, FormatSpecRow, FormatVersion
from app.pipeline.formats import FORMATS
from app.pipeline.framework.registry import Flow, discover_flows, flow_directory
from app.schemas.pipeline import FormatSpec

logger = logging.getLogger(__name__)

#: Note recorded on the revision a shipped definition is seeded at.
SHIPPED_NOTE = "Mit dem Build ausgeliefert"

_ID = re.compile(r"^[a-z0-9][a-z0-9_]{1,63}$")


class CatalogueError(ValueError):
    """A request the catalogue refuses: unknown id, duplicate id, bad id."""


def check_id(value: str) -> str:
    candidate = value.strip().lower()
    if not _ID.match(candidate):
        raise CatalogueError(
            f"'{value}' is not a usable id. Use 2–64 characters: lowercase letters, digits "
            f"and underscores, starting with a letter or digit."
        )
    return candidate


# ------------------------------------------------------------------- flows


def flow_spec(flow: Flow) -> dict[str, Any]:
    """A flow's editable definition, without the identity around it."""
    return {
        "description": flow.description,
        "nodes": [{"node": n.node, "config": dict(n.config)} for n in flow.nodes],
        "gates": list(flow.gates),
    }


def flow_from_row(row: FlowRow) -> Flow:
    spec = dict(row.spec_json or {})
    return Flow(
        id=row.id,
        version=row.version,
        description=spec.get("description") or row.description,
        nodes=spec.get("nodes") or [],
        gates=spec.get("gates") or [],
        path=row.path or None,
    )


def flows(session: Session) -> dict[str, Flow]:
    """Every runnable flow, keyed by id."""
    out = discover_flows(flow_directory())
    for row in session.scalars(select(FlowRow)).all():
        if row.archived:
            out.pop(row.id, None)
            continue
        if row.spec_json:
            out[row.id] = flow_from_row(row)
    return out


def get_flow(session: Session, flow_id: str) -> Flow:
    flow = flows(session).get(flow_id)
    if flow is None:
        known = ", ".join(sorted(flows(session))) or "none"
        raise CatalogueError(f"'{flow_id}' is not an available pipeline. Known: {known}.")
    return flow


def flow_row(session: Session, flow_id: str) -> FlowRow | None:
    return session.get(FlowRow, flow_id)


def flow_history(session: Session, flow_id: str) -> list[FlowVersion]:
    return list(
        session.scalars(
            select(FlowVersion)
            .where(FlowVersion.flow_id == flow_id)
            .order_by(FlowVersion.revision.desc())
        ).all()
    )


def flow_revision(session: Session, flow_id: str, revision: int) -> FlowVersion:
    row = session.scalars(
        select(FlowVersion).where(FlowVersion.flow_id == flow_id, FlowVersion.revision == revision)
    ).first()
    if row is None:
        raise CatalogueError(f"Pipeline '{flow_id}' has no revision {revision}.")
    return row


def create_flow(
    session: Session,
    *,
    flow_id: str,
    name: str,
    version: str,
    spec: dict[str, Any],
    user_id: str | None,
    note: str | None = None,
) -> FlowRow:
    flow_id = check_id(flow_id)
    if session.get(FlowRow, flow_id) is not None:
        raise CatalogueError(f"A pipeline with the id '{flow_id}' already exists.")
    if flow_id in discover_flows(flow_directory()):
        raise CatalogueError(f"'{flow_id}' is the id of a pipeline that ships with the build.")

    row = FlowRow(
        id=flow_id,
        version=version,
        path="",
        name=name,
        description=spec.get("description"),
        spec_json=spec,
        revision=0,
        origin="user",
        archived=False,
    )
    session.add(row)
    session.flush()
    _write_flow_revision(session, row, note=note or "Angelegt", user_id=user_id)
    return row


def save_flow(
    session: Session,
    *,
    flow_id: str,
    name: str | None,
    version: str,
    spec: dict[str, Any],
    user_id: str | None,
    note: str | None = None,
    restored_from: int | None = None,
) -> FlowRow:
    """Store a new revision of a flow, seeding the row from disk if needed."""
    row = session.get(FlowRow, flow_id)
    if row is None:
        # A flow that only ever existed on disk. Take its file as revision 1 so
        # the history starts at what shipped rather than at the first edit.
        shipped = discover_flows(flow_directory()).get(flow_id)
        if shipped is None:
            raise CatalogueError(f"'{flow_id}' is not an available pipeline.")
        row = _seed_flow_row(session, shipped)

    if row.archived:
        raise CatalogueError(f"Pipeline '{flow_id}' is archived. Restore it before editing.")

    row.name = name or row.name
    row.version = version
    row.description = spec.get("description")
    row.spec_json = spec
    row.updated_at = datetime.now(UTC)
    row.updated_by = user_id
    _write_flow_revision(session, row, note=note, user_id=user_id, restored_from=restored_from)
    return row


def restore_flow(session: Session, *, flow_id: str, revision: int, user_id: str | None) -> FlowRow:
    """Bring an earlier revision back as a new one. Nothing is rewritten."""
    old = flow_revision(session, flow_id, revision)
    return save_flow(
        session,
        flow_id=flow_id,
        name=None,
        version=old.version,
        spec=dict(old.spec_json or {}),
        user_id=user_id,
        note=f"Wiederhergestellt aus Revision {revision}",
        restored_from=revision,
    )


def archive_flow(session: Session, *, flow_id: str, archived: bool, user_id: str | None) -> FlowRow:
    row = session.get(FlowRow, flow_id)
    if row is None:
        shipped = discover_flows(flow_directory()).get(flow_id)
        if shipped is None:
            raise CatalogueError(f"'{flow_id}' is not an available pipeline.")
        row = _seed_flow_row(session, shipped)
    row.archived = archived
    row.updated_at = datetime.now(UTC)
    row.updated_by = user_id
    return row


def _seed_flow_row(session: Session, flow: Flow) -> FlowRow:
    row = FlowRow(
        id=flow.id,
        version=flow.version,
        path=flow.path or "",
        name=None,
        description=flow.description,
        spec_json=flow_spec(flow),
        revision=0,
        origin="file",
        archived=False,
    )
    session.add(row)
    session.flush()
    _write_flow_revision(session, row, note=SHIPPED_NOTE, user_id=None)
    return row


def _write_flow_revision(
    session: Session,
    row: FlowRow,
    *,
    note: str | None,
    user_id: str | None,
    restored_from: int | None = None,
) -> FlowVersion:
    row.revision = int(row.revision or 0) + 1
    version = FlowVersion(
        flow_id=row.id,
        revision=row.revision,
        version=row.version,
        spec_json=dict(row.spec_json or {}),
        note=note,
        restored_from=restored_from,
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    return version


# ----------------------------------------------------------------- formats


def formats(session: Session) -> dict[str, FormatSpec]:
    out = {key: spec.model_copy(deep=True) for key, spec in FORMATS.items()}
    for row in session.scalars(select(FormatSpecRow)).all():
        if row.archived:
            out.pop(row.id, None)
            continue
        try:
            out[row.id] = FormatSpec.model_validate(row.spec_json)
        except Exception:  # noqa: BLE001 - a broken row must not hide the rest
            logger.exception("format spec %s in the database is not valid", row.id)
    return out


def get_format_spec(session: Session, format_id: str) -> FormatSpec:
    spec = formats(session).get(format_id)
    if spec is None:
        known = ", ".join(sorted(formats(session))) or "none"
        raise CatalogueError(f"'{format_id}' is not an available format. Known: {known}.")
    return spec


def format_row(session: Session, format_id: str) -> FormatSpecRow | None:
    return session.get(FormatSpecRow, format_id)


def format_history(session: Session, format_id: str) -> list[FormatVersion]:
    return list(
        session.scalars(
            select(FormatVersion)
            .where(FormatVersion.format_id == format_id)
            .order_by(FormatVersion.revision.desc())
        ).all()
    )


def format_revision(session: Session, format_id: str, revision: int) -> FormatVersion:
    row = session.scalars(
        select(FormatVersion).where(
            FormatVersion.format_id == format_id, FormatVersion.revision == revision
        )
    ).first()
    if row is None:
        raise CatalogueError(f"Format '{format_id}' has no revision {revision}.")
    return row


def create_format(
    session: Session,
    *,
    spec: FormatSpec,
    user_id: str | None,
    note: str | None = None,
) -> FormatSpecRow:
    format_id = check_id(spec.id)
    if session.get(FormatSpecRow, format_id) is not None or format_id in FORMATS:
        raise CatalogueError(f"A format with the id '{format_id}' already exists.")

    row = FormatSpecRow(
        id=format_id,
        name=spec.name,
        spec_json=spec.model_dump(mode="json"),
        revision=0,
        origin="user",
        archived=False,
    )
    session.add(row)
    session.flush()
    _write_format_revision(session, row, note=note or "Angelegt", user_id=user_id)
    return row


def save_format(
    session: Session,
    *,
    format_id: str,
    spec: FormatSpec,
    user_id: str | None,
    note: str | None = None,
    restored_from: int | None = None,
) -> FormatSpecRow:
    row = session.get(FormatSpecRow, format_id)
    if row is None:
        builtin = FORMATS.get(format_id)
        if builtin is None:
            raise CatalogueError(f"'{format_id}' is not an available format.")
        row = _seed_format_row(session, builtin)

    if row.archived:
        raise CatalogueError(f"Format '{format_id}' is archived. Restore it before editing.")
    if spec.id != format_id:
        raise CatalogueError("A format's id cannot be changed. Create a new format instead.")

    row.name = spec.name
    row.spec_json = spec.model_dump(mode="json")
    row.updated_at = datetime.now(UTC)
    row.updated_by = user_id
    _write_format_revision(session, row, note=note, user_id=user_id, restored_from=restored_from)
    return row


def restore_format(
    session: Session, *, format_id: str, revision: int, user_id: str | None
) -> FormatSpecRow:
    old = format_revision(session, format_id, revision)
    return save_format(
        session,
        format_id=format_id,
        spec=FormatSpec.model_validate(old.spec_json),
        user_id=user_id,
        note=f"Wiederhergestellt aus Revision {revision}",
        restored_from=revision,
    )


def archive_format(
    session: Session, *, format_id: str, archived: bool, user_id: str | None
) -> FormatSpecRow:
    row = session.get(FormatSpecRow, format_id)
    if row is None:
        builtin = FORMATS.get(format_id)
        if builtin is None:
            raise CatalogueError(f"'{format_id}' is not an available format.")
        row = _seed_format_row(session, builtin)
    row.archived = archived
    row.updated_at = datetime.now(UTC)
    row.updated_by = user_id
    return row


def _seed_format_row(session: Session, spec: FormatSpec) -> FormatSpecRow:
    row = FormatSpecRow(
        id=spec.id,
        name=spec.name,
        spec_json=spec.model_dump(mode="json"),
        revision=0,
        origin="file",
        archived=False,
    )
    session.add(row)
    session.flush()
    _write_format_revision(session, row, note=SHIPPED_NOTE, user_id=None)
    return row


def _write_format_revision(
    session: Session,
    row: FormatSpecRow,
    *,
    note: str | None,
    user_id: str | None,
    restored_from: int | None = None,
) -> FormatVersion:
    row.revision = int(row.revision or 0) + 1
    version = FormatVersion(
        format_id=row.id,
        revision=row.revision,
        spec_json=dict(row.spec_json or {}),
        note=note,
        restored_from=restored_from,
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    return version


# ------------------------------------------------------------------- sync


def sync_builtins(session: Session) -> list[str]:
    """Reconcile what the build ships with what the database holds.

    A shipped definition nobody has edited tracks the build: if the YAML or the
    built-in format changed, the seeded revision is rewritten in place rather
    than left to shadow it. Once someone has saved an edit, the database wins
    and the change on disk is only logged — silently reverting an author's work
    on the next deploy would be worse than diverging from the file.
    """
    seen: list[str] = []
    for flow in discover_flows(flow_directory()).values():
        seen.append(flow.id)
        row = session.get(FlowRow, flow.id)
        spec = flow_spec(flow)
        if row is None:
            _seed_flow_row(session, flow)
            continue

        row.path = flow.path or row.path
        if row.origin != "file" or row.revision > 1:
            if row.spec_json != spec:
                logger.info(
                    "flow %s has been edited in the console; the file on disk is not applied",
                    flow.id,
                )
            continue
        if row.spec_json == spec and row.version == flow.version:
            continue

        logger.info("flow %s changed on disk; refreshing the shipped revision", flow.id)
        row.version = flow.version
        row.description = flow.description
        row.spec_json = spec
        shipped = session.scalars(
            select(FlowVersion).where(FlowVersion.flow_id == flow.id, FlowVersion.revision == 1)
        ).first()
        if shipped is None:
            _write_flow_revision(session, row, note=SHIPPED_NOTE, user_id=None)
        else:
            shipped.spec_json = spec
            shipped.version = flow.version

    for spec_model in FORMATS.values():
        format_row_ = session.get(FormatSpecRow, spec_model.id)
        payload = spec_model.model_dump(mode="json")
        if format_row_ is None:
            _seed_format_row(session, spec_model)
            continue
        if (
            format_row_.origin != "file"
            or format_row_.revision > 1
            or format_row_.spec_json == payload
        ):
            continue
        logger.info(
            "format %s changed in the build; refreshing the shipped revision", format_row_.id
        )
        format_row_.name = spec_model.name
        format_row_.spec_json = payload
        shipped_format = session.scalars(
            select(FormatVersion).where(
                FormatVersion.format_id == format_row_.id, FormatVersion.revision == 1
            )
        ).first()
        if shipped_format is None:
            _write_format_revision(session, format_row_, note=SHIPPED_NOTE, user_id=None)
        else:
            shipped_format.spec_json = payload

    return sorted(seen)
