"""Search inside episodes: ``GET /companion/search?q=`` over the transcript library.

PodFetch searches the transcripts it stores itself (``GET /api/v1/transcripts/search``). The
operator's older transcripts live only in the companion's library adapter (``PODCAST_LIBRARY``),
so this route searches those, and the UI merges both result sets per episode. Each hit is returned
under the PodFetch episode it belongs to, matched by the exact publisher media URL, the same rule
``transcripts.py`` uses to serve the transcript; an episode that doesn't match is only counted.

A passage matches when every search word starts a word in it, the way PodFetch's prefix search
works. A phrase split between two neighbouring segments still matches, as one hit. Times are
seconds from the start of the original episode audio. A library text file that is really SRT or
WebVTT keeps its times; a transcript without timing gives ``start: null``.

Response::

    {query, terms: [str], library: bool, unmatched: int,
     episodes: [{episode_id, id, podcast_id, title, duration, matches,
                 hits: [{segment_id, start, end, text}]}]}
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import PodFetch, get_podfetch
from ..engine import parse
from ..engine.select import STOP_TERMS
from ..library.library import LibraryError

if TYPE_CHECKING:
    from ..library.library import FilesystemLibrary
    from ..transcripts import TranscriptLibrary

router = APIRouter()

MAX_QUERY = 200        # characters
MAX_EPISODES = 30      # episodes in one response, best first
MAX_HITS = 10          # hits per episode; "matches" still counts all of them
PAGE = 75              # PodFetch returns 75 episodes per page (usecases/podcast_episode/mod.rs)
MAX_PAGES = 200
PODCAST_TTL = 300      # seconds to keep one show's media URL -> episode map
UNTIMED_CHUNK = 240    # characters per passage of a transcript without timing
KEEP = ("id", "episode_id", "podcast_id", "name", "total_time")

_WORD = re.compile(r"[\w+#.]+")   # "802.1q" and "c++" stay whole; "spanning-tree" is two words
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CREATE = threading.Lock()


def query_terms(query: str) -> list[str]:
    """The search words: lowercased, stop words dropped (kept when the query has nothing else)."""
    words = [word.strip(".") for word in _WORD.findall(query.lower())]
    words = list(dict.fromkeys(word for word in words if len(word) >= 2))
    return [word for word in words if word not in STOP_TERMS] or words


@dataclass
class Doc:
    stamp: tuple[Any, ...]
    audio: str
    feed: str
    duration: float
    passages: list[tuple[str | None, float | None, float | None, str]]  # segment id, start, end, text
    folded: str                                                         # every passage, lowercased


def _stat(path: Path | None) -> tuple[str, int, int] | None:
    try:
        info = path.stat() if path else None
    except OSError:
        return None
    return (str(path), info.st_mtime_ns, info.st_size) if info else None


def _chunks(text: str) -> list[str]:
    """Split an untimed transcript into passages of about UNTIMED_CHUNK characters, at sentence ends."""
    chunks: list[str] = []
    for sentence in _SENTENCE.split(" ".join(text.split())):
        if chunks and len(chunks[-1]) + len(sentence) < UNTIMED_CHUNK:
            chunks[-1] += " " + sentence
        elif sentence:
            chunks.append(sentence)
    return chunks


def _load(library: FilesystemLibrary, library_id: str,
          duration: float) -> list[tuple[str | None, float | None, float | None, str]]:
    try:
        doc = library.transcript(library_id, include_words=False)
    except (LibraryError, ValueError, OSError):
        return []  # the transcript route refuses it too (stale timing), so it isn't searchable
    passages = [(s.id, s.start, s.end, s.text) for s in doc.segments if s.text]
    if passages or not doc.text.strip():
        return passages
    try:  # a transcript file that is really SRT or WebVTT still has its times
        timed = parse(doc.text, duration=duration or None)
    except ValueError:
        timed = []
    if timed:
        return [(s.id, s.start, s.end, s.text) for s in timed if s.text]
    return [(None, None, None, chunk) for chunk in _chunks(doc.text)]


def _feed_key(url: Any) -> str:
    parts = urlsplit(str(url or "").strip())
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}?{parts.query}" if parts.netloc else ""


def _same_duration(expected: float, actual: Any) -> bool:
    """transcripts.py refuses a library transcript whose audio length changed by more than 2%."""
    try:
        actual = float(actual or 0)
    except (TypeError, ValueError):
        return True
    return not (expected and actual and abs(expected - actual) / expected > .02)


class LibraryIndex:
    """Library transcripts kept in memory, reread only when their files change."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.docs: dict[str, Doc] = {}
        self.podcasts: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}

    def refresh(self, source: TranscriptLibrary) -> list[Doc]:
        library = source.library
        if library is None:
            return []
        docs: dict[str, Doc] = {}
        for show_dir in library._show_dirs():
            try:
                manifest = library._read_json(show_dir / "_feed.json")
            except LibraryError:
                continue
            feed = str(manifest.get("feed") or "")
            for raw in manifest.get("episodes") or []:
                if not isinstance(raw, dict):
                    continue
                library_id, audio = library._episode_id(show_dir.name, raw), str(raw.get("audio") or "")
                # One library entry per media URL: the one the transcript route serves.
                if not audio or source.by_media_url.get(audio, ("",))[0] != library_id:
                    continue
                files = (library._timed_path(show_dir, raw), library._local_transcript_path(show_dir, raw))
                stamp = (feed, raw.get("duration"), *(_stat(path) for path in files))
                doc = self.docs.get(library_id)
                if doc is None or doc.stamp != stamp:
                    try:
                        duration = max(0.0, float(raw.get("duration") or 0))
                    except (TypeError, ValueError):
                        duration = 0.0
                    passages = _load(library, library_id, duration)
                    doc = Doc(stamp, audio, feed, duration, passages, "\n".join(p[3] for p in passages).lower())
                docs[library_id] = doc
        self.docs = docs
        return list(docs.values())

    def search(self, source: TranscriptLibrary, terms: list[str]) -> list[tuple[Doc, list[dict[str, Any]]]]:
        patterns = [re.compile(r"(?<!\w)" + re.escape(term), re.IGNORECASE) for term in terms]
        with self.lock:
            docs = self.refresh(source)
        found = []
        for doc in docs:
            if all(term in doc.folded for term in terms):
                hits = find_hits(doc.passages, patterns)
                if hits:
                    found.append((doc, hits))
        return found

    def episodes_by_url(self, podfetch: PodFetch, feeds: set[str]) -> dict[str, dict[str, Any]]:
        """Media URL -> PodFetch episode, for the shows whose feed URL is one of ``feeds``."""
        wanted = {_feed_key(feed) for feed in feeds} - {""}
        found: dict[str, dict[str, Any]] = {}
        if not wanted:
            return found
        now = time.monotonic()
        for podcast in podfetch.get("/api/v1/podcasts") or []:
            if not isinstance(podcast, dict) or _feed_key(podcast.get("rssfeed")) not in wanted:
                continue
            podcast_id = str(podcast.get("id"))
            cached = self.podcasts.get(podcast_id)
            if cached is None or now - cached[0] > PODCAST_TTL:
                cached = (now, _podcast_episodes(podfetch, podcast_id))
                self.podcasts[podcast_id] = cached
            found.update(cached[1])
        return found


