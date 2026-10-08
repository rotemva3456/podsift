"""Highlights and recap.

GET  /companion/episodes/{id}/recap            heard status, cached key ideas, your highlights and
                                                notes, and the cached "what you learned" paragraph;
                                                never calls AI and never generates a brief
POST /companion/episodes/{id}/recap/learned    make the paragraph (needs AI and a transcript);
                                                {"regenerate": true} makes it again
GET  /companion/recap/week                     this week: episodes, minutes, key ideas, highlights

Highlights themselves are saved through POST /companion/notes (companion/routes/notes.py) with
kind="highlight"; this file only reads them back.
"""
from __future__ import annotations

import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import recap as recaps
from ..deps import PodFetch, TranscriptLoader, current_user, get_db, get_podfetch, get_transcripts
from ..llm import LLM, get_llm

router = APIRouter()


class LearnedInput(BaseModel):
    regenerate: bool = False


@router.get("/companion/episodes/{episode_id}/recap")
def get_recap(episode_id: UUID, user: str = Depends(current_user), podfetch: PodFetch = Depends(get_podfetch),
              transcripts: TranscriptLoader = Depends(get_transcripts), db: sqlite3.Connection = Depends(get_db),
              llm: LLM | None = Depends(get_llm)):
    ctx = recaps.RecapContext(podfetch, transcripts, db, llm)
    return recaps.episode_recap(ctx, episode_id, user)


@router.post("/companion/episodes/{episode_id}/recap/learned")
def make_learned(episode_id: UUID, body: LearnedInput | None = None, user: str = Depends(current_user),
                 podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts),
                 db: sqlite3.Connection = Depends(get_db), llm: LLM | None = Depends(get_llm)):
    ctx = recaps.RecapContext(podfetch, transcripts, db, llm)
    return recaps.generate_learned(ctx, episode_id, user, regenerate=bool(body and body.regenerate))


@router.get("/companion/recap/week")
def get_week(user: str = Depends(current_user), podfetch: PodFetch = Depends(get_podfetch),
            transcripts: TranscriptLoader = Depends(get_transcripts), db: sqlite3.Connection = Depends(get_db),
            llm: LLM | None = Depends(get_llm)):
    ctx = recaps.RecapContext(podfetch, transcripts, db, llm)
    return recaps.weekly_recap(ctx, user)
