"""Shared helpers for notes: targeting, subject resolution, identifiers."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from app.pipeline.framework.node import NodeError
from app.schemas.pipeline import Note, Notes, NoteSubject, Outline, Script

SubjectChoice = Literal["auto", "outline", "script"]


def new_note_id() -> str:
    return f"n{uuid.uuid4().hex[:10]}"


def resolve_subject(
    choice: str | None,
    outline: Outline | None,
    script: Script | None,
) -> NoteSubject:
    """Pick which artifact notes are about.

    ``auto`` prefers a script when both are present — a script is the more
    specific object, and beat ids on it still address the outline's beats.
    """
    wanted = (choice or "auto").strip().lower()
    if wanted == "outline":
        if outline is None:
            raise NodeError("subject is 'outline' but no outline is in the bag")
        return "outline"
    if wanted == "script":
        if script is None:
            raise NodeError("subject is 'script' but no script is in the bag")
        return "script"
    if script is not None:
        return "script"
    if outline is not None:
        return "outline"
    raise NodeError("needs an outline or a script to attach notes to")


def beat_ids(outline: Outline | None, script: Script | None) -> set[str]:
    ids: set[str] = set()
    if outline is not None:
        ids.update(beat.id for beat in outline.beats)
    if script is not None:
        ids.update(segment.beat_id for segment in script.segments if segment.beat_id)
    return ids


def segment_ids(script: Script | None) -> set[str]:
    if script is None:
        return set()
    return {segment.id for segment in script.segments}


def validate_note_targets(
    notes: list[Note],
    *,
    subject: NoteSubject,
    outline: Outline | None,
    script: Script | None,
) -> None:
    """Refuse a note that points at something that is not there."""
    beats = beat_ids(outline, script)
    segments = segment_ids(script)
    for note in notes:
        text = note.text.strip()
        if not text:
            raise ValueError(f"note '{note.id}' has no text")
        if note.target.kind == "segment":
            if subject == "outline":
                raise ValueError(
                    f"note '{note.id}' targets a segment, but these notes are about an outline"
                )
            if note.target.id not in segments:
                raise ValueError(
                    f"note '{note.id}' targets unknown segment '{note.target.id}'"
                )
            continue
        if note.target.id not in beats:
            raise ValueError(f"note '{note.id}' targets unknown beat '{note.target.id}'")


def merge_notes(
    existing: Notes | None,
    incoming: list[Note],
    *,
    subject: NoteSubject,
) -> Notes:
    items = list(existing.items) if existing is not None else []
    seen = {note.id for note in items}
    for note in incoming:
        if note.id in seen:
            items = [note if item.id == note.id else item for item in items]
            continue
        items.append(note)
        seen.add(note.id)
    return Notes(items=items, subject=subject)


def human_notes(raw: list[dict[str, Any]] | list[Note], *, subject: NoteSubject) -> list[Note]:
    """Coerce posted notes and stamp them as human-authored."""
    out: list[Note] = []
    for item in raw:
        data = item.model_dump() if isinstance(item, Note) else dict(item)
        data["id"] = data.get("id") or new_note_id()
        data["source"] = "human"
        data["text"] = str(data.get("text") or "").strip()
        out.append(Note.model_validate(data))
    return out
