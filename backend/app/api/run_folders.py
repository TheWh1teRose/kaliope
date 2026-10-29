"""Folders of the runs overview.

Same rules and contract as the documents' ``/api/folders`` (``app.folder_tree``),
in a tree of their own. A folder holds standalone runs and whole series: a
series is filed as one unit, so its planner and episodes are listed wherever
the series is. Deleting a folder moves what it holds up to the parent; it never
deletes a run. Runs in the trash keep their folder, so restoring one brings it
back where it was, but they are left out of every count.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.folder_tree import ROOT, FolderTree, TreeRow
from app.models import Run, RunFolder, Series, User
from app.schemas.api import (
    FolderCreate,
    FolderMove,
    FolderRename,
    RunFolderOut,
    RunFolderSummary,
)
from app.security import current_user

router = APIRouter(prefix="/api/run-folders", tags=["runs"])

__all__ = ["ROOT", "resolve_run_folder", "router", "runs_in_folder", "series_in_folder"]


class RunFolderTree(FolderTree):
    """A tree whose items are standalone runs and series, a series counting once."""

    def __init__(self) -> None:
        super().__init__(RunFolder, Run, Run.folder_id, "runs")

    def direct_counts(self, db: Session) -> dict[str | None, int]:
        counts: dict[str | None, int] = {}
        for statement in (
            select(Run.folder_id, func.count())
            .where(Run.series_id.is_(None), Run.trashed_at.is_(None))
            .group_by(Run.folder_id),
            select(Series.folder_id, func.count())
            .where(Series.trashed_at.is_(None))
            .group_by(Series.folder_id),
        ):
            for folder_id, count in db.execute(statement).all():
                counts[folder_id] = counts.get(folder_id, 0) + int(count)
        return counts

    def items_in(self, db: Session, folder_ids: list[str]) -> list[Any]:
        return [
            *db.scalars(select(Run).where(Run.folder_id.in_(folder_ids))).all(),
            *db.scalars(select(Series).where(Series.folder_id.in_(folder_ids))).all(),
        ]

    def count_items(self, items: list[Any]) -> int:
        return sum(1 for item in items if item.trashed_at is None)


tree = RunFolderTree()


@router.get("", response_model=list[RunFolderOut])
def list_folders(
    db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> list[RunFolderOut]:
    """Every folder, depth-first in display order, each with its counts."""
    return [_out(row) for row in tree.rows(db)]


@router.get("/summary", response_model=RunFolderSummary)
def summary(db: Session = Depends(get_db), _user: User = Depends(current_user)) -> RunFolderSummary:
    """How many runs and series are listed at all, and how many of them in no folder."""
    counts = tree.direct_counts(db)
    return RunFolderSummary(total=sum(counts.values()), root=counts.get(None, 0))


@router.post("", response_model=RunFolderOut, status_code=201)
def create_folder(
    payload: FolderCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> RunFolderOut:
    folder_id = tree.create(db, payload.name, payload.parent_id, user.id)
    return _out(tree.row(db, folder_id))


@router.patch("/{folder_id}", response_model=RunFolderOut)
def rename_folder(
    folder_id: str,
    payload: FolderRename,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> RunFolderOut:
    tree.rename(db, folder_id, payload.name)
    return _out(tree.row(db, folder_id))


@router.post("/{folder_id}/move", response_model=RunFolderOut)
def move_folder(
    folder_id: str,
    payload: FolderMove,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> RunFolderOut:
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
        "moved_runs": result.moved_items,
        "moved_to": result.moved_to,
    }


def resolve_run_folder(db: Session, folder_id: str | None) -> str | None:
    """Validate a run folder id; ``None``, ``""`` and ``root`` mean no folder."""
    return tree.resolve(db, folder_id)


def runs_in_folder(db: Session, folder_id: str) -> ColumnElement[bool]:
    """The runs listed in ``folder_id`` (``root``: in none): standalone ones by their
    own folder, the runs of a series by the series' folder."""
    target = resolve_run_folder(db, folder_id)
    own = Run.folder_id.is_(None) if target is None else Run.folder_id == target
    series = select(Series.id).where(
        Series.folder_id.is_(None) if target is None else Series.folder_id == target
    )
    return (Run.series_id.is_(None) & own) | Run.series_id.in_(series)


def series_in_folder(db: Session, folder_id: str) -> ColumnElement[bool]:
    """The series filed in ``folder_id`` (``root``: in none)."""
    target = resolve_run_folder(db, folder_id)
    return Series.folder_id.is_(None) if target is None else Series.folder_id == target


def _out(row: TreeRow) -> RunFolderOut:
    return RunFolderOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        depth=row.depth,
        path=row.path,
        run_count=row.count,
        total_run_count=row.total,
        created_at=row.created_at,
    )