def _podcast_episodes(podfetch: PodFetch, podcast_id: str) -> dict[str, dict[str, Any]]:
    """Every episode of one show, 75 per page, newest first (PodFetch pages by recording date)."""
    episodes: dict[str, dict[str, Any]] = {}
    cursor = None
    for _ in range(MAX_PAGES):
        page = podfetch.get(f"/api/v1/podcasts/{podcast_id}/episodes", optional=True,
                            params={"last_podcast_episode": cursor} if cursor else None) or []
        items = [item.get("podcastEpisode") for item in page if isinstance(item, dict)]
        items = [item for item in items if isinstance(item, dict)]
        for episode in items:
            if episode.get("url") and episode.get("episode_id"):
                episodes.setdefault(episode["url"], {key: episode.get(key) for key in KEEP})
        following = items[-1].get("date_of_recording") if items else None
        if len(page) < PAGE or not following or following == cursor:
            break
        cursor = following
    return episodes


def find_hits(passages: list[tuple[str | None, float | None, float | None, str]],
              patterns: list[re.Pattern[str]]) -> list[dict[str, Any]]:
    """Passages that hold every pattern, in time order; a phrase split over two passages is one hit."""
    full = (1 << len(patterns)) - 1
    masks = [sum(1 << n for n, pattern in enumerate(patterns) if pattern.search(text))
             for _, _, _, text in passages]
    hits, i = [], 0
    while i < len(passages):
        segment_id, start, end, text = passages[i]
        if masks[i] == full:
            hits.append({"segment_id": segment_id, "start": start, "end": end, "text": text})
            i += 1
        elif (i + 1 < len(passages) and masks[i] and masks[i + 1] and masks[i + 1] != full
              and masks[i] | masks[i + 1] == full):
            _, _, end, following = passages[i + 1]
            hits.append({"segment_id": segment_id, "start": start, "end": end, "text": f"{text} {following}"})
            i += 2
        else:
            i += 1
    return hits


def _index(request: Request) -> LibraryIndex:
    with _CREATE:
        index = getattr(request.app.state, "search_index", None)
        if index is None:
            index = request.app.state.search_index = LibraryIndex()
        return index


@router.get("/companion/search")
def search(request: Request, q: str = "", podfetch: PodFetch = Depends(get_podfetch)):
    query = " ".join(q.split())
    if len(query) > MAX_QUERY:
        raise HTTPException(422, f"Search for {MAX_QUERY} characters or fewer.")
    source: TranscriptLibrary = request.app.state.transcript_library
    terms = query_terms(query)
    result: dict[str, Any] = {"query": query, "terms": terms, "library": source.connected,
                              "unmatched": 0, "episodes": []}
    if not terms or not source.connected:
        return result
    index = _index(request)
    found = index.search(source, terms)
    if not found:
        return result
    by_url = index.episodes_by_url(podfetch, {doc.feed for doc, _ in found})
    episodes = []
    for doc, hits in found:
        episode = by_url.get(doc.audio)
        if not episode or not _same_duration(doc.duration, episode.get("total_time")):
            result["unmatched"] += 1
            continue
        title = str(episode.get("name") or "")
        episodes.append({"episode_id": episode["episode_id"], "id": episode.get("id"),
                         "podcast_id": episode.get("podcast_id"), "title": title,
                         "duration": episode.get("total_time") or doc.duration, "matches": len(hits),
                         "hits": hits[:MAX_HITS],
                         "_title_match": all(term in title.lower() for term in terms)})
    episodes.sort(key=lambda e: (not e["_title_match"], -e["matches"], e["title"].lower()))
    for episode in episodes:
        del episode["_title_match"]
    result["episodes"] = episodes[:MAX_EPISODES]
    return result
