"""Command-line interface (§12.2).

    python -m app.cli create-user --email … --role admin
    python -m app.cli run-flow --document … --flow baseline_v0
    python -m app.cli reparse --document …

There is no self-registration; accounts exist so that every edit event carries a
real reviewer identity.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import select

from app.config import get_settings
from app.db import create_all, session_scope
from app.models import Document, EditEvent, Run, User
from app.pipeline import catalogue
from app.pipeline.catalogue import CatalogueError
from app.pipeline.formats import DEFAULT_AUDIENCE
from app.pipeline.framework.registry import bootstrap_nodes, discover_flows
from app.security import hash_password
from app.worker import _flow_directory, worker

app = typer.Typer(help="Kalliope administration.", no_args_is_help=True)


@app.command("init-db")
def init_db() -> None:
    """Create the schema in an empty /data directory."""
    get_settings().ensure_dirs()
    create_all()
    typer.echo(f"schema ready at {get_settings().db_path}")


@app.command("create-user")
def create_user(
    email: str = typer.Option(..., help="Login address."),
    name: str = typer.Option("", help="Display name; defaults to the local part."),
    role: str = typer.Option("reviewer", help="admin or reviewer."),
    password: str | None = typer.Option(None, help="Omit to be prompted."),
) -> None:
    if role not in {"admin", "reviewer"}:
        typer.secho("role must be 'admin' or 'reviewer'", fg=typer.colors.RED)
        raise typer.Exit(2)

    secret = password or getpass.getpass("Password: ")
    if len(secret) < 10:
        typer.secho("password must be at least 10 characters", fg=typer.colors.RED)
        raise typer.Exit(2)

    get_settings().ensure_dirs()
    create_all()
    with session_scope() as session:
        if session.scalars(select(User).where(User.email == email.lower())).first():
            typer.secho(f"user {email} already exists", fg=typer.colors.RED)
            raise typer.Exit(1)
        user = User(
            email=email.lower(),
            name=name or email.split("@")[0],
            password_hash=hash_password(secret),
            role=role,
        )
        session.add(user)
    typer.secho(f"created {role} {email}", fg=typer.colors.GREEN)


@app.command("list-users")
def list_users() -> None:
    with session_scope() as session:
        for user in session.scalars(select(User).order_by(User.created_at)).all():
            state = "active" if user.active else "disabled"
            typer.echo(f"{user.id}  {user.email:<32} {user.role:<9} {state}")


@app.command("import-document")
def import_document(
    path: Path = typer.Argument(..., exists=True, readable=True),
    parse: bool = typer.Option(True, help="Parse immediately."),
) -> None:
    """Register a PDF from disk without going through the HTTP upload."""
    settings = get_settings()
    settings.ensure_dirs()
    create_all()
    bootstrap_nodes()

    payload = path.read_bytes()
    if not payload.startswith(b"%PDF-"):
        typer.secho("not a PDF", fg=typer.colors.RED)
        raise typer.Exit(2)

    digest = hashlib.sha256(payload).hexdigest()
    target = settings.uploads_dir / f"{digest}.pdf"
    if not target.exists():
        target.write_bytes(payload)

    with session_scope() as session:
        existing = session.scalars(select(Document).where(Document.sha256 == digest)).first()
        if existing is not None:
            document_id = existing.id
        else:
            document = Document(filename=path.name, sha256=digest, parse_status="pending")
            session.add(document)
            session.flush()
            document_id = document.id

    typer.echo(document_id)
    if parse:
        worker.parse_document(document_id)
        with session_scope() as session:
            stored = session.get(Document, document_id)
            if stored is None:  # pragma: no cover - deleted between calls
                typer.secho("document vanished during parsing", fg=typer.colors.RED)
                raise typer.Exit(1)
            colour = typer.colors.GREEN if stored.parse_status == "parsed" else typer.colors.RED
            typer.secho(f"parse status: {stored.parse_status}", fg=colour)
            if stored.parse_error:
                typer.echo(stored.parse_error)


@app.command("reparse")
def reparse(document: str = typer.Option(..., "--document", help="Document id.")) -> None:
    bootstrap_nodes()
    worker.parse_document(document)
    with session_scope() as session:
        row = session.get(Document, document)
        if row is None:
            typer.secho("no such document", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.echo(f"{row.id}: {row.parse_status} (parse_version {row.parse_version})")
        if row.parse_error:
            typer.echo(row.parse_error)


@app.command("run-flow")
def run_flow(
    document: str = typer.Option(..., "--document", help="Document id."),
    flow: str = typer.Option("baseline_v0", help="Flow id."),
    format_id: str = typer.Option("two_host_dialogue", "--format", help="Format id."),
    minutes: int = typer.Option(0, help="Target minutes; 0 uses the format default."),
    force: bool = typer.Option(False, help="Ignore the artifact cache."),
) -> None:
    """Execute a flow synchronously and print the result."""
    bootstrap_nodes()
    get_settings().ensure_dirs()
    create_all()

    with session_scope() as session:
        catalogue.sync_builtins(session)
        try:
            definition = catalogue.get_flow(session, flow)
            format_spec = catalogue.get_format_spec(session, format_id)
        except CatalogueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(2) from exc

        if session.get(Document, document) is None:
            typer.secho(f"no such document '{document}'", fg=typer.colors.RED)
            raise typer.Exit(1)
        flow_row = catalogue.flow_row(session, definition.id)
        run = Run(
            document_id=document,
            flow_id=definition.id,
            flow_version=definition.version,
            flow_revision=flow_row.revision if flow_row else 0,
            config_json={
                "target_minutes": minutes or format_spec.target_minutes,
                "force": force,
            },
            format_spec_json=format_spec.model_dump(mode="json"),
            audience_spec_json=DEFAULT_AUDIENCE.model_dump(mode="json"),
            status="queued",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    typer.echo(f"run {run_id} starting")
    worker.execute_run(run_id)

    with session_scope() as session:
        row = session.get(Run, run_id)
        assert row is not None
        colour = typer.colors.GREEN if row.status == "completed" else typer.colors.RED
        typer.secho(f"{row.status}  ${row.total_cost_usd:.4f}", fg=colour)
        if row.error:
            typer.echo(row.error)


@app.command("export-edit-events")
def export_edit_events(
    output: Path = typer.Option(Path("edit-events.jsonl"), "--output", "-o"),
) -> None:
    """Write every edit event as JSONL — the file the evaluation work consumes."""
    written = 0
    with output.open("w", encoding="utf-8") as handle, session_scope() as session:
        for event in session.scalars(select(EditEvent).order_by(EditEvent.created_at)).all():
            record: dict[str, Any] = {
                "id": event.id,
                "run_id": event.run_id,
                "document_id": event.document_id,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "user_id": event.user_id,
                "action": event.action,
                "reason_code": event.reason_code,
                "note": event.note,
                "text_before": event.text_before,
                "text_after": event.text_after,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    typer.echo(f"wrote {written} event(s) to {output}")


@app.command("doctor")
def doctor() -> None:
    """Check that the environment is usable, and say what is missing."""
    settings = get_settings()
    problems: list[str] = []
    try:
        settings.validate_required()
        typer.secho("config: ok", fg=typer.colors.GREEN)
    except Exception as exc:  # noqa: BLE001
        problems.append(str(exc))
        typer.secho(f"config: {exc}", fg=typer.colors.RED)

    settings.ensure_dirs()
    typer.echo(f"data dir: {settings.data_dir} (writable: {settings.data_dir.exists()})")

    if settings.has_any_llm_key():
        typer.secho("llm: at least one provider key is set", fg=typer.colors.GREEN)
    else:
        typer.secho("llm: no provider key set — generation will fail", fg=typer.colors.YELLOW)

    bootstrap_nodes()
    typer.echo(f"flows on disk: {', '.join(sorted(discover_flows(_flow_directory()))) or 'none'}")
    if problems:
        raise typer.Exit(1)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
