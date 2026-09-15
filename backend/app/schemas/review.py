"""Reason codes and review payloads (§9.4).

One shared definition, consumed by the API and exported to the frontend via
``GET /api/review/reason-codes`` so the labels cannot drift. Extending the enum
means adding one row to ``REASON_CODES`` — no code elsewhere enumerates the
values (§15).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReasonCode(StrEnum):
    FACT_WRONG = "FACT_WRONG"
    NOT_IN_DOC = "NOT_IN_DOC"
    UNINTUITIVE_EXAMPLE = "UNINTUITIVE_EXAMPLE"
    WRONG_LEVEL = "WRONG_LEVEL"
    CLUMSY_LANGUAGE = "CLUMSY_LANGUAGE"
    PACING = "PACING"
    MISSING_CONTENT = "MISSING_CONTENT"
    STRUCTURE = "STRUCTURE"
    CITATION_WRONG = "CITATION_WRONG"
    ZONE_WRONG = "ZONE_WRONG"
    OTHER = "OTHER"


class ReasonCodeSpec(BaseModel):
    code: ReasonCode
    label_de: str
    #: Codes that carry no information without a free-text note.
    requires_note: bool = False


REASON_CODES: dict[ReasonCode, ReasonCodeSpec] = {
    spec.code: spec
    for spec in [
        ReasonCodeSpec(code=ReasonCode.FACT_WRONG, label_de="Fachlich falsch"),
        ReasonCodeSpec(code=ReasonCode.NOT_IN_DOC, label_de="Nicht im Dokument belegt"),
        ReasonCodeSpec(
            code=ReasonCode.UNINTUITIVE_EXAMPLE,
            label_de="Beispiel unpassend oder unverständlich",
        ),
        ReasonCodeSpec(code=ReasonCode.WRONG_LEVEL, label_de="Falsches Niveau für die Zielgruppe"),
        ReasonCodeSpec(code=ReasonCode.CLUMSY_LANGUAGE, label_de="Sprachlich unsauber"),
        ReasonCodeSpec(code=ReasonCode.PACING, label_de="Tempo / Länge unpassend"),
        ReasonCodeSpec(code=ReasonCode.MISSING_CONTENT, label_de="Wichtiger Inhalt fehlt"),
        ReasonCodeSpec(code=ReasonCode.STRUCTURE, label_de="Aufbau / Reihenfolge"),
        ReasonCodeSpec(code=ReasonCode.CITATION_WRONG, label_de="Beleg passt nicht zur Aussage"),
        ReasonCodeSpec(code=ReasonCode.ZONE_WRONG, label_de="Textbereich falsch klassifiziert"),
        ReasonCodeSpec(
            code=ReasonCode.OTHER,
            label_de="Sonstiges (Freitext erforderlich)",
            requires_note=True,
        ),
    ]
}


def requires_note(code: ReasonCode) -> bool:
    return REASON_CODES[code].requires_note


def reason_code_catalogue() -> list[dict[str, object]]:
    return [
        {"code": s.code.value, "label_de": s.label_de, "requires_note": s.requires_note}
        for s in REASON_CODES.values()
    ]


TargetType = Literal["segment", "selection", "outline", "block_zone"]
EditAction = Literal["accept", "edit", "flag", "comment", "relabel", "undo", "tag", "untag"]

#: Actions that record a reviewer judgement about *why* something was wrong.
#: ``accept`` and ``comment`` carry no defect claim, so they need no code, and
#: neither does taking one back or filing something under a tag.
ACTIONS_REQUIRING_REASON: frozenset[str] = frozenset({"edit", "flag", "relabel"})

#: Actions that put a segment into a reviewed state, in the order they arrive.
#: ``undo`` takes the most recent one of these back — one step at a time, so a
#: reviewer who accepted after editing returns to the edit rather than to the
#: generated text. Nothing is deleted: the undo is itself an event, and the
#: evaluation export keeps the whole sequence.
UNDOABLE_ACTIONS: frozenset[str] = frozenset({"accept", "edit", "flag"})

#: A tag is free text so reviewers can name a pattern before anyone has agreed
#: a vocabulary for it; what the evaluation work needs is which segments a
#: reviewer grouped together, not that the names came from a fixed list.
MAX_TAG_LENGTH = 40


def clean_tag(raw: str) -> str:
    return " ".join(raw.split())[:MAX_TAG_LENGTH]


class EditEventIn(BaseModel):
    target_type: TargetType
    target_id: str
    action: EditAction
    reason_code: ReasonCode | None = None
    note: str | None = None
    text_before: str | None = None
    text_after: str | None = None

    @model_validator(mode="after")
    def _check_reason(self) -> EditEventIn:
        if self.action in ACTIONS_REQUIRING_REASON and self.reason_code is None:
            raise ValueError(f"action '{self.action}' requires a reason_code")
        needs_note = self.reason_code is not None and requires_note(self.reason_code)
        if needs_note and not (self.note or "").strip():
            raise ValueError(f"reason_code '{self.reason_code}' requires a non-empty note")
        if self.action in {"tag", "untag"} and not clean_tag(self.text_after or ""):
            raise ValueError(f"action '{self.action}' requires a tag in text_after")
        if self.action == "comment" and not (self.note or "").strip():
            raise ValueError("action 'comment' requires a non-empty note")
        return self


class EditEventOut(BaseModel):
    id: str
    run_id: str | None
    document_id: str | None
    target_type: str
    target_id: str
    user_id: str
    user_email: str | None = None
    action: str
    reason_code: str | None
    note: str | None
    text_before: str | None
    text_after: str | None
    created_at: str


class ReviewSummary(BaseModel):
    run_id: str
    reviewer_id: str
    duration_seconds: float
    event_count: int
    reason_code_counts: dict[str, int] = Field(default_factory=dict)
    action_counts: dict[str, int] = Field(default_factory=dict)
    edited_script_artifact: str | None = None
