"""Folders of the Sammlung: the collected outputs of every experiment.

Same rules and contract as the documents' ``/api/folders`` (``app.folder_tree``),
in a tree of their own. Deleting a folder moves its outputs up to the parent;
it never deletes an output.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.folder_tree import FolderTree, TreeRow
from app.models import ExperimentOutput, OutputFolder, User
from app.schemas.api import FolderCreate, FolderMove, FolderRename
from app.schemas.experiments import OutputFolderOut
from app.security import current_user

router = APIRouter(prefix="/api/experiment-folders", tags=["experiments"])

tree = FolderTree(OutputFolder, ExperimentOutput, ExperimentOutput.folder_id, "outputs")


@router.get("", response_model=list[OutputFolderOut])
def list_folders(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[OutputFolderOut]:
    return [_out(row) for row in tree.rows(db)]


@router.post("", response_model=OutputFolderOut, status_code=201)
def create_folder(
    payload: FolderCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> OutputFolderOut:
    folder_id = tree.create(db, payload.name, payload.parent_id, user.id)
    return _out(tree.row(db, folder_id))


@router.patch("/{folder_id}", response_model=OutputFolderOut)
def rename_folder(
    folder_id: str,
    payload: FolderRename,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> OutputFolderOut:
    tree.rename(db, folder_id, payload.name)
    return _out(tree.row(db, folder_id))


@router.post("/{folder_id}/move", response_model=OutputFolderOut)
def move_folder(
    folder_id: str,
    payload: FolderMove,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> OutputFolderOut:
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
        "moved_outputs": result.moved_items,
        "moved_to": result.moved_to,
    }


def _out(row: TreeRow) -> OutputFolderOut:
    return OutputFolderOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        depth=row.depth,
        path=row.path,
        output_count=row.count,
        total_output_count=row.total,
        created_at=row.created_at,
    )
