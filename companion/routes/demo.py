"""The bundled demo pack: something to read and hear before the listener has an AI key,
their own shows, or a single download.

``demo/library/<show>/`` in the repo ships a manifest (``_feed.json``), Groq-transcribed
transcripts (``<key>.txt`` + ``<key>.timed.json``, the podcast-learn library format) and
precomputed briefs (``briefs.json``). None of that reaches the companion's container image
(only ``companion/`` does), so ``scripts/load-demo.sh`` copies the show's folder onto this
server's ``PODCAST_LIBRARY`` (the ``./data/companion/library`` bind mount) before calling the
two routes here:

GET  /companion/demo/feed/{show_id}.xml   an RSS 2.0 feed built from the copied manifest, for
                                          PodFetch's own subscribe API to fetch. Needs a login
                                          exactly like every other ``/companion/*`` route
                                          (companion/test_auth.py), so it only works while
                                          PodFetch itself runs without one - which is the state
                                          the demo pack is meant to be loaded in. Its enclosures
                                          are the publisher's own URLs (Hacker Public Radio) -
                                          nothing is proxied or re-hosted.
POST /companion/demo/import                after PodFetch has parsed that feed: refreshes this
                                          process's transcript index (the files may have just
                                          landed on disk) and stores each episode's precomputed
                                          brief, matched to PodFetch's episode_id by its exact
                                          enclosure URL. Safe to call more than once, and before
                                          every episode has been picked up yet (it waits a little).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import brief as briefs
from ..deps import PodFetch, Settings, current_user, get_db, get_podfetch, get_settings
from ..transcripts import TranscriptLibrary

router = APIRouter()

DEFAULT_SHOW = "hpr-bash-tips"
RESOLVE_TRIES = 8
RESOLVE_WAIT_SECONDS = 1.5
PAGE_SIZE = 75
SHOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validated_show_id(show_id: str) -> str:
    """A plain id only, so it can never carry ``..`` or a path separator into a filesystem path."""
    if not SHOW_ID_RE.match(show_id):
        raise HTTPException(404, f'"{show_id}" is not a valid demo show id.')
    return show_id


def _unreadable(show_id: str) -> str:
    return (f'The demo library for "{show_id}" is not readable by the companion. Re-run '
           "scripts/load-demo.sh; on a Docker install it fixes the file ownership too.")


def _manifest(library_root: Path, show_id: str) -> dict[str, Any]:
    show_id = _validated_show_id(show_id)
    path = Path(library_root) / show_id / "_feed.json"
    try:
        exists = path.is_file()
    except OSError as exc:
        raise HTTPException(409, _unreadable(show_id)) from exc
    if not exists:
        raise HTTPException(404, f'No demo library at "{show_id}". Run scripts/load-demo.sh first.')
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(409, _unreadable(show_id)) from exc
    except ValueError as exc:
        raise HTTPException(500, f"The demo manifest for {show_id!r} is not valid JSON.") from exc


def _rfc2822(published: str) -> str:
    try:
        when = datetime.fromisoformat(str(published)).replace(tzinfo=timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)
    return when.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _hms(seconds: Any) -> str:
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "0:00"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def build_feed_xml(manifest: Mapping[str, Any], self_url: str) -> str:
    """A minimal, valid RSS 2.0 + itunes feed for PodFetch to subscribe to. Every enclosure is
    the publisher's own URL from the manifest; nothing here is proxied or re-hosted."""
    title = escape(str(manifest.get("title") or "Demo podcast"))
    items = []
    for episode in manifest.get("episodes") or []:
        if not isinstance(episode, Mapping) or not episode.get("audio"):
            continue
        guid = escape(str(episode.get("guid") or episode.get("key") or episode["audio"]))
        etitle = escape(str(episode.get("title") or "Episode"))
        summary = escape(str(episode.get("summary") or ""))
        audio = escape(str(episode["audio"]))
        length = int(episode.get("byte_length") or 0)
        items.append(f"""    <item>
      <title>{etitle}</title>
      <guid isPermaLink="false">{guid}</guid>
      <pubDate>{_rfc2822(episode.get("published"))}</pubDate>
      <description>{summary}</description>
      <enclosure url="{audio}" length="{length}" type="audio/mpeg"/>
      <itunes:duration>{_hms(episode.get("duration"))}</itunes:duration>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{title}</title>
    <link>{escape(self_url)}</link>
    <language>en-us</language>
    <description>{title} - bundled demo pack. See demo/LICENSE-CONTENT.md for source and licence.</description>
{chr(10).join(items)}
  </channel>
</rss>"""


@router.get("/companion/demo/feed/{show_id}.xml")
def demo_feed(show_id: str, request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Every /companion/* route needs a login when PodFetch has one (companion/test_auth.py), so
    this one only works while PodFetch runs without login (require_login -> "default"; the demo
    pack is meant to be loaded before you've set anything up). Unlike /companion/feed/*, PodFetch
    itself is the caller here, not a phone app with a token, so it gets no separate exemption."""
    if not settings.library_root:
        raise HTTPException(404, "PODCAST_LIBRARY is not set, so there is no demo library to serve.")
    manifest = _manifest(settings.library_root, show_id)
    return Response(build_feed_xml(manifest, str(request.url)), media_type="application/rss+xml")


def _find_podcast(podfetch: PodFetch, title: str) -> str | None:
    """The id of the PodFetch show whose name is exactly ``title``, or None."""
    try:
        podcasts = podfetch.get("/api/v1/podcasts", optional=True)
    except HTTPException:
        return None
    for item in podcasts if isinstance(podcasts, list) else ():
        if isinstance(item, Mapping) and str(item.get("name") or "").strip() == title.strip():
            return str(item.get("id"))
    return None


def _episodes_of(podfetch: PodFetch, podcast_id: str) -> list[dict[str, Any]]:
    """This show's PodFetch episodes (paginated like brief.show_episodes), url -> episode kept."""
    items: list[dict[str, Any]] = []
    last = None
    for _ in range(20):
        try:
            page = podfetch.get(f"/api/v1/podcasts/{podcast_id}/episodes", optional=True,
                                params={"last_podcast_episode": last} if last else None)
        except HTTPException:
            break
        if not isinstance(page, list) or not page:
            break
        for entry in page:
            episode = entry.get("podcastEpisode") if isinstance(entry, Mapping) else None
            if isinstance(episode, Mapping) and episode.get("url") and episode.get("episode_id"):
                items.append(episode)
        last_entry = page[-1] if isinstance(page[-1], Mapping) else {}
        last = (last_entry.get("podcastEpisode") or {}).get("date_of_recording")
        if len(page) < PAGE_SIZE or not last:
            break
    return items


def _resolve(podfetch: PodFetch, title: str, wanted_urls: Mapping[str, str]) -> dict[str, str]:
    """key -> PodFetch episode_id, for every manifest episode PodFetch has parsed so far, waiting
    a little for a feed it only just subscribed to."""
    resolved: dict[str, str] = {}
    for attempt in range(RESOLVE_TRIES):
        podcast_id = _find_podcast(podfetch, title)
        if podcast_id:
            for episode in _episodes_of(podfetch, podcast_id):
                key = wanted_urls.get(episode["url"])
                if key:
                    resolved[key] = str(episode["episode_id"])
        if len(resolved) >= len(wanted_urls) or attempt == RESOLVE_TRIES - 1:
            break
        time.sleep(RESOLVE_WAIT_SECONDS)
    return resolved


def _transcript_hash(library: TranscriptLibrary, show_id: str, key: str) -> str:
    """The same hash companion/brief.py would compute for this episode's library transcript."""
    if not library.library:
        raise ValueError("no PODCAST_LIBRARY is configured")
    doc = library.library.transcript(f"{show_id}--{key}", include_words=False)
    public = [segment.model_dump() for segment in doc.segments]
    return briefs.transcript_hash(briefs.timed_segments({"segments": public}))


@router.post("/companion/demo/import")
def demo_import(request: Request, show_id: str = DEFAULT_SHOW, podfetch: PodFetch = Depends(get_podfetch),
                db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings),
                user: str = Depends(current_user)):
    if not settings.library_root:
        raise HTTPException(409, "PODCAST_LIBRARY is not set, so there is nowhere to read the demo pack from.")
    manifest = _manifest(settings.library_root, show_id)  # also validates show_id
    briefs_path = Path(settings.library_root) / show_id / "briefs.json"
    precomputed: dict[str, Any] = {}
    try:
        if briefs_path.is_file():
            precomputed = json.loads(briefs_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(409, _unreadable(show_id)) from exc
    except ValueError:
        precomputed = {}

    # The files may have just been copied onto PODCAST_LIBRARY: this process's index (built once,
    # at startup) would not see them yet.
    try:
        library = TranscriptLibrary(settings.library_root)
    except OSError as exc:
        raise HTTPException(409, _unreadable(show_id)) from exc
    request.app.state.transcript_library = library

    by_key = {str(episode.get("key")): episode for episode in manifest.get("episodes") or []
             if isinstance(episode, Mapping) and episode.get("key")}
    wanted_urls = {str(episode["audio"]): key for key, episode in by_key.items() if episode.get("audio")}
    resolved = _resolve(podfetch, str(manifest.get("title") or ""), wanted_urls)

    imported, skipped = [], []
    for key in by_key:
        episode_id = resolved.get(key)
        if not episode_id:
            skipped.append({"key": key, "reason": "PodFetch hasn't parsed this episode from the feed yet"})
            continue
        body = precomputed.get(key)
        if not isinstance(body, Mapping) or not isinstance(body.get("body"), Mapping):
            skipped.append({"key": key, "reason": "no precomputed brief shipped for this episode"})
            continue
        try:
            transcript_hash = _transcript_hash(library, show_id, key)
        except (ValueError, OSError) as exc:
            skipped.append({"key": key, "reason": f"couldn't read its transcript: {exc}"})
            continue
        # Reuses brief.py's own upsert so the stored row always matches its table's real shape.
        briefs._store(db, user, episode_id, transcript_hash, str(body.get("model") or "demo"),
                     "ready", dict(body["body"]), None)
        imported.append({"key": key, "episode_id": episode_id})
    return {"show": manifest.get("title"), "imported": imported, "skipped": skipped}
