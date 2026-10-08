from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimingGranularity(str, Enum):
    NONE = "none"
    SEGMENT = "segment"
    WORD = "word"


class TranscriptWord(BaseModel):
    text: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    id: str
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)


class MediaIdentity(BaseModel):
    source_url: str = ""
    sha256: str | None = None
    byte_length: int | None = None
    duration_seconds: float | None = None


class TranscriptDocument(BaseModel):
    episode_id: str
    granularity: TimingGranularity
    source: str | None = None
    media_identity: MediaIdentity | None = None
    media_verified: bool = False
    text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)


class EpisodeSummary(BaseModel):
    id: str
    show_id: str
    number: int | None = None
    title: str
    duration_seconds: float
    published: str = ""
    audio_url: str = ""
    transcript_ready: bool = False
    timing: TimingGranularity = TimingGranularity.NONE


class EpisodeDetail(EpisodeSummary):
    transcript_source: str | None = None


class ShowSummary(BaseModel):
    id: str
    title: str
    feed_url: str = ""
    episode_count: int
    prepared_count: int
    total_seconds: float


class LibraryResponse(BaseModel):
    shows: list[ShowSummary]
    episodes: list[EpisodeSummary]


class PlaybackUpdate(BaseModel):
    session_id: str = "default"
    episode_id: str
    source_position: float = Field(ge=0)
    playing: bool = False


class PlaybackState(PlaybackUpdate):
    show_id: str
    listened_seconds: float = 0
    updated_at: datetime = Field(default_factory=utc_now)


class SourceCitation(BaseModel):
    episode_id: str
    segment_id: str
    start: float
    end: float
    text: str


class AgentContextRequest(BaseModel):
    session_id: str = "default"
    question: str = "What was just explained?"
    window_seconds: float = Field(default=90, ge=15, le=600)


class AgentContext(BaseModel):
    question: str
    playback: PlaybackState
    episode: EpisodeDetail
    citations: list[SourceCitation]
    context_text: str
    status: str = "context_ready"


class RecapItem(BaseModel):
    episode: EpisodeSummary
    listened_seconds: float
    last_position: float
    last_listened_at: datetime


class LearningRecap(BaseModel):
    listened_seconds: float
    episodes_started: int
    recent: list[RecapItem]


class Recommendation(BaseModel):
    episode: EpisodeSummary
    reason: str
    score: float
