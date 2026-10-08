"""Worth hearing: new episodes of your watched shows, briefed for you.

GET  /companion/settings/worth-hearing            the setting (off by default), plus login_blocks_auto
PUT  /companion/settings/worth-hearing            {enabled, show_ids, daily_cap}
POST /companion/settings/worth-hearing/estimate    what that would send today; shown before Save
GET  /companion/worth-hearing                     {hear: [...], other: [...], login_blocks_auto} — the page
GET  /companion/feed/{token}/worth-hearing.xml    the HEAR list as a feed (public: the token is the login)

``login_blocks_auto`` (companion/autobrief.py) is true on a PodFetch that needs a login: the
background check and this feed have no session to read it with, so every response here says so
plainly instead of quietly staying empty forever.

The background check itself (companion/autobrief.py) is started and stopped with this router's
lifespan, so it runs for the life of the app under both a plain ``create_app()`` served for real
(``with TestClient(app) as client`` drives the ASGI lifespan protocol; so does uvicorn) and the
deployed ``uvicorn companion.server:default_app --factory``. A bare ``create_app()`` that is never
served never starts it, same as any other ASGI app — there is no server to send the startup event.
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import autobrief
from .. import brief as briefs
from ..deps import PodFetch, TranscriptLoader, current_user, get_db, get_podfetch, get_transcripts
from ..feeds import user_for_token
from ..llm import LLM, get_llm


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    autobrief.start(app)
    try:
        yield
    finally:
        autobrief.stop(app)


router = APIRouter(lifespan=_lifespan)


class SettingsInput(BaseModel):
    enabled: bool
    show_ids: list[str] = []
    daily_cap: Annotated[int, Field(strict=True)] = autobrief.DEFAULT_DAILY_CAP


@router.get("/companion/settings/worth-hearing")
def read_settings(request: Request, user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return {**autobrief.public_settings(autobrief.read_settings(db, user)),
            "login_blocks_auto": autobrief.login_blocks_auto(request.app)}


@router.put("/companion/settings/worth-hearing")
def save_settings(body: SettingsInput, request: Request, user: str = Depends(current_user),
                  db: sqlite3.Connection = Depends(get_db)):
    settings = autobrief.save_settings(db, user, enabled=body.enabled, show_ids=body.show_ids, daily_cap=body.daily_cap)
    return {**autobrief.public_settings(settings), "login_blocks_auto": autobrief.login_blocks_auto(request.app)}


@router.post("/companion/settings/worth-hearing/estimate")
def estimate_settings(body: SettingsInput, user: str = Depends(current_user), podfetch: PodFetch = Depends(get_podfetch),
                      transcripts: TranscriptLoader = Depends(get_transcripts), db: sqlite3.Connection = Depends(get_db),
                      llm: LLM | None = Depends(get_llm)):
    ctx = briefs.BriefContext(podfetch, transcripts, db, llm)
    return autobrief.estimate_settings(ctx, user, body.show_ids, body.daily_cap)


@router.get("/companion/worth-hearing")
def worth_hearing(request: Request, user: str = Depends(current_user), podfetch: PodFetch = Depends(get_podfetch),
                  db: sqlite3.Connection = Depends(get_db)):
    items = autobrief.worth_items(db, podfetch, user)
    return {"hear": [item for item in items if item["verdict"] == "HEAR"],
            "other": [item for item in items if item["verdict"] != "HEAR"],
            "login_blocks_auto": autobrief.login_blocks_auto(request.app)}


@router.get("/companion/feed/{token}/worth-hearing.xml")
def worth_hearing_feed(token: str, request: Request):
    settings = request.app.state.settings
    with briefs.connection(settings.database) as db:
        user = user_for_token(db, token)
        if user is None:
            raise HTTPException(404, "This feed link doesn't work anymore.")
        if autobrief.login_blocks_auto(request.app):
            body = autobrief.render_feed([], note=autobrief.LOGIN_BLOCKS_AUTO)
            return Response(content=body, media_type="application/rss+xml; charset=utf-8")
        podfetch = PodFetch(settings.podfetch_url, transport=settings.transport)
        items = autobrief.worth_items(db, podfetch, user)
    body = autobrief.render_feed([item for item in items if item["verdict"] == "HEAR"])
    return Response(content=body, media_type="application/rss+xml; charset=utf-8")
