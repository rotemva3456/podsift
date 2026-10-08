"""Flashcards.

GET  /companion/episodes/{id}/cards      this episode's cards; never calls AI
POST /companion/episodes/{id}/cards      make them (needs AI); {"regenerate": true} replaces them
GET  /companion/review/due               cards due now, oldest due first
POST /companion/review/{card_id}/grade   {"grade": "again"|"hard"|"good"|"easy"} -> reschedules (SM-2)
POST /companion/review/replay            a cut plan of every card answered Again 2+ times
GET  /companion/review/export.tsv        Anki-importable export (front, back, tags)
GET  /companion/review/export.md         Markdown export (Obsidian)
"""
from __future__ import annotations

import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import brief as briefs
from .. import cards_store, cuts
from ..deps import PodFetch, TranscriptLoader, current_user, get_db, get_podfetch, get_transcripts
from ..llm import LLM, LLMError, get_llm

router = APIRouter()


class MakeInput(BaseModel):
    regenerate: bool = False


class GradeInput(BaseModel):
    grade: str


@router.get("/companion/episodes/{episode_id}/cards")
def get_cards(episode_id: UUID, user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
             llm: LLM | None = Depends(get_llm), podfetch: PodFetch = Depends(get_podfetch),
             transcripts: TranscriptLoader = Depends(get_transcripts)):
    episode_id = str(episode_id)
    cards = cards_store.read_cards(db, user, episode_id)
    has_transcript = True
    if not cards:
        try:
            episode = podfetch.episode(episode_id)
            has_transcript = bool(briefs.timed_segments(transcripts(episode_id, episode)))
        except HTTPException:
            has_transcript = True     # a transient PodFetch error shouldn't hide the panel; POST surfaces it
    return {"episode_id": episode_id, "cards": cards, "ai_ready": llm is not None, "has_transcript": has_transcript}


@router.post("/companion/episodes/{episode_id}/cards")
def make_cards(episode_id: UUID, body: MakeInput | None = None, user: str = Depends(current_user),
              podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts),
              db: sqlite3.Connection = Depends(get_db), llm: LLM | None = Depends(get_llm)):
    try:
        result = cards_store.generate_cards(str(episode_id), user, podfetch, transcripts, db, llm,
                                            regenerate=bool(body and body.regenerate))
    except (LLMError, cards_store.CardProblem) as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**result, "ai_ready": True}


@router.get("/companion/review/due")
def due(limit: int = 50, user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    limit = max(1, min(int(limit), 200))
    return {"count": cards_store.due_count(db, user), "cards": cards_store.due_cards(db, user, limit=limit)}


@router.post("/companion/review/{card_id}/grade")
def grade(card_id: str, body: GradeInput, user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        return cards_store.grade_card(db, user, card_id, body.grade)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/companion/review/replay")
def replay(request: Request, user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
          podfetch: PodFetch = Depends(get_podfetch), folders: cuts.Folders = Depends(cuts.get_folders)):
    candidates = cards_store.replay_candidates(db, user)
    if not candidates:
        raise HTTPException(409, cards_store.NOTHING_TO_REPLAY)
    episode_ids = list(dict.fromkeys(str(c["episode_id"]) for c in candidates))
    episodes = cuts.load_episodes(episode_ids, podfetch, request.app.state.transcript_library, db, folders)
    record = cards_store.replay_plan(candidates, episodes)
    if record["status"] != "ready":
        raise HTTPException(409, cards_store.NO_REPLAYABLE)
    cuts.save_plan(db, record, user)
    return cuts.public_plan(record)


@router.get("/companion/review/export.tsv")
def export_tsv(user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return PlainTextResponse(cards_store.anki_tsv(db, user), media_type="text/tab-separated-values",
                             headers={"Content-Disposition": "attachment; filename=cards.tsv"})


@router.get("/companion/review/export.md")
def export_md(user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return PlainTextResponse(cards_store.obsidian_markdown(db, user), media_type="text/markdown",
                             headers={"Content-Disposition": "attachment; filename=cards.md"})
