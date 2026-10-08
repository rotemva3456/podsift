"""Portable MCP argument schemas; the HTTP service validates them again at its boundary."""
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LearningMode = Literal["balanced", "focus", "chill"]


class SegmentRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_id: str = Field(min_length=1, max_length=120)
    end_id: str = Field(min_length=1, max_length=120)
    why: str = Field(min_length=1, max_length=300)
    relevance: int = Field(default=3, ge=1, le=3)


class EpisodeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: UUID
    transcript_digest: str = Field(pattern=r"^[0-9a-f]{32}$")
    keep: list[SegmentRange] = Field(max_length=100)
    skip: list[SegmentRange] = Field(default_factory=list, max_length=100)
