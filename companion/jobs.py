"""Render jobs: a plan becomes one MP3, one job at a time, on a background thread.

Every episode is cut from its DOWNLOADED file, never from a stream:
1. When PodFetch hasn't downloaded the episode, the job asks it to
   (``PUT /api/v1/podcasts/{episode_id}/episodes/download``) and waits. Only when PodFetch
   refuses (the caller may not download) does the job fetch the publisher's file itself, through
   ``engine.net.safe_fetch``; that copy may carry other ads, so its timing counts as unverified.
2. PodFetch's file is copied into the audio cache (capped by ``CACHE_MAX_MB``) and identified
   (sha256, size, duration). The copy is requested from the configured PodFetch address only.
3. Each transcript is checked against that file (``engine.align.check_timing``). An unverified
   one gets a spot check when speech-to-text is available (3 windows of 15 s); without it the cut
   is labelled "timing not checked". A mismatch fails the job.
4. ``listen.export`` moves every edge into a pause, renders the enabled spans with
   ``engine.render.cut`` (one decode, one encode, -16 LUFS), listens to the MP3 with
   speech-to-text against the plan's script, and renders again from the source when an edge is
   wrong. The cut keeps that check (``check``).

Up to 5 jobs wait behind the running one. Deleting a job cancels it.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from . import cuts, engine, listen
from .db import connect
from .deps import PodFetch
from .llm import LLMError
from .transcripts import TranscriptLibrary, load_timing

log = logging.getLogger(__name__)

MAX_WAITING = 5
DOWNLOAD_WAIT_SECONDS = 15 * 60.0
DOWNLOAD_POLL_SECONDS = 3.0
AUDIO_MAX_BYTES = engine.net.DEFAULT_MAX_BYTES
BYTES_PER_SECOND = 16_000             # 128 kbps: a size guess when a server doesn't say
CHUNK = 1024 * 1024

TRANSCRIPT_CHANGED = "This episode's transcript changed after the plan was made. Make the plan again."
RESTARTED = "The app restarted before this export finished. Export it again."
CANCELLED = "The export was cancelled."
FAILED = "The export failed. Please try again."
UNAVAILABLE = "The podcast library is unavailable. Please try again."


class JobFailed(Exception):
    """Stops a job with a message for the user and, optionally, the detail behind it."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


class QueueFull(Exception):
    pass


@dataclass
class RenderJob:
    id: str
    user: str
    plan_id: str
    episodes: frozenset[str]
    podfetch: PodFetch               # carries the caller's login headers
    library: TranscriptLibrary
    folders: cuts.Folders
    database: Path
    speech: Any                      # has transcribe_audio(path), or None: no spot checks
    cancel: threading.Event = field(default_factory=threading.Event)


