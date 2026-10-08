"""Cut plans, exports and sponsor spans.

    POST   /companion/plans                make a plan (keyword or AI mode)
    GET    /companion/plans/{id}           the plan
    GET    /companion/plans/{id}/script    every word it keeps and cuts, with times, why and chapters
    PATCH  /companion/plans/{id}           turn passages on or off, add context around them
    POST   /companion/plans/{id}/render    202 {job_id}: export the plan as one MP3
    GET    /companion/jobs/{id}            the export's progress
    DELETE /companion/jobs/{id}            cancel it
    GET    /companion/cuts/{id}            the MP3's index back to the episodes, and its listen-back check
    GET    /companion/cuts/{id}.mp3        the MP3 (Range requests work)
    DELETE /companion/cuts/{id}            delete it
    GET    /companion/episodes/{id}/ads    sponsor reads found in the transcript
    GET    /companion/storage              audio cache and cut sizes
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import brief, cuts, jobs
from ..deps import PodFetch, Settings, current_user, get_db, get_podfetch, get_settings
from ..llm import LLM, get_llm
from ..transcripts import load_timing

router = APIRouter()


def _has_episode(record: dict[str, Any], episode_id: str) -> bool:
    """Include every source a plan knew about, even when it kept no enabled passage."""
    groups = (record.get("episodes"), record.get("spans"), record.get("omitted"),
              record.get("needs_timing"))
    return any(str(item.get("episode_id")) == episode_id
               for group in groups for item in (group or []) if isinstance(item, dict))


@router.get("/companion/plans")
def list_plans(episode_id: UUID | None = None, limit: int = Query(20, ge=1, le=50),
               learning_mode: cuts.LearningMode | None = None,
               db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    """Saved public plans, newest first. Episode filtering happens before the result limit."""
    wanted = str(episode_id) if episode_id is not None else None
    rows = db.execute(
        "SELECT data FROM cut_plans WHERE user_id=? ORDER BY created_at DESC, id DESC", (user,)
    )
    found = []
    for row in rows:
        record = json.loads(row["data"])
        if (wanted is None or _has_episode(record, wanted)) and (
            learning_mode is None or record.get("learning_mode", "balanced") == learning_mode
        ):
            found.append(cuts.public_plan(record))
            if len(found) == limit:
                break
    return found


@router.post("/companion/plans", status_code=201)
def create_plan(body: cuts.PlanRequest, request: Request, podfetch: PodFetch = Depends(get_podfetch),
                llm: LLM | None = Depends(get_llm), db: sqlite3.Connection = Depends(get_db),
                user: str = Depends(current_user), folders: cuts.Folders = Depends(cuts.get_folders)):
    cuts.check_request(body)
    if body.mode == "ai" and llm is None:
        raise HTTPException(409, cuts.NEED_AI)
    ids = [str(i) for i in body.episode_ids] if body.episode_ids else cuts.queue_episode_ids(podfetch)
    episodes = cuts.load_episodes(ids, podfetch, request.app.state.transcript_library, db, folders)
    chars = getattr(llm, "max_input_chars", None)
    record = cuts.make_plan(body, episodes, llm, max_input_chars=chars if isinstance(chars, int) else None)
    cuts.save_plan(db, record, user)
    return cuts.public_plan(record)


@router.get("/companion/plans/{plan_id}")
def get_plan(plan_id: UUID, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    return cuts.public_plan(cuts.load_plan(db, plan_id, user))


@router.get("/companion/plans/{plan_id}/script")
def get_script(plan_id: UUID, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    """What to check before cutting. Chapters come from the episodes' stored briefs only: this
    never calls AI or PodFetch."""
    record = cuts.load_plan(db, plan_id, user)
    ids = [e["episode_id"] for e in record["episodes"]]
    chapters = {b["episode_id"]: b.get("chapters") or [] for b in brief.cached_briefs(db, user, ids)}
    return cuts.script(record, chapters)


@router.patch("/companion/plans/{plan_id}")
def change_plan(plan_id: UUID, patch: cuts.PlanPatch, db: sqlite3.Connection = Depends(get_db),
                user: str = Depends(current_user)):
    record = cuts.patch_plan(cuts.load_plan(db, plan_id, user), patch)
    cuts.save_plan(db, record, user, new=False)
    return cuts.public_plan(record)


@router.post("/companion/plans/{plan_id}/render", status_code=202)
def render_plan(plan_id: UUID, request: Request, podfetch: PodFetch = Depends(get_podfetch),
                db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user),
                folders: cuts.Folders = Depends(cuts.get_folders), settings: Settings = Depends(get_settings),
                speech: Any = Depends(cuts.get_speech)):
    record = cuts.load_plan(db, plan_id, user)
    if not any(span["enabled"] for span in record["spans"]):
        raise HTTPException(422, "Turn on at least one passage before exporting.")
    try:
        cuts.require_free_disk(folders)
        job_id = jobs.submit(request.app, user=user, plan=record, podfetch=podfetch,
                             library=request.app.state.transcript_library, folders=folders,
                             database=settings.database, speech=speech)
    except cuts.LowDisk as exc:
        raise HTTPException(507, str(exc)) from exc
    except jobs.QueueFull as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"job_id": job_id}


@router.get("/companion/jobs/{job_id}")
def get_job(job_id: UUID, request: Request, db: sqlite3.Connection = Depends(get_db),
            user: str = Depends(current_user)):
    return jobs.read_job(request.app, db, str(job_id), user)


@router.delete("/companion/jobs/{job_id}")
def cancel_job(job_id: UUID, request: Request, db: sqlite3.Connection = Depends(get_db),
               user: str = Depends(current_user)):
    jobs.delete_job(request.app, db, str(job_id), user)
    return {"deleted": True}


def _cut(db: sqlite3.Connection, cut_id: UUID, user: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM cut_files WHERE id=? AND user_id=?", (str(cut_id), user)).fetchone()
    if row is None:
        raise HTTPException(404, "This cut doesn't exist anymore.")
    return row


# Before /companion/cuts/{cut_id}: routes match in the order they are declared.
@router.api_route("/companion/cuts/{cut_id}.mp3", methods=["GET", "HEAD"])
def cut_audio(cut_id: UUID, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user),
              folders: cuts.Folders = Depends(cuts.get_folders)):
    row = _cut(db, cut_id, user)
    path = folders.cuts / f"{cut_id}.mp3"
    if not path.is_file():
        raise HTTPException(404, "This cut's MP3 is missing. Export the plan again.")
    title = json.loads(row["data"]).get("title") or "cut"
    return FileResponse(path, media_type="audio/mpeg", filename=cuts.download_name(title),
                        content_disposition_type="inline")


@router.get("/companion/cuts/{cut_id}")
def get_cut(cut_id: UUID, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user)):
    row = _cut(db, cut_id, user)
    data = json.loads(row["data"])
    return {"id": row["id"], "plan_id": row["plan_id"], "duration": row["duration"],
            "size_bytes": row["size_bytes"], "index": data["index"], "title": data.get("title"),
            "timing": data.get("timing") or [], "label": data.get("label"), "check": data.get("check"),
            "created_at": row["created_at"]}


@router.delete("/companion/cuts/{cut_id}")
def delete_cut(cut_id: UUID, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user),
               folders: cuts.Folders = Depends(cuts.get_folders)):
    _cut(db, cut_id, user)
    jobs.remove_cut_files(folders, str(cut_id))
    db.execute("DELETE FROM cut_files WHERE id=? AND user_id=?", (str(cut_id), user))
    return {"deleted": True}


@router.get("/companion/episodes/{episode_id}/ads")
def episode_ads(episode_id: UUID, request: Request, podfetch: PodFetch = Depends(get_podfetch)):
    episode = podfetch.episode(episode_id)
    timing = load_timing(episode_id, episode, podfetch, request.app.state.transcript_library)
    return {"episode_id": str(episode_id), "spans": cuts.sponsor_spans(timing.segments), "origin": timing.origin}


@router.get("/companion/storage")
def storage(folders: cuts.Folders = Depends(cuts.get_folders)):
    return cuts.storage(folders)
