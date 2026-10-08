"""Private podcast-app feeds (``companion/feeds.py``).

Settings, under the normal login:
    GET  /companion/settings/feed          {configured, created_at}
    POST /companion/settings/feed/token    make or replace the token; returns it once, with the URLs

Feed, with no login at all (``auth.is_public`` already skips it for this whole prefix; every route
below checks the token itself and answers 404 for a bad one):
    GET /companion/feed/{token}/shows/{podcast_id}.xml       PodFetch's own show RSS, chapters added
    GET /companion/feed/{token}/chapters/{episode_id}.json   Podcasting 2.0 JSON chapters
    GET /companion/feed/{token}/cuts.xml                     every cut you've exported
    GET /companion/feed/{token}/cuts/{cut_id}.mp3            the cut's MP3 (Range works)
"""
from __future__ import annotations

import json
import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .. import auth
from .. import cuts as cutstore
from .. import feeds
from ..deps import PodFetch, Settings, current_user, get_db, get_podfetch, get_settings
from .cuts import _cut as load_cut_row

router = APIRouter()


@router.get("/companion/settings/feed")
def feed_status(user: str = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = feeds.status(db, user)
    return {"configured": row is not None, "created_at": row.created_at if row else None}


@router.post("/companion/settings/feed/token")
def make_feed_token(request: Request, db: sqlite3.Connection = Depends(get_db), user: str = Depends(current_user),
                    podfetch: PodFetch = Depends(get_podfetch), settings: Settings = Depends(get_settings)):
    api_key = None
    if auth.authenticator(request.app).checks_logins():
        # Only a PodFetch with a login needs (or lets us capture) a personal API key: its RSS
        # apiKey check itself only runs in that mode (feeds.show_feed_xml's docstring).
        me = podfetch.get("/api/v1/users/me", optional=True)
        api_key = me.get("apiKey") if isinstance(me, dict) else None
        if not api_key:
            raise HTTPException(409, feeds.NO_KEY)
    token = feeds.create_token(db, feeds.data_folder(settings.database), user, settings.podfetch_url, api_key)
    base = feeds.public_base_url(request)
    return {"token": token,
            "shows_url_template": f"{base}/companion/feed/{token}/shows/{{podcast_id}}.xml",
            "cuts_url": f"{base}/companion/feed/{token}/cuts.xml"}


def _feed_user(db: sqlite3.Connection, token: str) -> str:
    user = feeds.user_for_token(db, token)
    if user is None:
        raise HTTPException(404, feeds.NOT_FOUND)
    return user


@router.get("/companion/feed/{token}/shows/{podcast_id}.xml")
def show_feed(token: str, podcast_id: UUID, request: Request, db: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    user = _feed_user(db, token)
    key = None
    if auth.authenticator(request.app).checks_logins():
        key = feeds.podfetch_key_for(db, feeds.data_folder(settings.database), user, settings.podfetch_url)
        if key is None:
            # Login is on now but this token has no key -- either it predates login being turned
            # on, or the saved one stopped opening. Either way, a new link captures a fresh one.
            raise HTTPException(404, feeds.NOT_FOUND)
    xml = feeds.show_feed_xml(db, settings, request, token, str(podcast_id), user, key)
    return Response(xml, media_type="application/rss+xml")


@router.get("/companion/feed/{token}/chapters/{episode_id}.json")
def episode_chapters(token: str, episode_id: UUID, db: sqlite3.Connection = Depends(get_db)):
    user = _feed_user(db, token)
    return feeds.episode_chapters(db, user, str(episode_id))


@router.get("/companion/feed/{token}/cuts.xml")
def cuts_feed(token: str, request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = _feed_user(db, token)
    return Response(feeds.cuts_feed_xml(db, request, token, user), media_type="application/rss+xml")


@router.api_route("/companion/feed/{token}/cuts/{cut_id}.mp3", methods=["GET", "HEAD"])
def cut_audio(token: str, cut_id: UUID, db: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    user = _feed_user(db, token)
    row = load_cut_row(db, cut_id, user)   # the same lookup and 404 message as the cuts route
    folders = cutstore.get_folders(settings)
    path = folders.cuts / f"{cut_id}.mp3"
    if not path.is_file():
        raise HTTPException(404, "This cut's MP3 is missing. Export the plan again.")
    title = json.loads(row["data"]).get("title") or "cut"
    return FileResponse(path, media_type="audio/mpeg", filename=cutstore.download_name(title),
                        content_disposition_type="inline")
