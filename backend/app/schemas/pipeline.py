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

#: How many times longer a podcast may run than a plain read-through of the same
#: words. A read-through is narratable words divided by the language's speaking
#: rate (German: 135 per minute). Questions, explanation and back-and-forth need
#: the rest of the minute: at 2.5×, about 54 of those 135 spoken words develop
#: the source and the other 81 are dialogue. 2.5 is the middle of the requested
#: two-to-three range, and the same magnitude previously applied as compression,
#: which made episodes shorter than reading the source aloud. A 15-minute
#: episode therefore wants about 810 German source words, not the 2,025 of a
#: read-through. The 2,800-word series sample is a 20.7-minute read and about
#: 52 minutes of podcast: three 15-minute episodes with room to talk.
DEFAULT_DIALOGUE_EXPANSION = 2.5


class ContentBudget(BaseModel):
    """§6.1."""

    narratable_words: int
    words_per_minute: int
    min_compression: float
    #: Stretches a plain read-through into podcast minutes. Missing on older artifacts.
    dialogue_expansion: float = DEFAULT_DIALOGUE_EXPANSION
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


# ------------------------------------------------------------------ series


class SeriesRequest(BaseModel):
    """What a series run asks the planner for."""

    #: ``None`` lets the planner choose the count from the content budget.
    episodes: int | None = None
    minutes_per_episode: int
    #: Free-text guidance on how to split, e.g. "Paris in its own episode".
    hint: str | None = None


class SeriesTerm(BaseModel):
    term: str
    gloss: str = ""
    #: 1-based index of the episode that introduces the term.
    first_episode: int = 1


class EpisodePlan(BaseModel):
    """One episode's share of the document, as the planner decided it."""

    id: str
    #: 1-based position in the series.
    index: int
    title: str
    role: str = ""
    summary: str | None = None
    #: The episode's own passages; each narratable block has at most one home.
    block_ids: list[str] = Field(default_factory=list)
    #: A few passages of earlier episodes this one may cite again for a recap.
    recap_block_ids: list[str] = Field(default_factory=list)
    goals: list[LearningGoal] = Field(default_factory=list)
    #: The document's stated objectives this episode serves, verbatim.
    objective_refs: list[str] = Field(default_factory=list)
    target_minutes: float
    supportable_minutes: float
    #: What to recall from earlier episodes, and what the next one answers.
    recap: str | None = None
    preview: str | None = None


class UnassignedBlock(BaseModel):
    block_id: str
    reason: str


SeriesVerdict = Literal["ok", "clamped", "reduced"]


class SeriesBudget(BaseModel):
    max_supportable_minutes: float
    minutes_per_episode: int
    requested_episodes: int | None = None
    verdict: SeriesVerdict
    explanation: str


class SeriesPlan(BaseModel):
    """How a document is split into a series of episodes."""

    title: str
    #: The arc across the episodes, in one or two sentences.
    through_line: str = ""
    terms: list[SeriesTerm] = Field(default_factory=list)
    episodes: list[EpisodePlan]
    unassigned: list[UnassignedBlock] = Field(default_factory=list)
    budget: SeriesBudget
    #: Episodes that carry more source words than the dialogue budget allows.
    #: The passages stay; the text asks for more episodes.
    warnings: list[str] = Field(default_factory=list)

    def episode(self, index: int) -> EpisodePlan:
        for episode in self.episodes:
            if episode.index == index:
                return episode
        raise KeyError(f"the series plan has no episode {index}")


class EpisodeBrief(BaseModel):
    """One episode's slice of the series plan, the input of its early nodes.

    It holds the series header and this episode only, so changing another
    episode's share leaves this one's cache keys untouched.
    """

    series_title: str
    through_line: str = ""
    episode_count: int
    terms: list[SeriesTerm] = Field(default_factory=list)
    episode: EpisodePlan

    @classmethod
    def from_plan(cls, plan: SeriesPlan, index: int) -> EpisodeBrief:
        return cls(
            series_title=plan.title,
            through_line=plan.through_line,
            episode_count=len(plan.episodes),
            terms=plan.terms,
            episode=plan.episode(index),
        )


class EpisodeSummary(BaseModel):
    index: int
    title: str
    role: str = ""
    summary: str | None = None


class EpisodeOutline(BaseModel):
    index: int
    title: str
    #: ``(title, summary)`` per beat.
    beats: list[tuple[str, str]] = Field(default_factory=list)


class EarlierEpisode(BaseModel):
    index: int
    title: str
    #: The final script as ``Speaker: text`` lines, without block ids.
    lines: list[str] = Field(default_factory=list)


class SeriesContext(BaseModel):
    """What the script of one episode sees of the rest of the series."""

    series_title: str
    through_line: str = ""
    terms: list[SeriesTerm] = Field(default_factory=list)
    episode_index: int
    episodes: list[EpisodeSummary]
    #: The outlines of the other episodes.
    outlines: list[EpisodeOutline] = Field(default_factory=list)
    #: The full text of the episodes before this one, in order.
    earlier: list[EarlierEpisode] = Field(default_factory=list)