class JobQueue:
    """One worker thread, started when a job arrives and gone when the queue is empty."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: deque[RenderJob] = deque()
        self._running: RenderJob | None = None
        self._thread: threading.Thread | None = None

    def submit(self, job: RenderJob) -> None:
        with self._lock:
            if len(self._waiting) >= MAX_WAITING:
                raise QueueFull(f"{MAX_WAITING} exports are already waiting. Try again when one has finished.")
            self._waiting.append(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="cut-render", daemon=True)
                self._thread.start()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            for job in self._waiting:
                if job.id == job_id:
                    self._waiting.remove(job)
                    job.cancel.set()
                    return True
            if self._running is not None and self._running.id == job_id:
                self._running.cancel.set()
                return True
        return False

    def knows(self, job_id: str) -> bool:
        with self._lock:
            return any(j.id == job_id for j in self._waiting) or (
                self._running is not None and self._running.id == job_id)

    def needed(self) -> set[str]:
        """Episodes whose cached audio a waiting or running job still needs."""
        with self._lock:
            jobs = [*self._waiting, *([self._running] if self._running else [])]
        return {episode for job in jobs for episode in job.episodes}

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._waiting:
                    self._running = self._thread = None
                    return
                job = self._waiting.popleft()
                self._running = job
            try:
                run(job, self)
            except Exception:                        # never kill the worker
                log.exception("cut render %s crashed", job.id)
            finally:
                with self._lock:
                    self._running = None


_QUEUE_LOCK = threading.Lock()


def job_queue(app: FastAPI) -> JobQueue:
    with _QUEUE_LOCK:
        queue = getattr(app.state, "cut_jobs", None)
        if queue is None:
            queue = app.state.cut_jobs = JobQueue()
        return queue


# ------------------------------------------------------------------ job rows


def submit(app: FastAPI, *, user: str, plan: Mapping[str, Any], podfetch: PodFetch,
           library: TranscriptLibrary, folders: cuts.Folders, database: Path, speech: Any) -> str:
    episodes = frozenset(s["episode_id"] for s in plan["spans"] if s["enabled"])
    job = RenderJob(str(uuid4()), user, plan["id"], episodes, podfetch, library, folders, Path(database), speech)
    stamp = cuts.now()
    # Committed before the worker can start, on its own connection: the worker updates this row.
    with closing(connect(database)) as db:
        db.execute("INSERT INTO cut_jobs (id, user_id, plan_id, status, stage, progress, created_at, updated_at) "
                   "VALUES (?,?,?,'queued','waiting',0,?,?)", (job.id, user, plan["id"], stamp, stamp))
        db.commit()
        try:
            job_queue(app).submit(job)
        except QueueFull:
            db.execute("DELETE FROM cut_jobs WHERE id=?", (job.id,))
            db.commit()
            raise
    return job.id


def public_job(row: sqlite3.Row) -> dict[str, Any]:
    """The Job JSON (plus plan_id, stage and detail)."""
    return {"id": row["id"], "status": row["status"], "progress": row["progress"], "error": row["error"],
            "cut_id": row["cut_id"], "plan_id": row["plan_id"], "stage": row["stage"], "detail": row["detail"]}


def read_job(app: FastAPI, db: sqlite3.Connection, job_id: str, user: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM cut_jobs WHERE id=? AND user_id=?", (job_id, user)).fetchone()
    if row is None:
        raise HTTPException(404, "This export doesn't exist anymore.")
    if row["status"] in ("queued", "running") and not job_queue(app).knows(job_id):
        db.execute("UPDATE cut_jobs SET status='failed', error=?, updated_at=? "
                   "WHERE id=? AND status IN ('queued', 'running')", (RESTARTED, cuts.now(), job_id))
        db.commit()
        row = db.execute("SELECT * FROM cut_jobs WHERE id=?", (job_id,)).fetchone()
    return public_job(row)


def delete_job(app: FastAPI, db: sqlite3.Connection, job_id: str, user: str) -> None:
    if db.execute("SELECT 1 FROM cut_jobs WHERE id=? AND user_id=?", (job_id, user)).fetchone() is None:
        raise HTTPException(404, "This export doesn't exist anymore.")
    job_queue(app).cancel(job_id)
    db.execute("DELETE FROM cut_jobs WHERE id=? AND user_id=?", (job_id, user))


def _update(database: Path, job_id: str, **values: Any) -> None:
    """Write job fields; a deleted (cancelled) job's row stays deleted."""
    names = ", ".join(f"{name}=?" for name in values)
    with closing(connect(database)) as db:
        db.execute(f"UPDATE cut_jobs SET {names}, updated_at=? WHERE id=?", [*values.values(), cuts.now(), job_id])
        db.commit()


class _Progress:
    """Throttled progress writes: every 1%, a stage change, or once a second."""

    def __init__(self, job: RenderJob):
        self.job, self.last, self.stage, self.at = job, 0.0, "", 0.0

    def __call__(self, fraction: float, stage: str | None = None) -> None:
        fraction = max(self.last, min(1.0, fraction))
        if stage and stage != self.stage or fraction - self.last >= 0.01 or time.monotonic() - self.at > 1:
            values: dict[str, Any] = {"progress": round(fraction, 3)}
            if stage and stage != self.stage:
                values["stage"] = self.stage = stage
            _update(self.job.database, self.job.id, **values)
            self.last, self.at = fraction, time.monotonic()


# ------------------------------------------------------------------ the job


def run(job: RenderJob, queue: JobQueue) -> None:
    progress = _Progress(job)
    try:
        _update(job.database, job.id, status="running", stage="audio", progress=0.0)
        progress.stage = "audio"
        with closing(connect(job.database)) as db:
            plan = cuts.load_plan(db, job.plan_id, job.user)
        cut_id = render(job, plan, queue, progress)
        _update(job.database, job.id, status="done", stage="done", progress=1.0, cut_id=cut_id)
    except engine.Cancelled:
        _update(job.database, job.id, status="failed", error=CANCELLED)
    except JobFailed as exc:
        _update(job.database, job.id, status="failed", error=str(exc), detail=exc.detail)
    except engine.MediaIdentityError as exc:
        _update(job.database, job.id, status="failed", detail=str(exc),
                error="The episode's audio file changed while exporting. Export it again.")
    except (cuts.LowDisk, cuts.CacheFull, engine.FetchError, engine.AudioError, ValueError) as exc:
        _update(job.database, job.id, status="failed", error=str(exc))
    except HTTPException as exc:                 # from the PodFetch client
        _update(job.database, job.id, status="failed", error=str(exc.detail))
    except Exception:
        log.exception("cut render %s failed", job.id)
        _update(job.database, job.id, status="failed", error=FAILED)


