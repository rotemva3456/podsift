"""Private podcast-app feeds: your own phone podcast app, upgraded.

PodFetch already builds a per-show RSS feed with enclosures and transcript tags
(``crates/podfetch-web/src/controllers/websocket_controller.rs``, ``/rss/apiKey/{apiKey}/{id}``,
which authenticates by the key in the path -- no login session, exactly what an external podcast
app needs). This module fetches that feed on the server with the caller's own PodFetch API key and
adds one thing PodFetch doesn't have: a ``<podcast:chapters>`` link per episode, built from the
cached AI brief plus sponsor reads found in the transcript (``engine.ads``), in the
Podcasting 2.0 JSON chapters format. It also serves a second, simpler feed of the cuts you've
exported.

Two families of route (``routes/feed.py``):
- Settings, under a normal login (``/companion/settings/feed*``, like Settings -> AI): create or
  replace the feed token. That also captures the caller's PodFetch API key
  (``GET /api/v1/users/me`` while they are logged in) and stores it write-only, sealed the same way
  the AI key is sealed -- an HMAC stream cipher keyed by a secret file of its own, so it never sits
  in the data folder as plain text. (``companion/providers/store.py`` says "features never import
  this package"; this module keeps its own copy of the same small primitive rather than reach past
  that line, under its own domain-separated labels and its own secret file.) The plaintext token is
  returned once, at creation; only its hash is kept, so it can never be read back, only replaced.
- Feed, under ``/companion/feed/<token>/...``: no login at all -- ``auth.is_public`` already skips
  the normal login for that whole prefix. Every route here checks the token itself with
  ``user_for_token``, in constant time, and answers 404 for a bad one, the same answer as "this
  doesn't exist", so a guess can't be told from a wrong guess.

Building a show's feed makes exactly one authenticated PodFetch call: the RSS itself, which
authenticates by the apiKey already in its path (PodFetch's ``get_private_api`` login gate, in
``startup.rs``, deliberately excludes that route and the apiKey transcript-file route, precisely so
a feed reader can use them with no session -- general routes such as episode or chapter lookups do
NOT get that exemption). Everything else this module adds comes from that same response: the AI
part is our own cached brief (no network call at all); the sponsor spans come from re-reading the
transcript file at the URL PodFetch just gave us in that RSS (also apiKey-authenticated), never
from PodFetch's general, login-gated API. So the feed works the same whether or not PodFetch's own
login is turned on.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
from fastapi import HTTPException, Request

from . import cuts as cutstore
from . import engine
from . import sealed
from .brief import cached_briefs
from .db import register_migration
from .deps import Settings

log = logging.getLogger(__name__)

LABEL = "feed"          # this feature's own sealed.py label -- its own secret file, feed.secret
TOKEN_BYTES = 32
MIN_TOKEN_LEN, MAX_TOKEN_LEN = 16, 128
CUTS_FEED_LIMIT = 200

NOT_FOUND = "This feed link doesn't work anymore. Make a new one in Settings → Podcast app feeds."
NO_KEY = "Your PodFetch account has no API key yet. Open PodFetch's own account settings once, then try again."
UNAVAILABLE = "The podcast library is unavailable. Please try again."

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
RSS_NAMESPACES = {"podcast": PODCAST_NS, "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
                  "content": "http://purl.org/rss/1.0/modules/content/", "atom": "http://www.w3.org/2005/Atom"}
CHAPTERS_TYPE = "application/json+chapters"

register_migration("feeds", 1, """
CREATE TABLE IF NOT EXISTS feed_tokens (
    user_id TEXT PRIMARY KEY NOT NULL DEFAULT 'default',
    token_hash TEXT NOT NULL,
    podfetch_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feed_ad_spans (
    user_id TEXT NOT NULL DEFAULT 'default',
    episode_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    spans TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, episode_id))
""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_folder(database: Path | str) -> Path:
    return Path(database).expanduser().parent


def origin(base_url: str) -> str:
    """``scheme://host:port``, so a sealed key is only ever used against the address it was
    captured for (a copied data folder pointed at a different PodFetch can't reuse an old key)."""
    url = httpx.URL(base_url)
    return f"{url.scheme}://{url.host}:{url.port or (443 if url.scheme == 'https' else 80)}".lower()


# ── the token: 32 random bytes, stored hashed, never recoverable ───────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


