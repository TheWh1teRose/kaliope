"""Folder endpoints (§11).

Folders are filing, not policy: they carry no permissions, no inheritance and
no effect on parsing or runs. A document's folder is a single nullable column,
so ``None`` is the root and no migration of existing documents is needed.

Deleting a folder never deletes a document. The subtree's documents move up to
the deleted folder's parent, which is the only outcome that cannot lose work.
The rules live in ``app.folder_tree``, shared with the Sammlung's folders.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.folder_tree import MAX_DEPTH, ROOT, FolderTree, TreeRow
from app.models import Document, Folder, User
from app.schemas.api import FolderCreate, FolderMove, FolderOut, FolderRename
from app.security import current_user

router = APIRouter(prefix="/api/folders", tags=["folders"])

__all__ = ["MAX_DEPTH", "ROOT", "resolve_folder", "router"]

tree = FolderTree(Folder, Document, Document.folder_id, "documents")


@router.get("", response_model=list[FolderOut])
def list_folders(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[FolderOut]:
    """Every folder, depth-first in display order, each with its counts."""
    return [_out(row) for row in tree.rows(db)]


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    payload: FolderCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> FolderOut:
    folder_id = tree.create(db, payload.name, payload.parent_id, user.id)
    return _out(tree.row(db, folder_id))


@router.patch("/{folder_id}", response_model=FolderOut)
def rename_folder(
    folder_id: str,
    payload: FolderRename,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FolderOut:
    tree.rename(db, folder_id, payload.name)
    return _out(tree.row(db, folder_id))


@router.post("/{folder_id}/move", response_model=FolderOut)
def move_folder(
    folder_id: str,
    payload: FolderMove,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> FolderOut:
    tree.move(db, folder_id, payload.parent_id)
    return _out(tree.row(db, folder_id))


@router.delete("/{folder_id}", status_code=200)
def delete_folder(
    folder_id: str,
    cascade: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> dict[str, object]:
    result = tree.delete(db, folder_id, cascade)
    return {
        "deleted_folders": result.deleted_folders,
        "moved_documents": result.moved_items,
        "moved_to": result.moved_to,
    }


def resolve_folder(db: Session, folder_id: str | None) -> str | None:
    """Validate a folder id used elsewhere; ``None`` and ``root`` mean the root."""
    return tree.resolve(db, folder_id)


def _out(row: TreeRow) -> FolderOut:
    return FolderOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        depth=row.depth,
        path=row.path,
        document_count=row.count,
        total_document_count=row.total,
        created_at=row.created_at,
    )
