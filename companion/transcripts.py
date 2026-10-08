"""Where an episode's transcript comes from, picked so that its timing fits the audio file.

Order, first match wins:
1. PodFetch's own Whisper transcript of the downloaded file (origin ``generated``). PodFetch
   itself serves a feed transcript first even when it has made this one, so we list them.
2. The transcript library (``PODCAST_LIBRARY``), matched by the exact publisher media URL
   (origin ``library``).
3. PodFetch's preferred transcript, usually the publisher's (origin ``feed``).

Output (the Transcript JSON):
    {episode_id, source, origin, timed, text, segments: [{id, start, end|None, text}]}
``origin`` is None when there is no transcript. Library transcripts also carry
``media_verified``. Times are seconds from the start of the audio the transcript was made from;
cutting checks them against the downloaded file with ``load_timing`` and ``engine.align``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

import httpx
from fastapi import HTTPException

from . import engine
from .library.library import FilesystemLibrary, LibraryError

if TYPE_CHECKING:
    from .deps import PodFetch

SOURCES = {"generated": "Made from your audio file", "library": "Your transcript library",
           "feed": "Publisher transcript"}
FILE_MAX_BYTES = 20 * 1024 * 1024


class Segment(TypedDict):
    id: str
    start: float
    end: float | None
    text: str


class Transcript(TypedDict, total=False):
    episode_id: str
    source: str | None
    origin: str | None
    timed: bool
    media_verified: bool
    text: str
    segments: list[Segment]
    digest: str


class TranscriptLibrary:
    """The optional on-disk library (``PODCAST_LIBRARY``), indexed by exact publisher media URL."""

    def __init__(self, root: Path | None):
        self.library = FilesystemLibrary(root) if root else None
        self.by_media_url: dict[str, tuple[str, dict[str, Any]]] = {}
        if not self.library:
            return
        for manifest in self.library.root.glob("*/_feed.json"):
            try:
                data = json.loads(manifest.read_text())
                for episode in data.get("episodes", []):
                    self.by_media_url[episode.get("audio")] = (
                        self.library._episode_id(manifest.parent.name, episode), episode)
            except (ValueError, OSError):
                continue

    @property
    def connected(self) -> bool:
        return bool(self.library)


@dataclass
class Timing:
    """A transcript plus what cutting needs to check it against the downloaded file.

    segments   parsed with engine.transcript, with word times when the source has them, and
               never clamped to a duration, so a transcript that runs past the file shows
    origin     "generated", "library" or "feed"; None without a transcript
    made_from  library transcripts: the identity (or length) of the audio they were made from
    """
    transcript: Transcript
    segments: list[engine.Segment] = field(default_factory=list)
    origin: str | None = None
    made_from: dict[str, Any] | None = None

    @property
    def digest(self) -> str:
        """Changes whenever a segment's id, times or text change."""
        digest = hashlib.sha256()
        if not self.segments:
            digest.update(self.transcript.get("text", "").encode())
        for seg in self.segments:
            digest.update(f"{seg.id}\x1f{seg.start:.3f}\x1f{seg.end:.3f}\x1f{seg.text}\x1e".encode())
        return digest.hexdigest()[:32]


def episode_transcript(episode_id: UUID | str, episode: dict[str, Any], podfetch: PodFetch,
                       library: TranscriptLibrary) -> Transcript:
    timing = load_timing(episode_id, episode, podfetch, library)
    return {**timing.transcript, "digest": timing.digest}


def load_timing(episode_id: UUID | str, episode: dict[str, Any], podfetch: PodFetch,
                library: TranscriptLibrary) -> Timing:
    return (_generated(episode_id, episode, podfetch)
            or _from_library(episode_id, episode, library)
            or _preferred(episode_id, episode, podfetch))


def _parsed(episode_id: UUID | str, origin: str, segments: list[engine.Segment]) -> Timing:
    public: list[Segment] = [{"id": s.id, "start": s.start, "end": s.end, "text": s.text} for s in segments]
    return Timing({"episode_id": str(episode_id), "source": SOURCES[origin], "origin": origin,
                   "timed": bool(public), "text": engine.plain_text(segments), "segments": public},
                  segments, origin)


def _generated(episode_id: UUID | str, episode: dict[str, Any], podfetch: PodFetch) -> Timing | None:
    """PodFetch's Whisper transcript of the downloaded file, once it is parsed (a queued or
    running job is listed as a generated entry too)."""
    try:
        listed = podfetch.get(f'/api/v1/podcasts/episodes/{episode["id"]}/transcripts', optional=True)
    except HTTPException:
        return None                  # the library and the preferred transcript can still answer
    for item in listed if isinstance(listed, list) else ():
        if not isinstance(item, dict) or item.get("source") != "generated" or item.get("status") != "parsed":
            continue
        try:
            found = _transcript_file(podfetch, episode["id"], UUID(str(item.get("id"))))
            segments = engine.transcript.parse(found[0], content_type=found[1], group=False) if found else []
        except (ValueError, HTTPException):     # a bad id, an unreadable file, PodFetch trouble
            continue
        if segments:
            return _parsed(episode_id, "generated", segments)
    return None


