"""Folder trees: the rules Dokumente and the Sammlung share (§11).

Folders are filing, not policy: they carry no permissions, no inheritance and
no effect on what they hold. An item's folder is a single nullable column, so
``None`` is the root. Deleting a folder never deletes an item: the subtree's
items move up to the deleted folder's parent, the only outcome that cannot
lose work.

Each tree has its own table (``folders``, ``output_folders``) and its own item
column (``documents.folder_id``, ``experiment_outputs.folder_id``); one
``FolderTree`` per pair holds the rules, so both trees behave the same.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.errors import problem

#: The literal that means "items in no folder" wherever a folder id is accepted.
ROOT = "root"

MAX_DEPTH = 8


@dataclass
class TreeRow:
    """One folder in display order, with what the tree view needs."""

    id: str
    name: str
    parent_id: str | None
    #: 0 at the root.
    depth: int
    #: Names from the root down to and including this folder.
    path: list[str]
    #: Items directly in this folder.
    count: int
    #: Including every subfolder.
    total: int
    created_at: str


@dataclass
class DeleteResult:
    deleted_folders: int
    moved_items: int
    moved_to: str | None


class FolderTree:
    def __init__(
        self,
        folder_model: type[Any],
        item_model: type[Any],
        item_folder: InstrumentedAttribute[str | None],
        item_noun: str,
    ) -> None:
        self.folder_model = folder_model
        self.item_model = item_model
        self.item_folder = item_folder
        #: Plural, for messages: "documents", "outputs".
        self.item_noun = item_noun

    # ------------------------------------------------------------ lookups

    def require(self, db: Session, folder_id: str) -> Any:
        folder = db.get(self.folder_model, folder_id)
        if folder is None:
            raise problem(404, "No such folder", f"Folder '{folder_id}' does not exist.")
        return folder

    def resolve(self, db: Session, folder_id: str | None) -> str | None:
        """Validate a folder id used elsewhere; ``None``, ``""`` and ``root`` mean the root."""
        if folder_id in (None, "", ROOT):
            return None
        self.require(db, str(folder_id))
        return str(folder_id)

    def descendants(self, db: Session, folder_id: str) -> list[str]:
        _, children = self._children_map(db)
        out: list[str] = []
        stack = [folder_id]
        while stack:
            for child in children.get(stack.pop(), []):
                out.append(child.id)
                stack.append(child.id)
        return out

    def rows(self, db: Session) -> list[TreeRow]:
        """Every folder, depth-first in display order, each with its counts."""
        by_id, children = self._children_map(db)
        direct: dict[str | None, int] = {
            folder_id: count
            for folder_id, count in db.execute(
                select(self.item_folder, func.count()).group_by(self.item_folder)
            ).all()
        }

        out: list[TreeRow] = []

        def walk(parent: str | None, prefix: list[str], depth: int) -> int:
            """Emit the subtree in display order and return its recursive count."""
            total = 0
            for folder in children.get(parent, []):
                path = [*prefix, folder.name]
                index = len(out)
                count = int(direct.get(folder.id, 0))
                out.append(
                    TreeRow(
                        id=folder.id,
                        name=folder.name,
                        parent_id=folder.parent_id,
                        depth=depth,
                        path=path,
                        count=count,
                        total=0,
                        created_at=_iso(folder.created_at),
                    )
                )
                subtotal = count + walk(folder.id, path, depth + 1)
                out[index].total = subtotal
                total += subtotal
            return total

        walk(None, [], 0)
        # Any folder the walk missed would mean an orphaned parent reference.
        missing = set(by_id) - {row.id for row in out}
        for folder_id in sorted(missing):
            orphan = by_id[folder_id]
            count = int(direct.get(orphan.id, 0))
            out.append(
                TreeRow(
                    id=orphan.id,
                    name=orphan.name,
                    parent_id=None,
                    depth=0,
                    path=[orphan.name],
                    count=count,
                    total=count,
                    created_at=_iso(orphan.created_at),
                )
            )
        return out

    def row(self, db: Session, folder_id: str) -> TreeRow:
        found = next((r for r in self.rows(db) if r.id == folder_id), None)
        if found is None:  # pragma: no cover - written in the same transaction
            raise problem(404, "No such folder", f"Folder '{folder_id}' does not exist.")
        return found

    def paths(self, db: Session) -> dict[str, list[str]]:
        """Folder id to its path, for labelling items without a second walk."""
        by_id, _ = self._children_map(db)
        out: dict[str, list[str]] = {}
        for folder_id in by_id:
            trail: list[str] = []
            current = by_id.get(folder_id)
            while current is not None and len(trail) <= MAX_DEPTH * 2:
                trail.insert(0, current.name)
                current = by_id.get(current.parent_id) if current.parent_id else None
            out[folder_id] = trail
        return out

    # ---------------------------------------------------------- mutations

    def create(self, db: Session, name: str, parent_id: str | None, user_id: str) -> str:
        clean = clean_name(name)
        parent = None if parent_id is None else self.require(db, parent_id)
        if parent is not None and self._depth(db, parent.id) + 1 >= MAX_DEPTH:
            raise problem(
                422,
                "Too deep",
                f"Folders nest at most {MAX_DEPTH} levels; this one would be deeper.",
            )
        self._reject_duplicate(db, parent_id, clean, exclude_id=None)
        folder = self.folder_model(name=clean, parent_id=parent_id, created_by=user_id)
        db.add(folder)
        db.commit()
        return str(folder.id)

    def rename(self, db: Session, folder_id: str, name: str) -> None:
        folder = self.require(db, folder_id)
        clean = clean_name(name)
        self._reject_duplicate(db, folder.parent_id, clean, exclude_id=folder.id)
        folder.name = clean
        db.commit()

    def move(self, db: Session, folder_id: str, parent_id: str | None) -> None:
        folder = self.require(db, folder_id)
        target = None if parent_id is None else self.require(db, parent_id)
        if target is not None:
            if target.id == folder.id or target.id in self.descendants(db, folder.id):
                raise problem(
                    422,
                    "Cannot move a folder into itself",
                    "The target folder is this folder or one of its own subfolders.",
                )
            if self._depth(db, target.id) + 1 + self._subtree_height(db, folder.id) > MAX_DEPTH:
                raise problem(
                    422,
                    "Too deep",
                    f"The move would nest folders more than {MAX_DEPTH} levels deep.",
                )
        self._reject_duplicate(db, parent_id, folder.name, exclude_id=folder.id)
        folder.parent_id = parent_id
        db.commit()

    def delete(self, db: Session, folder_id: str, cascade: bool) -> DeleteResult:
        folder = self.require(db, folder_id)
        subtree = [folder.id, *self.descendants(db, folder.id)]
        items: list[Any] = list(
            db.scalars(select(self.item_model).where(self.item_folder.in_(subtree))).all()
        )
        children = [f for f in self._all(db) if f.parent_id == folder.id]

        if (items or children) and not cascade:
            raise problem(
                409,
                "Folder is not empty",
                f"It holds {len(items)} {self.item_noun[:-1]}(s) and {len(children)} "
                "subfolder(s). Repeat with cascade=true to delete the subfolder(s); the "
                f"{self.item_noun} move up to the parent folder and are never deleted.",
            )

        for item in items:
            item.folder_id = folder.parent_id
        # Flush the re-filing before the folders go: the FK is ``ON DELETE SET
        # NULL``, so a delete that ran first would strand these items at the
        # root instead of in the parent folder.
        db.flush()

        # One statement rather than a row at a time. ``ON DELETE CASCADE`` is
        # enforced here, so a per-row delete would have the database removing
        # subfolders underneath the ORM while it still expects to delete them.
        db.execute(sa_delete(self.folder_model).where(self.folder_model.id.in_(subtree)))
        db.commit()
        return DeleteResult(
            deleted_folders=len(subtree), moved_items=len(items), moved_to=folder.parent_id
        )

    # ------------------------------------------------------------ helpers

    def _reject_duplicate(
        self, db: Session, parent_id: str | None, name: str, exclude_id: str | None
    ) -> None:
        siblings = [f for f in self._all(db) if f.parent_id == parent_id and f.id != exclude_id]
        if any(f.name.casefold() == name.casefold() for f in siblings):
            raise problem(
                409,
                "Name already used",
                f"A folder called '{name}' already exists in the same place.",
            )

    def _children_map(self, db: Session) -> tuple[dict[str, Any], dict[str | None, list[Any]]]:
        rows = self._all(db)
        by_id = {row.id: row for row in rows}
        children: dict[str | None, list[Any]] = defaultdict(list)
        for row in rows:
            children[row.parent_id].append(row)
        for bucket in children.values():
            bucket.sort(key=lambda f: f.name.casefold())
        return by_id, children

    def _all(self, db: Session) -> list[Any]:
        return list(db.scalars(select(self.folder_model)).all())

    def _depth(self, db: Session, folder_id: str) -> int:
        by_id, _ = self._children_map(db)
        depth = 0
        current = by_id.get(folder_id)
        while current is not None and current.parent_id is not None:
            depth += 1
            current = by_id.get(current.parent_id)
            if depth > MAX_DEPTH * 2:  # pragma: no cover - a cycle should be unreachable
                break
        return depth

    def _subtree_height(self, db: Session, folder_id: str) -> int:
        _, children = self._children_map(db)

        def height(node: str) -> int:
            kids = children.get(node, [])
            return 0 if not kids else 1 + max(height(k.id) for k in kids)

        return height(folder_id)


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise problem(422, "Name required", "A folder needs a name.")
    if len(name) > 200:
        raise problem(422, "Name too long", "A folder name is at most 200 characters.")
    return name


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""