@dataclass
class EpisodeTiming:
    status: str             # "ok", or "unverified" (the cut is labelled "timing not checked")
    reason: str
    offset: float = 0.0     # seconds the file runs later than the transcript
    pieces: list[engine.Segment] = field(default_factory=list)   # the script, in the file's time


def render(job: RenderJob, plan: Mapping[str, Any], queue: JobQueue, progress: _Progress) -> str:
    spans = [s for s in plan["spans"] if s["enabled"]]
    if not spans:
        raise JobFailed("Turn on at least one passage, then export again.")
    cuts.require_free_disk(job.folders)
    planned = {e["episode_id"]: e for e in plan["episodes"]}
    order = list(dict.fromkeys(s["episode_id"] for s in spans))
    ready: dict[str, tuple[dict[str, Any], cuts.Media, EpisodeTiming]] = {}
    for number, episode_id in enumerate(order):
        _stop_if_cancelled(job)
        episode = {**job.podfetch.episode(episode_id), "episode_id": episode_id}
        progress(0.3 * number / len(order), "audio")
        media = ensure_audio(job, episode, queue,
                             lambda f, n=number: progress(0.3 * (n + min(f, 1.0)) / len(order)))
        progress(0.3 * (number + 1) / len(order), "timing")
        ready[episode_id] = (episode, media, check_episode(job, episode, media, planned[episode_id]))
    progress(0.4, "render")

    pieces: list[dict[str, Any]] = []
    for span in spans:
        episode, media, timing = ready[span["episode_id"]]
        shift = timing.offset - float(planned[span["episode_id"]].get("offset") or 0.0)
        start = max(0.0, min(media.duration, span["start"] + shift))
        end = max(0.0, min(media.duration, span["end"] + shift))
        if end - start < 0.05:
            continue
        last = pieces[-1] if pieces else None
        if last and last["episode_id"] == span["episode_id"] and 0 <= start - last["end"] <= 0.25:
            last["end"] = last["planned_end"] = max(last["end"], end)   # touching passages: one piece, no join
            last["span_ids"].append(span["id"])
            continue
        # planned_*: where the approved script puts the piece; start/end move into pauses later
        pieces.append({"audio": str(media.path), "start": start, "end": end, "episode_id": span["episode_id"],
                       "title": str(episode.get("name") or ""), "why": span.get("why") or span.get("text") or "",
                       "media": media.identity, "source_url": str(episode.get("url") or ""),
                       "planned_start": start, "planned_end": end, "span_ids": [span["id"]]})
    if not pieces:
        raise JobFailed("Nothing is left to export: every passage lies outside the audio file.")
    cuts.require_free_disk(job.folders)
    cut_id = str(uuid4())
    out = job.folders.cuts / f"{cut_id}.mp3"
    title = cut_title(plan, [ready[e][0] for e in order])
    sources = {e: listen.Source(e, str(ready[e][1].path), ready[e][1].duration, ready[e][2].pieces) for e in order}
    job.folders.cuts.mkdir(parents=True, exist_ok=True)
    try:
        result, check = listen.export(pieces, sources, out, speech=job.speech, cancel=job.cancel,
                                      tags={"title": title, "album": "Podcast cuts"},
                                      progress=lambda f, stage=None: progress(0.4 + 0.59 * f, stage))
    except BaseException:
        remove_cut_files(job.folders, cut_id)
        raise
    if job.cancel.is_set():                          # deleted while the last bytes were written
        remove_cut_files(job.folders, cut_id)
        raise engine.Cancelled(CANCELLED)
    timing = [{"episode_id": e, "status": ready[e][2].status, "reason": ready[e][2].reason,
               "offset": ready[e][2].offset} for e in order]
    data = {"title": title, "index": result.index, "timing": timing,
            "label": cuts.NOT_CHECKED if any(t["status"] != "ok" for t in timing) else None,
            "kept_seconds": plan["kept_seconds"], "loudness": result.loudness, "check": check}
    with closing(connect(job.database)) as db:
        db.execute("INSERT INTO cut_files (id, user_id, plan_id, duration, size_bytes, data, created_at) "
                   "VALUES (?,?,?,?,?,?,?)", (cut_id, job.user, plan["id"], round(result.duration, 3),
                                              result.size_bytes, json.dumps(data), cuts.now()))
        db.commit()
    return cut_id


