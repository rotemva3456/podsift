"""Native skip passages with transcript evidence; never uses SponsorBlock data."""
from __future__ import annotations

import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import engine
from ..db import register_migration
from ..deps import PodFetch, current_user, get_db, get_podfetch
from ..engine.skipping import skip_spans
from ..transcripts import Timing, load_timing

router = APIRouter()

LABELS = {"sponsor": "Sponsor", "selfpromo": "Self-promotion", "interaction": "Interaction reminder",
          "intro": "Intro", "outro": "Outro", "preview": "Preview or recap", "filler": "Filler",
          "music_offtopic": "Non-music part"}

Category = Literal["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler", "music_offtopic"]

register_migration("native_skipping", 1, """CREATE TABLE native_skip_preferences (
    user_id TEXT PRIMARY KEY, settings TEXT NOT NULL)""")


class SkipPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    manual_categories: list[Category] = Field(default_factory=list, max_length=8)
    minimum_seconds: float = Field(default=0, ge=0, le=60, strict=True)

    @field_validator("manual_categories")
    @classmethod
    def unique_categories(cls, categories: list[Category]) -> list[Category]:
        return list(dict.fromkeys(categories))


@router.get("/companion/settings/skipping", response_model=SkipPreferences)
def skip_preferences(db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    row = db.execute("SELECT settings FROM native_skip_preferences WHERE user_id=?", (user,)).fetchone()
    return SkipPreferences.model_validate_json(row["settings"]) if row else SkipPreferences()


@router.put("/companion/settings/skipping", response_model=SkipPreferences)
def save_skip_preferences(settings: SkipPreferences, db: sqlite3.Connection = Depends(get_db),
                          user: str = Depends(current_user)):
    db.execute("""INSERT INTO native_skip_preferences (user_id, settings) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings""", (user, settings.model_dump_json()))
    return settings


def timing_status(timing: Timing, duration: float | None) -> str:
    if not timing.segments:
        return "missing"
    # Reuse the export engine's source check; a similar length does not prove file identity.
    checked = engine.check_timing(timing.segments, timing.origin or "", {"duration_seconds": duration},
                                  timing.made_from, tolerance=1)
    if checked == "mismatch":
        return "mismatch"
    if checked == "ok" and duration:
        return "matched"
    return "unverified"


@router.get("/companion/episodes/{episode_id}/skip-segments")
def episode_skip_segments(episode_id: UUID, request: Request, podfetch: PodFetch = Depends(get_podfetch)):
    episode = podfetch.episode(episode_id)
    timing = load_timing(episode_id, episode, podfetch, request.app.state.transcript_library)
    duration = engine.media_duration({"duration_seconds": episode.get("total_time")})
    status = timing_status(timing, duration)
    spans = skip_spans(timing.segments, duration=duration) if status != "mismatch" else []
    return {"episode_id": str(episode_id), "source": "podsift-transcript", "origin": timing.origin,
            "timed": bool(timing.segments), "transcript_digest": timing.digest, "duration": duration,
            "timing_status": status, "auto_skip_safe": status == "matched",
            "spans": [{"start": span.start, "end": span.end, "category": span.category,
                       "label": LABELS[span.category], "reason": span.reason,
                       "segment_ids": list(span.segment_ids)} for span in spans]}
