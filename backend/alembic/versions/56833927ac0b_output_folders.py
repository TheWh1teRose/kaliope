"""output folders, decision record and search text on collected outputs

Revision ID: 56833927ac0b
Revises: e5a1c7d93b40
Create Date: 2026-10-02 18:00:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "56833927ac0b"
down_revision: str | None = "e5a1c7d93b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "output_folders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["output_folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_output_folders_parent_id"), "output_folders", ["parent_id"], unique=False
    )

    with op.batch_alter_table("experiment_outputs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("folder_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("status", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("decided_by", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("search_text", sa.Text(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_experiment_outputs_folder_id"), ["folder_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_experiment_outputs_folder_id",
            "output_folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_experiment_outputs_decided_by", "users", ["decided_by"], ["id"]
        )

    # Existing outputs stay "Ohne Ordner"; only the search text is filled in.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, item, output_json, text FROM experiment_outputs")
    ).fetchall()
    for row_id, item, output_json, text in rows:
        parsed = json.loads(output_json) if isinstance(output_json, str) else output_json
        bind.execute(
            sa.text("UPDATE experiment_outputs SET search_text = :t WHERE id = :id"),
            {"t": _search_text(parsed, item, text), "id": row_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("experiment_outputs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_experiment_outputs_decided_by", type_="foreignkey")
        batch_op.drop_constraint("fk_experiment_outputs_folder_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_experiment_outputs_folder_id"))
        batch_op.drop_column("search_text")
        batch_op.drop_column("decided_at")
        batch_op.drop_column("decided_by")
        batch_op.drop_column("note")
        batch_op.drop_column("status")
        batch_op.drop_column("folder_id")
    op.drop_index(op.f("ix_output_folders_parent_id"), table_name="output_folders")
    op.drop_table("output_folders")


# A frozen copy of ``app.experiments.search.item_search_text`` as it was when
# this migration was written, so the backfill never changes with app code.
_SKIP = frozenset(
    {
        "block_id",
        "citation_check",
        "citations",
        "cost",
        "error",
        "flags",
        "id",
        "item",
        "kind",
        "quote",
        "raw",
        "source",
        "stop_reason",
        "variant",
        "warnings",
    }
)


def _search_text(output_json: Any, item: str, text: str | None) -> str:
    if not isinstance(output_json, dict) or not output_json:
        return text or ""
    scope = _find_item(output_json, item)
    parts: list[str] = []
    if scope is not None:
        _strings(scope, parts)
    else:
        skip = {"text"} if output_json.get("payload") is not None else set()
        _strings({k: v for k, v in output_json.items() if k not in skip}, parts)
    found = "\n".join(part for part in parts if part.strip())
    return found or (text or "")


def _find_item(node: Any, item: str) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if node.get("item") == item:
            return node
        for value in node.values():
            found = _find_item(value, item)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_item(value, item)
            if found is not None:
                return found
    return None


def _strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key not in _SKIP:
                _strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _strings(value, out)
