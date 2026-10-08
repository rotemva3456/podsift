"""What the listener is here for: a short list of topics, picked in the welcome.

GET /companion/profile   {topics: [str, ...]}
PUT /companion/profile   {topics: [str, ...]} -> the saved list

Briefs (companion/brief.py) read the topics with ``topics_for`` and pass them to the verdict
prompt as evidence, so "worth hearing" can lean toward what this listener actually asked for.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from ..db import register_migration
from ..deps import current_user, get_db

router = APIRouter()

register_migration("profile", 1, """CREATE TABLE IF NOT EXISTS profile (
    user_id TEXT PRIMARY KEY, topics TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)""")

TOPICS_MAX = 20
TOPIC_CHARS_MAX = 60


class ProfileInput(BaseModel):
    topics: list[str] = Field(default_factory=list)

    @field_validator("topics")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            word = " ".join(str(item or "").split())[:TOPIC_CHARS_MAX]
            if word and word.lower() not in seen:
                seen.add(word.lower())
                cleaned.append(word)
        return cleaned[:TOPICS_MAX]


def topics_for(db: sqlite3.Connection, user: str) -> list[str]:
    """This user's saved topics, or ``[]`` when they haven't picked any."""
    row = db.execute("SELECT topics FROM profile WHERE user_id=?", (user,)).fetchone()
    if not row:
        return []
    try:
        parsed = json.loads(row["topics"])
    except ValueError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


@router.get("/companion/profile")
def get_profile(db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    return {"topics": topics_for(db, user)}


@router.put("/companion/profile")
def save_profile(body: ProfileInput, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    db.execute("""INSERT INTO profile (user_id, topics, updated_at) VALUES (?,?,?)
                  ON CONFLICT (user_id) DO UPDATE SET topics=excluded.topics, updated_at=excluded.updated_at""",
               (user, json.dumps(body.topics, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    return {"topics": body.topics}