@dataclass(frozen=True)
class FeedRow:
    user_id: str
    created_at: str
    updated_at: str


def status(db: sqlite3.Connection, user: str) -> FeedRow | None:
    row = db.execute("SELECT user_id, created_at, updated_at FROM feed_tokens WHERE user_id=?", (user,)).fetchone()
    return FeedRow(row["user_id"], row["created_at"], row["updated_at"]) if row else None


def create_token(db: sqlite3.Connection, folder: Path, user: str, podfetch_url: str, api_key: str | None) -> str:
    """A fresh token for ``user``, replacing any old one -- the old link stops working at once.
    Returns the plaintext token. Only its hash is kept, so this is the only time it exists outside
    the caller's own copy-paste.

    ``api_key`` is None when PodFetch has no login at all: there is nothing to seal, and
    ``show_feed_xml`` then fetches PodFetch's RSS with no key, which is all PodFetch itself
    requires in that mode (``routes/feed.py`` decides this from ``auth.checks_logins``, never a
    setting of our own)."""
    sealed_key = ""
    if api_key:
        secret = sealed.secret(folder, LABEL, create=True)
        if secret is None:
            raise HTTPException(500, f"The feed link can't be saved: "
                                     f"{folder / sealed.secret_file_name(LABEL)} can't be written.")
        sealed_key = sealed.seal(secret, LABEL, api_key, origin(podfetch_url))
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    db.execute("""INSERT INTO feed_tokens (user_id, token_hash, podfetch_key, created_at, updated_at)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT (user_id) DO UPDATE SET
                    token_hash=excluded.token_hash, podfetch_key=excluded.podfetch_key,
                    updated_at=excluded.updated_at""",
               (user, _token_hash(token), sealed_key, now, now))
    return token


def user_for_token(db: sqlite3.Connection, token: str) -> str | None:
    """The feed token's owner, or None for a bad or replaced token.

    Every ``/companion/feed/<token>/...`` route calls this itself -- ``auth.is_public`` already
    skips the normal login for that whole path prefix. It opens nothing but the feed paths: never
    treat its answer as a login for anything else. Compares in constant time (one row at a time,
    self-hosted installs have a handful at most) so a wrong guess can't be timed against a right
    one via a database index."""
    if not token or not (MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN):
        return None
    candidate = _token_hash(token).encode()
    found = None
    for row in db.execute("SELECT user_id, token_hash FROM feed_tokens"):
        if hmac.compare_digest(row["token_hash"].encode(), candidate):
            found = row["user_id"]
    return found


def podfetch_key_for(db: sqlite3.Connection, folder: Path, user: str, podfetch_url: str) -> str | None:
    """The user's own PodFetch API key, unsealed -- only ever used server-side, and only against
    the apiKey-authenticated routes (the RSS feed, the transcript file); never sent to the caller,
    never logged. None both when no key was ever needed (the link was made while PodFetch had no
    login) and when a stored one can no longer be opened -- callers that need to tell those apart
    already know which one applies, from ``auth.checks_logins`` at the point they ask."""
    row = db.execute("SELECT podfetch_key FROM feed_tokens WHERE user_id=?", (user,)).fetchone()
    if row is None or not row["podfetch_key"]:
        return None
    secret = sealed.secret(folder, LABEL, create=False)
    opened = sealed.unseal(secret, LABEL, row["podfetch_key"]) if secret else None
    if opened is None:
        log.warning("A feed's saved PodFetch key can't be opened (was %s changed or lost?).",
                   sealed.secret_file_name(LABEL))
        return None
    key, key_origin = opened
    if key_origin != origin(podfetch_url):
        log.warning("A feed's saved PodFetch key was sealed for a different PodFetch address; ignoring it.")
        return None
    return key


# ── the public address: mirrors PodFetch's own resolve_server_url_from_headers ─────────────────

