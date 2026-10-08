"""User-owned video records and a bounded, recoverable background work queue."""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import db, engine
from .engine.media import ffmpeg_input
from .llm import LLMError

log = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_DURATION = 8 * 3600
MIN_FREE_BYTES = 256 * 1024 * 1024
EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mkv', '.webm'}

db.register_migration('videos', 1, '''
CREATE TABLE videos (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'default', title TEXT NOT NULL,
    filename TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    duration REAL NOT NULL, has_audio INTEGER NOT NULL, media_token TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'saved', job_kind TEXT, progress REAL NOT NULL DEFAULT 0,
    error TEXT, transcript_json TEXT, learning_json TEXT, review_json TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(user_id, sha256)
);
CREATE INDEX videos_owner ON videos(user_id, created_at);
''')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root(database: Path) -> Path:
    return database.parent / 'videos'


def source_path(database: Path, row) -> Path:
    return root(database) / row['id'] / row['filename']


def owned(conn: sqlite3.Connection, video_id: str, user: str):
    row = conn.execute('SELECT * FROM videos WHERE id=? AND user_id=?', (video_id, user)).fetchone()
    if row is None:
        raise HTTPException(404, 'This video is not in your library.')
    return row


def public(row) -> dict:
    doc = json.loads(row['transcript_json']) if row['transcript_json'] else None
    result = json.loads(row['learning_json']) if row['learning_json'] else None
    return {key: row[key] for key in ('id', 'title', 'filename', 'bytes', 'duration', 'has_audio',
                                     'status', 'job_kind', 'progress', 'error', 'created_at')} | {
        'has_transcript': doc is not None, 'has_speech': bool(doc and doc['segments']),
        'media_url': f"/companion/videos/{row['id']}/media?token={row['media_token']}",
        'learning': result, 'visual_review': json.loads(row['review_json']) if row['review_json'] else None,
    }


def probe_video(path: Path) -> dict:
    try:
        response = subprocess.run(['ffprobe', '-v', 'error', *ffmpeg_input(path),
                                   '-show_entries', 'format=duration:stream=codec_type', '-of', 'json'],
                                  capture_output=True, text=True, timeout=30)
        if response.returncode:
            raise ValueError()
        metadata = json.loads(response.stdout)
        kinds = {s['codec_type'] for s in metadata.get('streams', [])}
        duration = float(metadata['format']['duration'])
        if 'video' not in kinds or not 0 < duration <= MAX_DURATION:
            raise ValueError()
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        raise HTTPException(422, 'Choose a readable video with a video track, up to eight hours long.') from None
    return {'duration': duration, 'has_audio': 'audio' in kinds}


def persist_json(database: Path, video_id: str, user: str, column: str, result: dict) -> None:
    if column not in ('transcript_json', 'learning_json', 'review_json'):
        raise ValueError('Unknown video record')
    with db.connect(database) as conn:
        conn.execute(f'UPDATE videos SET {column}=?, updated_at=? WHERE id=? AND user_id=?',
                     (json.dumps(result, ensure_ascii=False, allow_nan=False), now(), video_id, user))


class VideoQueue:
    def __init__(self, database: Path):
        self.database = database
        self.lock = threading.Lock()
        self.active = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='video-learning')
        with db.connect(database) as conn:
            conn.execute("UPDATE videos SET status='interrupted', error=?, updated_at=? "
                         "WHERE status IN ('queued','running','cancelling')",
                         ('Processing was interrupted. Retry to reuse completed transcription chunks.', now()))

    def update(self, video_id: str, **fields):
        with db.connect(self.database) as conn:
            conn.execute('UPDATE videos SET ' + ','.join(f'{key}=?' for key in fields) + ', updated_at=? WHERE id=?',
                         (*fields.values(), now(), video_id))

    def start(self, video_id: str, kind: str, work):
        with self.lock:
            if video_id in self.active:
                raise HTTPException(409, 'This video is already processing. Wait or cancel before starting another action.')
            if len(self.active) >= 6:
                raise HTTPException(429, 'The video queue is full. Wait for a video to finish and try again.')
            cancel = threading.Event()
            self.active[video_id] = cancel
            self.update(video_id, status='queued', job_kind=kind, progress=0, error=None)
            self.pool.submit(self._run, video_id, cancel, work)

    def _run(self, video_id: str, cancel, work):
        try:
            if cancel.is_set():
                raise engine.Cancelled('Processing cancelled. Your video and completed work are saved.')
            self.update(video_id, status='running')
            work(cancel, lambda fraction: self.update(video_id, progress=fraction))
            self.update(video_id, status='ready', progress=1, error=None)
        except engine.Cancelled as exc:
            self.update(video_id, status='cancelled', error=str(exc))
        except LLMError as exc:
            self.update(video_id, status='failed', error=str(exc))
        except BaseException as exc:
            # Never display ffmpeg output, local paths or a remote server's body.
            log.warning('Video %s processing failed (%s)', video_id, type(exc).__name__)
            self.update(video_id, status='failed', error='Processing failed. Your video is saved; try again.')
        finally:
            with self.lock:
                self.active.pop(video_id, None)

    def cancel(self, video_id: str):
        with self.lock:
            event = self.active.get(video_id)
            if event:
                event.set()
                self.update(video_id, status='cancelling')

    def close(self):
        with self.lock:
            for event in self.active.values():
                event.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


_QUEUE_LOCK = threading.Lock()


def queue_for(request) -> VideoQueue:
    with _QUEUE_LOCK:
        if not hasattr(request.app.state, 'video_queue'):
            request.app.state.video_queue = VideoQueue(request.app.state.settings.database)
        return request.app.state.video_queue
