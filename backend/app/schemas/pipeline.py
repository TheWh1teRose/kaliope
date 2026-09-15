"""Node input/output models for the baseline flow (§6)."""

from __future__ import annotations

import warnings
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document import Anchor

# ``FormatSpec.register`` is the field name §6.5 specifies, and it is part of the
# API contract. It shadows ``ModelMetaclass.register`` — a class-level method
# that instances never reach — so the field behaves correctly and only the
# defensive warning is unwanted.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "register" in "FormatSpec" shadows an attribute',
    category=UserWarning,
)


class SpeakerSpec(BaseModel):
    id: str
    name: str
    role: str
    #: Free-text guidance; never used as a branch condition.
    voice_note: str | None = None


class FormatSpec(BaseModel):
    """§6.5 — data, not prompt text."""

    id: str
    name: str
    speakers: list[SpeakerSpec]
    register: str
    target_minutes: int
    opening: str | None = None
    closing: str | None = None
    beats_hint: str | None = None

    def speaker_ids(self) -> list[str]:
        return [s.id for s in self.speakers]

    def speaker_names(self) -> list[str]:
        return [s.name for s in self.speakers]


class AudienceSpec(BaseModel):
    """§6.6."""

    description: str
    prior_knowledge: str | None = None
    role: str | None = None
    listening_context: str | None = None
    desired_outcome: str | None = None


BloomLevel = Literal["remember", "understand", "apply", "analyse", "evaluate", "create"]

BLOOM_LEVELS: tuple[BloomLevel, ...] = (
    "remember",
    "understand",
    "apply",
    "analyse",
    "evaluate",
    "create",
)


class Objective(BaseModel):
    """A listener-facing change the episode is meant to produce."""

    id: str
    text: str
    bloom_level: BloomLevel
    #: How this objective was derived from ``AudienceSpec.desired_outcome``.
    derivation: str | None = None


class Objectives(BaseModel):
    """What should be different for the listener after the episode."""

    items: list[Objective]
    desired_outcome: str
    rationale: str


BudgetVerdict = Literal["ok", "clamped", "insufficient"]


class ContentBudget(BaseModel):
    """§6.1."""

    narratable_words: int
    words_per_minute: int
    min_compression: float
    max_supportable_minutes: float
    requested_minutes: int
    target_minutes: float
    compression_ratio: float
    verdict: BudgetVerdict
    explanation: str

    @property
    def target_words(self) -> int:
        return int(round(self.target_minutes * self.words_per_minute))


class LearningGoal(BaseModel):
    id: str
    text: str
    source: Literal["document", "generated"]


class SelectedBlock(BaseModel):
    block_id: str
    #: Weight the selection prompt received for this block (zone salience).
    salience: float = 1.0
    reason: str | None = None
    goal_ids: list[str] = Field(default_factory=list)


class Selection(BaseModel):
    """§6.2."""

    learning_goals: list[LearningGoal]
    selected_blocks: list[SelectedBlock]
    rationale: str

    def block_ids(self) -> list[str]:
        return [b.block_id for b in self.selected_blocks]


class Beat(BaseModel):
    id: str
    title: str
    block_ids: list[str] = Field(default_factory=list)
    word_budget: int
    goal_id: str | None = None
    summary: str | None = None


class Outline(BaseModel):
    """§6.3."""

    beats: list[Beat]

    def total_budget(self) -> int:
        return sum(b.word_budget for b in self.beats)


class Segment(BaseModel):
    """§6.4."""

    id: str
    speaker: str
    text: str
    kind: Literal["claim", "pedagogy"]
    anchors: list[Anchor] = Field(default_factory=list)
    beat_id: str

    def word_count(self) -> int:
        return len(self.text.split())


class Script(BaseModel):
    segments: list[Segment]

    def word_count(self) -> int:
        return sum(s.word_count() for s in self.segments)


NoteSource = Literal["ai_critic", "human"]
NoteTargetKind = Literal["beat", "segment"]
NoteSubject = Literal["outline", "script"]


class NoteTarget(BaseModel):
    """What a note is about: an outline/script beat, or a script segment."""

    kind: NoteTargetKind
    id: str


class Note(BaseModel):
    """One remark on an outline or a script, collected for a later revision."""

    id: str
    text: str
    target: NoteTarget
    source: NoteSource
    #: The criterion the critic was applying, when the note came from a model.
    criterion: str | None = None


class Notes(BaseModel):
    """A collected set of notes, all about the same kind of artifact."""

    items: list[Note] = Field(default_factory=list)
    subject: NoteSubject = "script"


class IngestOutput(BaseModel):
    """What the ingest node publishes: a pointer plus the parse itself."""

    document_id: str
    parse_version: int
    parsed_artifact_hash: str
