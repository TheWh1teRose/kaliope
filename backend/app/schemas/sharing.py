"""Explicit public field allowlists; private source ids/blobs never leave the server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReaderRect(BaseModel):
    page: int
    bbox: tuple[float, float, float, float]


class ReaderCitation(BaseModel):
    """A highlight on a frozen page. No workspace block id."""

    rects: list[ReaderRect] = Field(default_factory=list)


class ReaderSegment(BaseModel):
    ordinal: int = 0
    start_s: float | None = None
    end_s: float | None = None
    speaker: str
    text: str
    citations: list[ReaderCitation] = Field(default_factory=list)


class ReaderPage(BaseModel):
    page: int
    width: float
    height: float
    url: str


class ReaderAudio(BaseModel):
    url: str
    duration_s: float


class ReaderEpisode(BaseModel):
    index: int
    title: str
    segments: list[ReaderSegment]
    audio: ReaderAudio | None = None
    pages: list[ReaderPage] = Field(default_factory=list)


class ReaderSnapshot(BaseModel):
    title: str
    created_at: str
    expires_at: str
    episodes: list[ReaderEpisode]


class EpisodeChoice(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    take_id: str | None = None


class ShareSelection(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    episodes: list[EpisodeChoice] = Field(min_length=1, max_length=50)
    acknowledge_missing_audio: bool = False


class CreateShare(ShareSelection):
    preview_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_link_id: str | None = None


ReactionName = Literal["impressed", "dislike", "horrible"]


class MarkIn(BaseModel):
    episode: int
    ordinal: int = Field(ge=0)
    reaction: ReactionName | None = None
    slop: bool = False
    comment: str | None = Field(default=None, max_length=500)


class MarkState(BaseModel):
    episode: int
    ordinal: int
    reaction: ReactionName | None
    slop: bool = False
    comment: str | None = None


class FeedbackState(BaseModel):
    label: str | None = None
    stars: float | None = None
    worked: str | None = None
    did_not: str | None = None
    marks: list[MarkState] = Field(default_factory=list)


class SheetIn(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    stars: float | None = None
    worked: str | None = Field(default=None, max_length=2000)
    did_not: str | None = Field(default=None, max_length=2000)


class MarkedLine(BaseModel):
    response: int
    label: str | None = None
    key: str
    speaker: str
    text: str
    reaction: ReactionName | None
    slop: bool
    comment: str | None = None


class ResponseSummary(BaseModel):
    index: int
    label: str | None = None
    stars: float | None = None
    worked: str | None = None
    did_not: str | None = None
    impressed: int = 0
    dislike: int = 0
    horrible: int = 0
    slop: int = 0


class FeedbackSummary(BaseModel):
    responses: list[ResponseSummary] = Field(default_factory=list)
    impressed: int = 0
    dislike: int = 0
    horrible: int = 0
    slop: int = 0
    lines: list[MarkedLine] = Field(default_factory=list)


class LinkMetadata(BaseModel):
    id: str
    created_at: str
    expires_at: str
    status: Literal["active", "expired", "revoked"]
    feedback: FeedbackSummary = Field(default_factory=FeedbackSummary)


class OwnerLinks(BaseModel):
    configured: bool
    links: list[LinkMetadata]


class CreatedLink(BaseModel):
    link: LinkMetadata
    url: str


class TakeOption(BaseModel):
    id: str
    created_at: str
    duration_s: float
    older_script: bool


class EpisodeOptions(BaseModel):
    index: int
    title: str
    takes: list[TakeOption]


class ShareOptions(BaseModel):
    title: str
    episodes: list[EpisodeOptions]
