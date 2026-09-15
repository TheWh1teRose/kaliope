"""Document endpoints (§11).

Zone corrections are stored as ``EditEvent`` rows and applied on read. The
``ParsedDocument`` artifact is never mutated: it is content-addressed, and a run
that already cited it must keep resolving against exactly what it saw. The
corrections are also the training data §5.6 exists to collect, so keeping them
in the event stream rather than folded into the parse is the point.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.folders import ROOT, resolve_folder
from app.config import get_settings
from app.db import get_db
from app.errors import problem
from app.ingestion.extract import render_page_png
from app.models import Document, EditEvent, User
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.api import (
    BlockOut,
    DocumentMove,
    DocumentOut,
    SectionOut,
    StructureOut,
    ZoneUpdate,
)
from app.schemas.document import IngestionReport, ParsedDocument
from app.schemas.review import ReasonCode, requires_note
from app.schemas.zones import Zone, zone_catalogue
from app.security import current_user
from app.worker import worker

router = APIRouter(prefix="/api/documents", tags=["documents"])

_PDF_MAGIC = b"%PDF-"


def _store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    folder_id: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[DocumentOut]:
    """Every document, or one folder's.

    ``folder_id`` absent means every document regardless of folder; the literal
    ``root`` means the documents that are in no folder. Without that literal
    there would be no way to ask for the root, because its stored value is NULL.
    """
    statement = select(Document).order_by(Document.uploaded_at.desc())
    if folder_id == ROOT:
        statement = statement.where(Document.folder_id.is_(None))
    elif folder_id:
        statement = statement.where(Document.folder_id == resolve_folder(db, folder_id))
    return [_document_out(row, include_report=False) for row in db.scalars(statement).all()]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    folder_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    settings = get_settings()
    payload = await file.read()

    if len(payload) > settings.max_upload_bytes:
        raise problem(
            413,
            "File too large",
            f"Uploads are limited to {settings.max_upload_mb} MB; this file is "
            f"{len(payload) / 1024 / 1024:.1f} MB.",
        )
    if not payload.startswith(_PDF_MAGIC):
        raise problem(
            415,
            "Not a PDF",
            "The uploaded file does not begin with the PDF magic bytes (%PDF-).",
        )

    digest = hashlib.sha256(payload).hexdigest()
    settings.ensure_dirs()
    path = settings.uploads_dir / f"{digest}.pdf"
    if not path.exists():
        path.write_bytes(payload)

    target_folder = resolve_folder(db, folder_id)

    existing = db.scalars(select(Document).where(Document.sha256 == digest)).first()
    if existing is not None:
        # Re-uploading the same bytes is how a reviewer files an existing
        # document into a folder, so honour the destination rather than
        # silently returning the old row untouched.
        if target_folder is not None and existing.folder_id != target_folder:
            existing.folder_id = target_folder
            db.commit()
        return _document_out(existing)

    document = Document(
        filename=file.filename or "document.pdf",
        sha256=digest,
        uploaded_by=user.id,
        parse_status="pending",
        folder_id=target_folder,
    )
    db.add(document)
    db.commit()
    worker.submit_parse(document.id)
    return _document_out(document)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> DocumentOut:
    return _document_out(_require(db, document_id))


@router.post("/{document_id}/reparse", response_model=DocumentOut, status_code=202)
def reparse(
    document_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> DocumentOut:
    document = _require(db, document_id)
    document.parse_status = "pending"
    document.parse_error = None
    db.commit()
    worker.submit_parse(document.id)
    return _document_out(document)


@router.post("/{document_id}/move", response_model=DocumentOut)
def move_document(
    document_id: str,
    payload: DocumentMove,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> DocumentOut:
    """File a document into a folder, or back to the root with ``null``."""
    document = _require(db, document_id)
    document.folder_id = resolve_folder(db, payload.folder_id)
    db.commit()
    return _document_out(document, include_report=False)


@router.get("/{document_id}/structure", response_model=StructureOut)
def get_structure(
    document_id: str, db: Session = Depends(get_db), _user: User = Depends(current_user)
) -> StructureOut:
    document = _require(db, document_id)
    parsed = _load_parsed(document)
    overrides = zone_overrides(db, document_id)

    blocks: list[BlockOut] = []
    for block in parsed.blocks:
        override = overrides.get(block.id)
        blocks.append(
            BlockOut(
                id=block.id,
                ordinal=block.ordinal,
                text=block.text,
                page=block.page,
                bboxes=block.bboxes,
                zone=(override or block.zone).value
                if isinstance(override or block.zone, Zone)
                else str(override or block.zone),
                zone_confidence=block.zone_confidence,
                zone_uncertain=block.zone_uncertain and override is None,
                salience=block.salience,
                section_id=block.section_id,
                heading_level=block.heading_level,
                is_table=block.table is not None,
                zone_overridden_from=block.zone.value if override else None,
            )
        )

    return StructureOut(
        document_id=parsed.document_id,
        parse_version=parsed.parse_version,
        language=parsed.language,
        page_count=parsed.page_count,
        page_sizes=parsed.page_sizes,
        sections=[
            SectionOut(
                id=s.id,
                title=s.title,
                level=s.level,
                ordinal=s.ordinal,
                parent_id=s.parent_id,
                block_ids=s.block_ids,
                page_start=s.page_start,
                page_end=s.page_end,
            )
            for s in (parsed.sections or [])
        ]
        if parsed.sections is not None
        else None,
        blocks=blocks,
        objectives=parsed.objectives,
        zone_catalogue=zone_catalogue(),
    )


@router.get("/{document_id}/pages/{page_number}/image")
def page_image(
    page_number: int,
    document_id: str,
    dpi: int = 130,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> Response:
    document = _require(db, document_id)
    settings = get_settings()
    source = settings.uploads_dir / f"{document.sha256}.pdf"
    if not source.exists():
        raise problem(404, "Source missing", "The uploaded PDF is no longer on disk.")
    if page_number < 0 or (document.page_count and page_number >= document.page_count):
        raise problem(404, "No such page", f"Page {page_number} is outside this document.")

    dpi = max(60, min(dpi, 300))
    cache = settings.renders_dir / document.sha256
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / f"{page_number}@{dpi}.png"
    if not cached.exists():
        cached.write_bytes(render_page_png(source, page_number, dpi=dpi))

    return Response(
        content=cached.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.patch("/{document_id}/blocks/{block_id}/zone", status_code=200)
def relabel_block(
    document_id: str,
    block_id: str,
    payload: ZoneUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    document = _require(db, document_id)
    parsed = _load_parsed(document)
    block = parsed.block_by_id(block_id)
    if block is None:
        raise problem(404, "No such block", f"Block '{block_id}' is not in this parse.")

    try:
        zone = Zone(payload.zone)
    except ValueError as exc:
        raise problem(
            422,
            "Unknown zone",
            f"'{payload.zone}' is not in the taxonomy: {', '.join(z.value for z in Zone)}.",
        ) from exc

    try:
        reason = ReasonCode(payload.reason_code)
    except ValueError as exc:
        raise problem(
            422, "Unknown reason code", f"'{payload.reason_code}' is not a reason code."
        ) from exc
    if requires_note(reason) and not (payload.note or "").strip():
        raise problem(422, "Note required", f"Reason code '{reason.value}' requires a note.")

    current = zone_overrides(db, document_id).get(block_id, block.zone)
    db.add(
        EditEvent(
            document_id=document_id,
            run_id=None,
            target_type="block_zone",
            target_id=block_id,
            user_id=user.id,
            action="relabel",
            reason_code=reason.value,
            note=payload.note,
            text_before=current.value if isinstance(current, Zone) else str(current),
            text_after=zone.value,
        )
    )
    db.commit()
    return {"block_id": block_id, "zone": zone.value}


# ---------------------------------------------------------------- helpers


def zone_overrides(db: Session, document_id: str) -> dict[str, Zone]:
    """Latest reviewer relabel per block, derived from the edit event stream."""
    rows = db.scalars(
        select(EditEvent)
        .where(
            EditEvent.document_id == document_id,
            EditEvent.target_type == "block_zone",
        )
        .order_by(EditEvent.created_at.asc())
    ).all()
    overrides: dict[str, Zone] = {}
    for row in rows:
        if not row.text_after:
            continue
        try:
            overrides[row.target_id] = Zone(row.text_after)
        except ValueError:  # pragma: no cover - taxonomy shrank under us
            continue
    return overrides


def load_parsed_document(document: Document) -> ParsedDocument:
    return _load_parsed(document)


def _load_parsed(document: Document) -> ParsedDocument:
    if not document.parsed_artifact_hash:
        raise problem(
            409,
            "Not parsed yet",
            f"Document '{document.id}' has parse status '{document.parse_status}'.",
        )
    store = _store()
    if not store.exists(document.parsed_artifact_hash):
        raise problem(500, "Artifact missing", "The stored parse artifact is gone from disk.")
    return ParsedDocument.model_validate(store.get_raw(document.parsed_artifact_hash))


def _require(db: Session, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise problem(404, "No such document", f"Document '{document_id}' does not exist.")
    return document


def _document_out(document: Document, include_report: bool = True) -> DocumentOut:
    report = None
    if include_report and document.report_json:
        report = IngestionReport.model_validate(document.report_json)
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        sha256=document.sha256,
        title=document.title,
        uploaded_at=_iso(document.uploaded_at),
        uploaded_by=document.uploaded_by,
        page_count=document.page_count,
        language=document.language,
        parse_version=document.parse_version,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
        report=report,
        folder_id=document.folder_id,
    )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""