def cut_title(plan: Mapping[str, Any], episodes: list[Mapping[str, Any]]) -> str:
    what = " ".join(str(plan.get("want") or "Cut").split())[:80]
    if len(episodes) == 1:
        return f"{what} - {' '.join(str(episodes[0].get('name') or 'episode').split())[:120]}"
    return f"{what} - {len(episodes)} episodes"


def remove_cut_files(folders: cuts.Folders, cut_id: str) -> None:
    for suffix in (".mp3", ".json", ".md"):
        try:
            (folders.cuts / f"{cut_id}{suffix}").unlink()
        except FileNotFoundError:
            pass


def _stop_if_cancelled(job: RenderJob) -> None:
    if job.cancel.is_set():
        raise engine.Cancelled(CANCELLED)


# ------------------------------------------------------------------ timing


def check_episode(job: RenderJob, episode: Mapping[str, Any], media: cuts.Media,
                  planned: Mapping[str, Any]) -> EpisodeTiming:
    """Does the transcript the plan used fit the file we are about to cut? The answer carries
    the transcript's sentence pieces in the file's time: the script the cut is checked against."""
    timing = load_timing(episode["episode_id"], dict(episode), job.podfetch, job.library)
    if planned.get("digest") and timing.digest != planned["digest"]:
        raise JobFailed(TRANSCRIPT_CHANGED)

    def pieces(offset: float) -> list[engine.Segment]:
        return [piece for piece, _n in engine.sentence_pieces(cuts._shift(timing.segments, offset, media.duration))]

    check = cuts.check_file(timing, media)          # check_timing, then a stored spot check
    if check == "unverified" and not media.checks.get(timing.digest):
        if job.speech is None:
            return EpisodeTiming("unverified", f"{check.reason} Connect an AI provider that offers "
                                               "speech-to-text (OpenAI or Groq) to check it.", pieces=pieces(0.0))
        try:
            check = cuts.spot_check_file(timing.segments, media.path, media.duration, job.speech)
        except LLMError as exc:               # not stored: the service may work next time
            return EpisodeTiming("unverified", f"The timing could not be checked: {exc}", pieces=pieces(0.0))
        with closing(connect(job.database)) as db:
            cuts.store_check(db, media, timing.digest, check)
    if check == "mismatch":
        raise JobFailed(cuts.MISMATCH, detail=check.reason)
    if check == "unverified":
        return EpisodeTiming("unverified", check.reason, pieces=pieces(0.0))
    return EpisodeTiming("ok", check.reason, check.offset, pieces(check.offset))


# ------------------------------------------------------------------ audio


def ensure_audio(job: RenderJob, episode: Mapping[str, Any], queue: JobQueue,
                 progress: Callable[[float], None]) -> cuts.Media:
    """The episode's downloaded file in the audio cache, identified."""
    episode_id = str(episode["episode_id"])
    with closing(connect(job.database)) as db:
        media = cuts.cached_media(db, job.folders, episode)
    if media is not None:
        os.utime(media.path)                         # most recently used: evicted last
        return media
    if not episode.get("status") and _request_download(job.podfetch, episode_id):
        episode = {**_wait_for_download(job, episode_id), "episode_id": episode_id}
    job.folders.cache.mkdir(parents=True, exist_ok=True)
    cuts.require_free_disk(job.folders)
    path = _copy_from_podfetch(job, episode, queue, progress) if episode.get("status") else None
    origin, version = "podfetch", cuts.audio_version(episode)
    if path is None:
        origin, version = "publisher", f"publisher|{episode.get('url') or ''}"
        path = _fetch_from_publisher(job, episode, queue)
    progress(1.0)
    identity = engine.identify(path, source_url=str(episode.get("url") or ""))
    media = cuts.Media(episode_id, Path(path), origin, version, identity)
    with closing(connect(job.database)) as db:
        cuts.save_media(db, media)
    return media


def _client(podfetch: PodFetch, timeout: float | httpx.Timeout) -> httpx.Client:
    return httpx.Client(base_url=podfetch.base_url, headers=podfetch.headers, transport=podfetch.transport,
                        timeout=timeout, follow_redirects=False)


def _request_download(podfetch: PodFetch, episode_id: str) -> bool:
    """Ask PodFetch to download the episode. False when it refuses: only privileged users may."""
    try:
        with _client(podfetch, podfetch.timeout) as client:
            response = client.put(f"/api/v1/podcasts/{episode_id}/episodes/download")
    except httpx.HTTPError as exc:
        raise JobFailed(UNAVAILABLE) from exc
    if response.status_code in (401, 403):
        return False
    if response.status_code == 404:
        raise JobFailed("This episode is no longer in your library.")
    if response.status_code >= 400:
        raise JobFailed("PodFetch could not start downloading this episode. Please try again.")
    return True


