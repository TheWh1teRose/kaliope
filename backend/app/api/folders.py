"""Folder endpoints (§11).

Folders are filing, not policy: they carry no permissions, no inheritance and
no effect on parsing or runs. A document's folder is a single nullable column,
so ``None`` is the root and no migration of existing documents is needed.

Deleting a folder never deletes a document. The subtree's documents move up to
the deleted folder's parent, which is the only outcome that cannot lose work.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import problem
from app.models import Document, Folder, User
from app.schemas.api import FolderCreate, FolderMove, FolderOut, FolderRename
from app.security import current_user

router = APIRouter(prefix="/api/folders", tags=["folders"])

#: ``folder_id=root`` on the document list means "documents in no folder".
ROOT = "root"

MAX_DEPTH = 8


@router.get("", response_model=list[FolderOut])
def list_folders(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[FolderOut]:
    """Every folder, depth-first in display order, each with its counts."""
    return _tree(db)


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    payload: FolderCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> FolderOut:
    name = _clean_name(payload.name)
    parent = _require_parent(db, payload.parent_id)
    if parent is not None and _depth(db, parent.id) + 1 >= MAX_DEPTH:
        raise problem(
            422,
            "Too deep",
            f"Folders nest at most {MAX_DEPTH} levels; this one would be deeper.",
        )
    _reject_duplicate(db, payload.parent_id, name, exclude_id=None)

    folder = Folder(name=name, parent_id=payload.parent_id, created_by=user.id)
    db.add(folder)
    db.commit()
    return _one(db, folder.id)


@router.patch("/{folder_id}", response_model=FolderOut)
def rename_folder(
    folder_id: str,
    payload: FolderRename,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FolderOut:
    folder = _require(db, folder_id)
    name = _clean_name(payload.name)
    _reject_duplicate(db, folder.parent_id, name, exclude_id=folder.id)
    folder.name = name
    db.commit()
    return _one(db, folder.id)


@router.post("/{folder_id}/move", response_model=FolderOut)
def move_folder(
    folder_id: str,
    payload: FolderMove,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FolderOut:
    folder = _require(db, folder_id)
    target = _require_parent(db, payload.parent_id)

    if target is not None:
        if target.id == folder.id or target.id in _descendants(db, folder.id):
            raise problem(
                422,
                "Cannot move a folder into itself",
                "The target folder is this folder or one of its own subfolders.",
            )
        if _depth(db, target.id) + 1 + _subtree_height(db, folder.id) > MAX_DEPTH:
            raise problem(
                422,
                "Too deep",
                f"The move would nest folders more than {MAX_DEPTH} levels deep.",
            )

    _reject_duplicate(db, payload.parent_id, folder.name, exclude_id=folder.id)
    folder.parent_id = payload.parent_id
    db.commit()
    return _one(db, folder.id)


@router.delete("/{folder_id}", status_code=200)
def delete_folder(
    folder_id: str,
    cascade: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> dict[str, object]:
    folder = _require(db, folder_id)
    subtree = [folder.id, *_descendants(db, folder.id)]
    documents = db.scalars(select(Document).where(Document.folder_id.in_(subtree))).all()
    children = [f for f in db.scalars(select(Folder)).all() if f.parent_id == folder.id]

    if (documents or children) and not cascade:
        raise problem(
            409,
            "Folder is not empty",
            f"It holds {len(documents)} document(s) and {len(children)} subfolder(s). "
            "Repeat with cascade=true to delete the subfolder(s); the documents move "
            "up to the parent folder and are never deleted.",
        )

    for document in documents:
        document.folder_id = folder.parent_id
    # Flush the re-filing before the folders go: the FK is ``ON DELETE SET
    # NULL``, so a delete that ran first would strand these documents at the
    # root instead of in the parent folder.
    db.flush()

    # One statement rather than a row at a time. ``ON DELETE CASCADE`` is
    # enforced here, so a per-row delete would have the database removing
    # subfolders underneath the ORM while it still expects to delete them.
    db.execute(sa_delete(Folder).where(Folder.id.in_(subtree)))
    db.commit()
    return {
        "deleted_folders": len(subtree),
        "moved_documents": len(documents),
        "moved_to": folder.parent_id,
    }


# ---------------------------------------------------------------- helpers


def resolve_folder(db: Session, folder_id: str | None) -> str | None:
    """Validate a folder id used elsewhere; ``None`` and ``root`` mean the root."""
    if folder_id in (None, "", ROOT):
        return None
    _require(db, str(folder_id))
    return str(folder_id)


def _require(db: Session, folder_id: str) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise problem(404, "No such folder", f"Folder '{folder_id}' does not exist.")
    return folder


def _require_parent(db: Session, parent_id: str | None) -> Folder | None:
    return None if parent_id is None else _require(db, parent_id)


def _clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise problem(422, "Name required", "A folder needs a name.")
    if len(name) > 200:
        raise problem(422, "Name too long", "A folder name is at most 200 characters.")
    return name


def _reject_duplicate(
    db: Session, parent_id: str | None, name: str, exclude_id: str | None
) -> None:
    siblings = [
        f
        for f in db.scalars(select(Folder)).all()
        if f.parent_id == parent_id and f.id != exclude_id
    ]
    if any(f.name.casefold() == name.casefold() for f in siblings):
        raise problem(
            409,
            "Name already used",
            f"A folder called '{name}' already exists in the same place.",
        )


def _children_map(db: Session) -> tuple[dict[str, Folder], dict[str | None, list[Folder]]]:
    rows = db.scalars(select(Folder)).all()
    by_id = {row.id: row for row in rows}
    children: dict[str | None, list[Folder]] = defaultdict(list)
    for row in rows:
        children[row.parent_id].append(row)
    for bucket in children.values():
        bucket.sort(key=lambda f: f.name.casefold())
    return by_id, children


def _descendants(db: Session, folder_id: str) -> list[str]:
    _, children = _children_map(db)
    out: list[str] = []
    stack = [folder_id]
    while stack:
        for child in children.get(stack.pop(), []):
            out.append(child.id)
            stack.append(child.id)
    return out


def _depth(db: Session, folder_id: str) -> int:
    by_id, _ = _children_map(db)
    depth = 0
    current = by_id.get(folder_id)
    while current is not None and current.parent_id is not None:
        depth += 1
        current = by_id.get(current.parent_id)
        if depth > MAX_DEPTH * 2:  # pragma: no cover - a cycle should be unreachable
            break
    return depth


def _subtree_height(db: Session, folder_id: str) -> int:
    _, children = _children_map(db)

    def height(node: str) -> int:
        kids = children.get(node, [])
        return 0 if not kids else 1 + max(height(k.id) for k in kids)

    return height(folder_id)


def _tree(db: Session) -> list[FolderOut]:
    by_id, children = _children_map(db)
    direct: dict[str | None, int] = {
        folder_id: count
        for folder_id, count in db.execute(
            select(Document.folder_id, func.count(Document.id)).group_by(Document.folder_id)
        ).all()
    }

    out: list[FolderOut] = []

    def walk(parent: str | None, prefix: list[str], depth: int) -> int:
        """Emit the subtree in display order and return its recursive count."""
        total = 0
        for folder in children.get(parent, []):
            path = [*prefix, folder.name]
            index = len(out)
            out.append(
                FolderOut(
                    id=folder.id,
                    name=folder.name,
                    parent_id=folder.parent_id,
                    depth=depth,
                    path=path,
                    document_count=int(direct.get(folder.id, 0)),
                    total_document_count=0,
                    created_at=_iso(folder.created_at),
                )
            )
            subtotal = int(direct.get(folder.id, 0)) + walk(folder.id, path, depth + 1)
            out[index].total_document_count = subtotal
            total += subtotal
        return total

    walk(None, [], 0)
    # ``by_id`` is only consulted for cycle-free traversal above; any folder it
    # holds that the walk missed would mean an orphaned parent reference.
    missing = set(by_id) - {f.id for f in out}
    for folder_id in sorted(missing):
        orphan = by_id[folder_id]
        out.append(
            FolderOut(
                id=orphan.id,
                name=orphan.name,
                parent_id=None,
                depth=0,
                path=[orphan.name],
                document_count=int(direct.get(orphan.id, 0)),
                total_document_count=int(direct.get(orphan.id, 0)),
                created_at=_iso(orphan.created_at),
            )
        )
    return out


def _one(db: Session, folder_id: str) -> FolderOut:
    found = next((f for f in _tree(db) if f.id == folder_id), None)
    if found is None:  # pragma: no cover - written in the same transaction
        raise problem(404, "No such folder", f"Folder '{folder_id}' does not exist.")
    return found


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""