def _transcript_file(podfetch: PodFetch, podfetch_id: str, transcript_id: UUID) -> tuple[bytes, str] | None:
    """The archived transcript file as (body, content type); None when PodFetch has none."""
    path = f"/api/v1/podcasts/episodes/{podfetch_id}/transcripts/{transcript_id}/file"
    try:
        with httpx.Client(base_url=podfetch.base_url, headers=podfetch.headers, timeout=podfetch.timeout,
                          transport=podfetch.transport, follow_redirects=False) as client:
            with client.stream("GET", path) as response:
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > FILE_MAX_BYTES:
                        return None
                return bytes(body), response.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        raise HTTPException(503, "The podcast library is unavailable. Please try again.") from exc


def _from_library(episode_id: UUID | str, episode: dict[str, Any], library: TranscriptLibrary) -> Timing | None:
    # Match the exact publisher media URL, never a fuzzy title or timestamp.
    match = library.by_media_url.get(episode["url"])
    if not (library.library and match):
        return None
    library_id, original = match
    expected = float(original.get("duration") or 0)
    actual = float(episode.get("total_time") or 0)
    if expected and actual and abs(expected - actual) / expected > .02:
        raise HTTPException(409, "This audio's duration changed. Its saved transcript needs to be checked again.")
    try:
        doc = library.library.transcript(library_id, include_words=False)
    except (LibraryError, ValueError, OSError) as exc:
        raise HTTPException(409, "The saved transcript does not match the current audio.") from exc
    if not doc.segments:
        return _cues(episode_id, doc)
    public = {"episode_id": str(episode_id), "source": SOURCES["library"], "origin": "library",
              "timed": bool(doc.segments), "media_verified": doc.media_verified,
              "text": doc.text, "segments": [segment.model_dump() for segment in doc.segments]}
    timed = _timed_file(library.library, library_id)
    try:        # the same segments and ids, with the words' own times: spot checks need them
        segments = engine.from_timed_json(timed) if timed else []
    except engine.TranscriptError:
        segments = []
    if [s.id for s in segments] != [s.id for s in doc.segments]:
        segments = engine.as_segments({"id": s.id, "start": s.start, "end": s.end, "text": s.text}
                                      for s in doc.segments)
    return Timing(public, segments, "library", _made_from(doc.media_identity, timed))


def _cues(episode_id: UUID | str, doc: Any) -> Timing:
    """A library transcript without a timed file. Some saved publisher transcripts are SRT or
    WebVTT in a .txt file; their cues carry the timing, so they are served timed."""
    cues: list[engine.Segment] = []
    if engine.transcript.sniff(doc.text) in ("srt", "vtt"):
        try:
            cues = engine.transcript.parse(doc.text)
        except engine.TranscriptError:
            cues = []
    public = {"episode_id": str(episode_id), "source": SOURCES["library"], "origin": "library",
              "timed": bool(cues), "media_verified": doc.media_verified,
              "text": engine.plain_text(cues) if cues else doc.text,
              "segments": [{"id": s.id, "start": s.start, "end": s.end, "text": s.text, "words": []} for s in cues]}
    return Timing(public, cues, "library", _made_from(doc.media_identity, None))


def _timed_file(library: FilesystemLibrary, library_id: str) -> dict[str, Any] | None:
    try:
        show_dir, raw = library._locate(library_id)
        timed_path = library._timed_path(show_dir, raw)
        return library._read_json(timed_path) if timed_path else None
    except (LibraryError, OSError):
        return None


def _made_from(identity: Any, timed: dict[str, Any] | None) -> dict[str, Any] | None:
    """The identity of the audio a library transcript was made from. When it records no length,
    the timed file's own ``duration`` (the length speech-to-text measured) stands in for it."""
    if hasattr(identity, "model_dump"):                 # the library's MediaIdentity model
        identity = identity.model_dump()
    made = {k: v for k, v in identity.items() if v not in (None, "")} if isinstance(identity, dict) else {}
    if engine.media_duration(made) is None and timed:
        duration = engine.media_duration({"duration": timed.get("duration")})
        if duration is not None:
            made["duration_seconds"] = duration
    return made or None


def _preferred(episode_id: UUID | str, episode: dict[str, Any], podfetch: PodFetch) -> Timing:
    native = podfetch.get(f'/api/v1/podcasts/episodes/{episode["id"]}/transcript', optional=True)
    if native and native.get("segments"):
        origin = "generated" if native.get("source") == "generated" else "feed"
        segments = [{"id": str(s["idx"]), "start": s["startMs"] / 1000,
                     "end": s["endMs"] / 1000 if s.get("endMs") is not None else None,
                     "text": s["text"]} for s in native["segments"] if s.get("startMs") is not None]
        public = {"episode_id": str(episode_id), "source": SOURCES[origin], "origin": origin,
                  "timed": bool(segments), "text": "\n".join(s["text"] for s in native["segments"]),
                  "segments": segments}
        # A segment without an end runs to the next one's start; the public list keeps None.
        return Timing(public, engine.from_podfetch(native, group=False), origin)
    return Timing({"episode_id": str(episode_id), "source": None, "origin": None, "timed": False,
                   "text": "", "segments": []})
