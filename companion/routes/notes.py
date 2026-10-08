from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..brief import timed_segments
from ..db import register_migration
from ..deps import PodFetch, TranscriptLoader, current_user, get_db, get_podfetch, get_transcripts
from ..recap import quote_for

router = APIRouter()

# Version 1 is the table the app created before migrations existed, so existing files pass.
register_migration("notes", 1, """CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, title TEXT NOT NULL,
            position REAL NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)""")
register_migration("notes", 2, """ALTER TABLE notes ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS notes_by_user_episode ON notes (user_id, episode_id)""")
# Highlights ("Save last 30 s") and voice notes share this table with plain notes.
# `kind` tells them apart; every existing row, and every caller that never sends it, is "note".
register_migration("notes", 3, """ALTER TABLE notes ADD COLUMN kind TEXT NOT NULL DEFAULT 'note';
ALTER TABLE notes ADD COLUMN "start" REAL;
ALTER TABLE notes ADD COLUMN "end" REAL;
ALTER TABLE notes ADD COLUMN quote TEXT;
CREATE INDEX IF NOT EXISTS notes_by_user_kind ON notes (user_id, kind, created_at)""")

# `"start"`/`"end"` are quoted because both are SQL keywords; the JSON keys they produce are plain.
COLUMNS = 'id, episode_id, title, position, text, created_at, kind, "start", "end", quote'
FIELDS = ("id", "episode_id", "title", "position", "text", "created_at", "kind", "start", "end", "quote")


class NoteInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: UUID
    episode_id: UUID
    position: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000)
    # Optional, so existing voice notes (today's fields only) keep working unchanged.
    kind: Literal["note", "highlight"] = "note"
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _highlight_needs_its_range(self) -> "NoteInput":
        if self.kind == "highlight":
            if self.start is None or self.end is None or self.end <= self.start:
                raise ValueError("A highlight needs start and end seconds, with end after start.")
            if not (self.quote or "").strip():
                raise ValueError("A highlight needs the transcript quote for its range.")
        return self


def _row(record: sqlite3.Row) -> dict:
    return {key: record[key] for key in FIELDS}


def list_notes(db: sqlite3.Connection, user: str, *, episode_id: UUID | str | None = None,
               kind: str | None = None, since: str | None = None) -> list[dict]:
    """Every note (or highlight) of ``user``, newest first. For other features: see recap.py."""
    where, params = ["user_id=?"], [user]
    if episode_id:
        where.append("episode_id=?")
        params.append(str(episode_id))
    if kind:
        where.append("kind=?")
        params.append(kind)
    if since:
        where.append("created_at>=?")
        params.append(since)
    rows = db.execute(f"SELECT {COLUMNS} FROM notes WHERE {' AND '.join(where)} ORDER BY created_at DESC", params)
    return [_row(row) for row in rows]


@router.get("/companion/notes")
def notes(episode_id: UUID | None = None, kind: str | None = None, db: sqlite3.Connection = Depends(get_db),
          user: str = Depends(current_user)):
    return list_notes(db, user, episode_id=episode_id, kind=kind)


@router.post("/companion/notes", status_code=201)
def save_note(note: NoteInput, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user),
              podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts)):
    text = note.text.strip()
    if not text:
        raise HTTPException(422, "Write a note before saving.")
    episode = podfetch.episode(note.episode_id)
    if episode.get("total_time") and note.position > episode["total_time"]:
        raise HTTPException(422, "The note timestamp is past the end of this episode.")
    if note.end is not None and episode.get("total_time") and note.end > episode["total_time"]:
        raise HTTPException(422, "That range runs past the end of this episode.")
    quote = (note.quote or "").strip() or None
    if note.kind == "highlight":
        # The client's quote is a fallback only: the server computes the authoritative one from
        # the real transcript, so a highlight quotes exactly its own range, not whole paragraphs.
        segments = timed_segments(transcripts(note.episode_id, episode))
        quote = quote_for(segments, note.start, note.end) or quote
    previous = db.execute(f"SELECT {COLUMNS}, user_id FROM notes WHERE id=?", (str(note.id),)).fetchone()
    if previous:
        same = (previous["user_id"] == user and previous["episode_id"] == str(note.episode_id)
                and previous["text"] == text and previous["position"] == note.position
                and previous["kind"] == note.kind and previous["start"] == note.start
                and previous["end"] == note.end and previous["quote"] == quote)
        if not same:
            raise HTTPException(409, "That note ID is already in use.")
        return _row(previous)
    row = {"id": str(note.id), "episode_id": str(note.episode_id), "title": episode["name"],
           "position": note.position, "text": text, "created_at": datetime.now(timezone.utc).isoformat(),
           "kind": note.kind, "start": note.start, "end": note.end, "quote": quote}
    db.execute(f"""INSERT INTO notes ({COLUMNS}, user_id)
                   VALUES (:id,:episode_id,:title,:position,:text,:created_at,:kind,:start,:end,:quote,:user_id)""",
               {**row, "user_id": user})
    return row
