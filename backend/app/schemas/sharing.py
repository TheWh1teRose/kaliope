"""Explicit public field allowlists; private source ids/blobs never leave the server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReaderSegment(BaseModel):
    speaker: str
    text: str


class ReaderAudio(BaseModel):
    url: str
    duration_s: float


class ReaderEpisode(BaseModel):
    index: int
    title: str
    segments: list[ReaderSegment]
    audio: ReaderAudio | None = None


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


class LinkMetadata(BaseModel):
    id: str
    created_at: str
    expires_at: str
    status: Literal["active", "expired", "revoked"]


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
