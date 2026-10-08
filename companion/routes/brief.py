"""Episode briefs.

GET  /companion/episodes/{id}/brief     the brief as it stands; never calls AI
POST /companion/episodes/{id}/brief     make it: 202 while it is made, or 200 with the cached one.
                                        {"regenerate": true} makes it again.
GET  /companion/briefs?ids=a,b          cached briefs only (episode row badges); never calls AI
POST /companion/briefs/queue            {"episode_ids": [...] (at most 20), "dry_run": bool}; a dry run
                                        returns what it would send (episodes, input tokens) and starts nothing
GET  /companion/briefs/queue            the queue's progress;  DELETE stops it after the current episode
"""
from __future__ import annotations

import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import brief as briefs
from ..deps import PodFetch, Settings, TranscriptLoader, current_user, get_db, get_podfetch, get_settings, get_transcripts
from ..llm import LLM, get_llm

router = APIRouter()


class MakeInput(BaseModel):
    regenerate: bool = False


class QueueInput(BaseModel):
    episode_ids: list[UUID]
    dry_run: bool = False


@router.get("/companion/episodes/{episode_id}/brief")
def get_brief(episode_id: UUID, request: Request, user: str = Depends(current_user),
              podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts),
              db: sqlite3.Connection = Depends(get_db), llm: LLM | None = Depends(get_llm)):
    runner = briefs.runner_for(request.app)
    return briefs.read_brief(episode_id, user, briefs.BriefContext(podfetch, transcripts, db, llm),
                             generating=runner.running(user, episode_id), error=runner.error(user, episode_id))


@router.post("/companion/episodes/{episode_id}/brief")
def make_brief(episode_id: UUID, request: Request, body: MakeInput | None = None, user: str = Depends(current_user),
               podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts),
               db: sqlite3.Connection = Depends(get_db), llm: LLM | None = Depends(get_llm),
               settings: Settings = Depends(get_settings)):
    if llm is None:
        raise HTTPException(409, briefs.CONNECT_AI)
    runner, regenerate = briefs.runner_for(request.app), bool(body and body.regenerate)
    current = briefs.read_brief(episode_id, user, briefs.BriefContext(podfetch, transcripts, db, llm),
                                generating=runner.running(user, episode_id))
    if current["status"] == "no_transcript":
        raise HTTPException(409, briefs.NO_TRANSCRIPT)
    if current["status"] == "generating":
        return JSONResponse(current, status_code=202)
    if current["status"] == "ready" and not regenerate and current["model"] == briefs.model_name(llm):
        return current
    runner.start(user, episode_id, lambda: briefs.generate_in_background(
        settings.database, podfetch, transcripts, llm, user, str(episode_id), regenerate))
    return JSONResponse({**current, "status": "generating", "error": None}, status_code=202)


@router.get("/companion/briefs")
def cached_briefs(ids: str = "", user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
                  llm: LLM | None = Depends(get_llm)):
    wanted = []
    for part in ids.split(","):
        if part.strip():
            try:
                wanted.append(str(UUID(part.strip())))
            except ValueError:
                raise HTTPException(422, "Give episode ids separated by commas.") from None
    if len(wanted) > briefs.IDS_MAX:
        raise HTTPException(422, f"Ask for at most {briefs.IDS_MAX} briefs at a time.")
    return briefs.cached_briefs(db, user, wanted, ai_ready=llm is not None)


@router.post("/companion/briefs/queue")
def queue_briefs(body: QueueInput, request: Request, user: str = Depends(current_user),
                 podfetch: PodFetch = Depends(get_podfetch), transcripts: TranscriptLoader = Depends(get_transcripts),
                 db: sqlite3.Connection = Depends(get_db), llm: LLM | None = Depends(get_llm),
                 settings: Settings = Depends(get_settings)):
    if llm is None:
        raise HTTPException(409, briefs.CONNECT_AI)
    ids = list(dict.fromkeys(str(episode_id) for episode_id in body.episode_ids))
    if not ids:
        raise HTTPException(422, "Pick at least one episode.")
    if len(ids) > briefs.QUEUE_MAX:
        raise HTTPException(422, f"Pick at most {briefs.QUEUE_MAX} episodes at a time.")
    plan = briefs.estimate(ids, user, briefs.BriefContext(podfetch, transcripts, db, llm))
    if body.dry_run:
        return plan
    todo = [item["episode_id"] for item in plan["items"] if item["status"] == "will_brief"]
    if not todo:
        raise HTTPException(409, "There is nothing to brief: these episodes have a brief already or no transcript.")
    state = briefs.runner_for(request.app).start_queue(user, todo, lambda episode_id: briefs.generate_in_background(
        settings.database, podfetch, transcripts, llm, user, episode_id))
    return JSONResponse({**state, "input_tokens": plan["input_tokens"], "ai_ready": True}, status_code=202)


@router.get("/companion/briefs/queue")
def queue_progress(request: Request, user: str = Depends(current_user), llm: LLM | None = Depends(get_llm)):
    state = briefs.runner_for(request.app).queue(user) or {"status": "idle"}
    return {**state, "ai_ready": llm is not None}


@router.delete("/companion/briefs/queue")
def stop_queue(request: Request, user: str = Depends(current_user)):
    state = briefs.runner_for(request.app).cancel(user)
    if state is None:
        raise HTTPException(404, "No brief queue is running.")
    return state