def _header(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    return value.split(",")[0].strip() if value else None


def forwarded_host_headers(request: Request) -> dict[str, str]:
    """The headers that decide the address embedded in a feed's URLs: whatever the caller's own
    request just arrived with, forwarded on to PodFetch's RSS call so it resolves to the same
    address a podcast app would get by asking PodFetch directly (``url_rewriting.rs``)."""
    headers = {}
    for name in ("host", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-scheme", "x-forwarded-prefix"):
        value = _header(request, name)
        if value:
            headers[name] = value
    return headers


def public_base_url(request: Request) -> str:
    """The address this request arrived on, with no trailing slash: the same algorithm as
    PodFetch's own ``resolve_server_url_from_headers`` (``crates/podfetch-web/src/url_rewriting.rs``),
    so our own links land on the same address as PodFetch's, through whatever reverse proxy fronts
    both (``docker/Caddyfile``)."""
    host = _header(request, "x-forwarded-host") or _header(request, "host")
    if not host:
        return str(request.base_url).rstrip("/")
    proto = _header(request, "x-forwarded-proto") or _header(request, "x-forwarded-scheme") or request.url.scheme
    prefix = (_header(request, "x-forwarded-prefix") or "").strip("/")
    base = f"{proto}://{host}"
    return f"{base}/{prefix}" if prefix else base


# ── the show feed: PodFetch's own RSS, with a <podcast:chapters> link added per episode ─────────

def _rss_client(settings: Settings, headers: Mapping[str, str]) -> httpx.Client:
    return httpx.Client(base_url=settings.podfetch_url, headers=dict(headers), transport=settings.transport, timeout=20)


def _internal_get(settings: Settings, url: str) -> httpx.Response | None:
    """Re-issue a URL PodFetch just handed us against our own known-good address, not whatever
    host happened to be embedded in it (a public hostname the container may not be able to route
    to). The apiKey, if any, is already in the path, so no extra headers are needed."""
    parsed = urlsplit(url)
    path_and_query = parsed.path + (("?" + parsed.query) if parsed.query else "")
    try:
        with httpx.Client(base_url=settings.podfetch_url, transport=settings.transport, timeout=20) as client:
            return client.get(path_and_query)
    except httpx.HTTPError:
        return None


def _cached_ad_spans(db: sqlite3.Connection, user: str, episode_id: str, source_url: str) -> list[dict[str, Any]] | None:
    row = db.execute("SELECT spans FROM feed_ad_spans WHERE user_id=? AND episode_id=? AND source_url=?",
                     (user, episode_id, source_url)).fetchone()
    return json.loads(row["spans"]) if row else None


def _store_ad_spans(db: sqlite3.Connection, user: str, episode_id: str, source_url: str, spans: list[dict[str, Any]]) -> None:
    db.execute("""INSERT INTO feed_ad_spans (user_id, episode_id, source_url, spans, updated_at) VALUES (?,?,?,?,?)
                  ON CONFLICT (user_id, episode_id) DO UPDATE SET
                    source_url=excluded.source_url, spans=excluded.spans, updated_at=excluded.updated_at""",
               (user, episode_id, source_url, json.dumps(spans), _now()))


def _ensure_ad_spans(db: sqlite3.Connection, settings: Settings, user: str, episode_id: str,
                     source_url: str | None, mime_type: str) -> None:
    """Sponsor spans for one episode, from the transcript PodFetch's own RSS just pointed at.
    Cached by that URL, so an unchanged transcript costs no extra fetch on the next poll.

    A fetch that fails (network error, or PodFetch answers anything but 200) is left uncached, so
    the next poll tries again -- a transient hiccup must never freeze in a permanent "no sponsors"
    answer. A transcript that fetches but won't parse *is* cached as no spans: re-fetching the same
    bytes on every poll would not fix that, so there is no point trying again until the URL changes."""
    if not source_url or _cached_ad_spans(db, user, episode_id, source_url) is not None:
        return
    response = _internal_get(settings, source_url)
    if response is None or response.status_code != 200:
        return
    try:
        segments = engine.parse(response.content, content_type=mime_type)
        spans = cutstore.sponsor_spans(segments)
    except (ValueError, KeyError, TypeError):
        spans = []
    _store_ad_spans(db, user, episode_id, source_url, spans)


def show_feed_xml(db: sqlite3.Connection, settings: Settings, request: Request, token: str,
                  podcast_id: str, user: str, key: str | None) -> str:
    """PodFetch's own show RSS, with one ``<podcast:chapters>`` link added to every item.

    ``key`` is the caller's PodFetch API key when PodFetch has a login on (PodFetch then checks it
    for this exact route) -- pass None when PodFetch has no login at all, which serves the same
    RSS with no key needed at all: the apiKey check in PodFetch's own RSS controller only runs
    when BASIC_AUTH or OIDC is configured (``crates/podfetch-web/src/controllers/
    websocket_controller.rs``, both the plain and the apiKey-in-path route)."""
    path = f"/rss/apiKey/{key}/{podcast_id}" if key else f"/rss/{podcast_id}"
    try:
        with _rss_client(settings, forwarded_host_headers(request)) as client:
            response = client.get(path)
    except httpx.HTTPError:
        raise HTTPException(503, UNAVAILABLE) from None
    if response.status_code == 404:
        raise HTTPException(404, "This show isn't in your library.")
    if response.status_code in (401, 403):
        raise HTTPException(404, NOT_FOUND)   # the saved key stopped working; don't say why to a feed reader
    if response.status_code >= 400:
        raise HTTPException(503, UNAVAILABLE)
    for prefix, uri in RSS_NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        raise HTTPException(503, UNAVAILABLE) from None
    channel = root.find("channel")
    if channel is None:
        raise HTTPException(503, UNAVAILABLE)
    base = public_base_url(request)
    for item in channel.findall("item"):
        guid = item.find("guid")
        episode_id = (guid.text or "").strip() if guid is not None else ""
        if not episode_id:
            continue
        transcript = item.find(f"{{{PODCAST_NS}}}transcript")
        if transcript is not None and transcript.get("url"):
            _ensure_ad_spans(db, settings, user, episode_id, transcript.get("url"), transcript.get("type") or "")
        chapters_el = ET.SubElement(item, f"{{{PODCAST_NS}}}chapters")
        chapters_el.set("url", f"{base}/companion/feed/{token}/chapters/{episode_id}.json")
        chapters_el.set("type", CHAPTERS_TYPE)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def episode_chapters(db: sqlite3.Connection, user: str, episode_id: str) -> dict[str, Any]:
    """Podcasting 2.0 JSON chapters for one episode: the cached brief's chapters (no AI call),
    plus every sponsor read found in the transcript, titled "Sponsor" so an app that can skip
    chapters can skip them. Empty when neither exists yet -- still a valid, empty document."""
    rows = cached_briefs(db, user, [episode_id])
    ai_chapters = rows[0]["chapters"] if rows else []
    spans_row = db.execute("SELECT spans FROM feed_ad_spans WHERE user_id=? AND episode_id=?",
                           (user, episode_id)).fetchone()
    ad_spans = json.loads(spans_row["spans"]) if spans_row else []
    chapters: list[dict[str, Any]] = []
    for chapter in ai_chapters:
        start = chapter.get("start")
        if isinstance(start, (int, float)) and chapter.get("title"):
            chapters.append({"startTime": round(float(start), 2), "title": str(chapter["title"])})
    for span in ad_spans:
        chapters.append({"startTime": round(float(span["start"]), 2), "title": "Sponsor",
                         "endTime": round(float(span["end"]), 2)})
    chapters.sort(key=lambda c: c["startTime"])
    return {"version": "1.2.0", "chapters": chapters}


# ── the cuts feed: everything you've exported, as one simple RSS ────────────────────────────────

def _cut_description(index: list[Mapping[str, Any]]) -> str:
    lines = [f"{engine.hms(item['cut_start'])}–{engine.hms(item['cut_end'])} — "
             f"{item.get('title') or 'Untitled'} (from {engine.hms(item['source_start'])})" for item in index]
    return "\n".join(lines) or "A cut of your own library."


def cuts_feed_xml(db: sqlite3.Connection, request: Request, token: str, user: str) -> str:
    for prefix, uri in RSS_NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    base = public_base_url(request)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "My cuts"
    ET.SubElement(channel, "link").text = base
    ET.SubElement(channel, "description").text = "MP3s cut from your own podcast library."
    ET.SubElement(channel, "language").text = "en"
    rows = db.execute("SELECT id, duration, size_bytes, data, created_at FROM cut_files WHERE user_id=? "
                      "ORDER BY created_at DESC LIMIT ?", (user, CUTS_FEED_LIMIT)).fetchall()
    for row in rows:
        data = json.loads(row["data"])
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = str(data.get("title") or "Cut")
        ET.SubElement(item, "description").text = _cut_description(data.get("index") or [])
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = row["id"]
        try:
            ET.SubElement(item, "pubDate").text = format_datetime(datetime.fromisoformat(row["created_at"]))
        except ValueError:
            pass
        ET.SubElement(item, "enclosure", {"url": f"{base}/companion/feed/{token}/cuts/{row['id']}.mp3",
                                          "length": str(row["size_bytes"]), "type": "audio/mpeg"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")