def _wait_for_download(job: RenderJob, episode_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + DOWNLOAD_WAIT_SECONDS
    while True:
        if job.cancel.wait(DOWNLOAD_POLL_SECONDS):
            raise engine.Cancelled(CANCELLED)
        episode = job.podfetch.episode(episode_id)
        if episode.get("status"):
            return episode
        if time.monotonic() > deadline:
            raise JobFailed("PodFetch hasn't finished downloading this episode yet. Export again in a few minutes.")


def podfetch_file_url(podfetch: PodFetch, episode: Mapping[str, Any]) -> str | None:
    """Where PodFetch serves its downloaded file: the path (and api key query) of ``local_url``,
    requested from the PodFetch address the companion is configured with, never from the host
    named in the link."""
    link = urlsplit(str(episode.get("local_url") or ""))
    if (not link.path.startswith("/") or "/podcasts/" not in link.path
            or ".." in unquote(link.path).split("/")):
        return None
    base = urlsplit(podfetch.base_url)
    return urlunsplit((base.scheme, base.netloc, link.path, link.query, ""))


def _extension(url: str) -> str:
    ext = os.path.splitext(urlsplit(url).path)[1].lower()
    return ext if ext in engine.render.AUDIO_EXTENSIONS else ".audio"


def _make_room(job: RenderJob, queue: JobQueue, incoming: int) -> None:
    free = cuts.free_bytes(job.folders.data) - job.folders.min_free_bytes
    if incoming > free:
        raise cuts.LowDisk(f"The episode needs {incoming / cuts.GB:.1f} GB and the disk is almost full. "
                           "Delete old cuts or free up space, then try again.")
    with closing(connect(job.database)) as db:
        cuts.make_room(job.folders, incoming, queue.needed() | set(job.episodes), db)


def _copy_from_podfetch(job: RenderJob, episode: Mapping[str, Any], queue: JobQueue,
                        progress: Callable[[float], None]) -> Path | None:
    url = podfetch_file_url(job.podfetch, episode)
    if url is None:
        return None
    dest = job.folders.cache / f"{episode['episode_id']}{_extension(url)}"
    try:
        with _client(job.podfetch, httpx.Timeout(60.0, connect=10.0)) as client:
            with client.stream("GET", url) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise JobFailed(f"PodFetch could not send the episode file (it answered "
                                    f"{response.status_code}). Please try again.")
                declared = int(response.headers.get("content-length") or 0)
                if declared > AUDIO_MAX_BYTES:
                    raise JobFailed(f"The episode file is larger than {AUDIO_MAX_BYTES // cuts.MB} MB, "
                                    "too large to cut.")
                _make_room(job, queue, declared)
                _write(response.iter_bytes(CHUNK), dest, job, declared, progress)
    except httpx.HTTPError as exc:
        raise JobFailed("Copying the episode from PodFetch was interrupted. Please try again.") from exc
    return dest


def _write(chunks: Iterator[bytes], dest: Path, job: RenderJob, declared: int,
           progress: Callable[[float], None]) -> None:
    """Stream into a private temp file beside ``dest`` and rename it into place when complete."""
    handle, part = tempfile.mkstemp(dir=dest.parent, prefix=".fetch-", suffix=".part")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            for chunk in chunks:
                _stop_if_cancelled(job)
                size += len(chunk)
                if size > AUDIO_MAX_BYTES:
                    raise JobFailed(f"The episode file is larger than {AUDIO_MAX_BYTES // cuts.MB} MB, "
                                    "too large to cut.")
                out.write(chunk)
                if declared:
                    progress(size / declared)
        if not size:
            raise JobFailed("PodFetch sent an empty episode file. Download the episode again.")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def _fetch_from_publisher(job: RenderJob, episode: Mapping[str, Any], queue: JobQueue) -> Path:
    """PodFetch refused to download: fetch the publisher's file ourselves, the safe way."""
    url = str(episode.get("url") or "")
    if not url:
        raise JobFailed("This episode has no audio link to download.")
    _make_room(job, queue, int((cuts._num(episode.get("total_time")) or 3600) * BYTES_PER_SECOND))
    dest = job.folders.cache / f"{episode['episode_id']}{_extension(url)}"
    engine.safe_fetch(url, dest, AUDIO_MAX_BYTES, allow_private=job.folders.allow_private)
    _stop_if_cancelled(job)
    _make_room(job, queue, 0)                        # the real size may be larger than the guess
    return dest
