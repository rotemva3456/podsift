from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .. import db as database, engine, video_learning, video_transcription, video_vision, videos
from ..cuts import get_speech
from ..deps import Settings, current_user, get_db, get_settings
from ..llm import LLM, get_llm
from ..providers.openai_compat import OpenAICompatLLMWithSpeech
from ..runtime import hosted_enabled

@asynccontextmanager
async def _lifespan(app):
    try:
        yield
    finally:
        queue = getattr(app.state, 'video_queue', None)
        if queue is not None:
            queue.close()
            del app.state.video_queue


router = APIRouter(lifespan=_lifespan)


def get_video_speech(speech=Depends(get_speech)):
    # Same env provider as PodFetch's episode STT; normalize its historical URL
    # spelling (without /v1) for our existing OpenAI-compatible speech client.
    base = os.getenv('TRANSCRIPTION_API_BASE_URL', '').strip().rstrip('/')
    if base:
        return OpenAICompatLLMWithSpeech(base_url=base if base.endswith('/v1') else base + '/v1',
                                       api_key=os.getenv('TRANSCRIPTION_API_KEY'),
                                       speech_model=os.getenv('TRANSCRIPTION_MODEL') or 'whisper-1')
    return speech


@router.get('/companion/videos/status')
def status(settings: Settings = Depends(get_settings), speech=Depends(get_video_speech),
           llm=Depends(get_llm)):
    return {'speech_configured': speech is not None, 'learning_configured': llm is not None,
            'vision_configured': video_vision.configured(), 'max_bytes': videos.MAX_UPLOAD_BYTES}


@router.get('/companion/videos')
def list_videos(request: Request, limit: int = Query(50, ge=1, le=100), cursor: int = Query(0, ge=0),
                conn=Depends(get_db), user: str = Depends(current_user)):
    videos.queue_for(request)  # recover interrupted work once, before returning status
    rows = conn.execute('SELECT * FROM videos WHERE user_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?',
                        (user, limit + 1, cursor)).fetchall()
    return {'videos': [videos.public(row) for row in rows[:limit]],
            'next_cursor': cursor + limit if len(rows) > limit else None}


@router.post('/companion/videos', status_code=201)
async def upload(request: Request, filename: str = Query(..., min_length=1, max_length=255),
                 settings: Settings = Depends(get_settings), conn=Depends(get_db), user=Depends(current_user)):
    extension = Path(filename.replace('\\', '/').split('/')[-1]).suffix.lower()
    if extension not in videos.EXTENSIONS:
        raise HTTPException(415, 'Choose an MP4, MOV, M4V, MKV, or WebM video.')
    length = request.headers.get('content-length', '')
    if length.isdigit() and int(length) > videos.MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Videos are limited to 1 GB.')
    video_id = str(uuid4())
    folder = videos.root(settings.database) / video_id
    folder.mkdir(parents=True, mode=0o700)
    stored_name = 'original' + extension
    path = folder / stored_name
    digest, size, registered = hashlib.sha256(), 0, False
    try:
        with path.open('wb') as handle:
            os.chmod(path, 0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > videos.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, 'Videos are limited to 1 GB.')
                if shutil.disk_usage(folder).free < videos.MIN_FREE_BYTES + len(chunk):
                    raise HTTPException(507, 'There is not enough free space to save this video.')
                digest.update(chunk)
                handle.write(chunk)
        if not size:
            raise HTTPException(422, 'The video file is empty.')
        probe = await run_in_threadpool(videos.probe_video, path)
        sha256 = digest.hexdigest()
        existing = conn.execute('SELECT * FROM videos WHERE user_id=? AND sha256=?', (user, sha256)).fetchone()
        if existing:
            previous = videos.source_path(settings.database, existing)
            if not previous.is_file():
                previous.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                os.replace(path, previous)
            return videos.public(existing)
        # No user-controlled path or executable reaches the media engine.
        title = filename.replace('\\', '/').split('/')[-1][:255]
        conn.execute('INSERT OR IGNORE INTO videos '
                     '(id,user_id,title,filename,sha256,bytes,duration,has_audio,media_token,created_at,updated_at) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (video_id, user, title, stored_name, sha256, size, probe['duration'], int(probe['has_audio']),
                      secrets.token_urlsafe(32), videos.now(), videos.now()))
        row = conn.execute('SELECT * FROM videos WHERE user_id=? AND sha256=?', (user, sha256)).fetchone()
        registered = row['id'] == video_id
        return videos.public(row)
    finally:
        if not registered:
            shutil.rmtree(folder, ignore_errors=True)


@router.get('/companion/videos/{video_id}')
def get_video(video_id: UUID, request: Request, conn=Depends(get_db), user=Depends(current_user)):
    videos.queue_for(request)
    return videos.public(videos.owned(conn, str(video_id), user))


@router.get('/companion/videos/{video_id}/media')
def media(video_id: UUID, request: Request, token: str = ''):
    # Native video and Range requests cannot attach a stored bearer header. An
    # unguessable, per-video read capability is disclosed only in owner reads.
    settings = request.app.state.settings
    # Hosted capabilities supplement signed account identity; they never replace
    # it. Self-hosted media keeps its intentional token-only playback behavior.
    user = current_user(request) if hosted_enabled() or not token else None
    with database.connect(settings.database) as conn:
        row = conn.execute('SELECT * FROM videos WHERE id=?', (str(video_id),)).fetchone()
        if (row is None or (token and not hmac.compare_digest(token, row['media_token']))
                or (user is not None and row['user_id'] != user)):
            raise HTTPException(404, 'This video link is unavailable.')
        path = videos.source_path(settings.database, row)
        if not path.is_file():
            raise HTTPException(404, 'The video file is missing. Import it again.')
        return FileResponse(path, filename=row['title'], content_disposition_type='inline',
                            headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})


@router.get('/companion/videos/{video_id}/transcript')
def transcript(video_id: UUID, cursor: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
               conn=Depends(get_db), user=Depends(current_user)):
    row = videos.owned(conn, str(video_id), user)
    if not row['transcript_json']:
        raise HTTPException(409, 'Transcribe this video to read its words.')
    doc = json.loads(row['transcript_json'])
    parts = doc['segments'][cursor:cursor + limit]
    # Paginated text remains complete and bounded for agents and the browser.
    return {key: value for key, value in doc.items() if key not in ('segments', 'words', 'text')} | {
        'video_id': str(video_id), 'segments': parts, 'text': '\n'.join(s['text'] for s in parts),
        'total_segments': len(doc['segments']),
        'next_cursor': cursor + len(parts) if cursor + len(parts) < len(doc['segments']) else None}


@router.post('/companion/videos/{video_id}/transcribe', status_code=202)
def transcribe(video_id: UUID, request: Request, settings=Depends(get_settings), conn=Depends(get_db),
               user=Depends(current_user), speech=Depends(get_video_speech)):
    row = videos.owned(conn, str(video_id), user)
    if row['transcript_json']:
        return videos.public(row)
    if not row['has_audio']:
        raise HTTPException(422, 'This video has no audio track. You can still watch it or prepare visual review.')
    if speech is None:
        raise HTTPException(409, 'Configure a speech provider in Settings → AI or the server transcription settings.')
    def work(cancel, progress):
        doc = video_transcription.full_transcript(videos.source_path(settings.database, row), row['duration'],
                                                  row['sha256'], videos.root(settings.database) / str(video_id) / 'chunks',
                                                  speech, cancel, progress)
        if cancel.is_set():
            raise engine.Cancelled('Transcription cancelled. Completed chunks are kept for retry.')
        videos.persist_json(settings.database, str(video_id), user, 'transcript_json', doc)
    videos.queue_for(request).start(str(video_id), 'transcribe', work)
    return {'id': str(video_id), 'status': 'queued'}


@router.post('/companion/videos/{video_id}/learn', status_code=202)
def learn(video_id: UUID, payload: video_learning.LearnRequest, request: Request,
          settings=Depends(get_settings), conn=Depends(get_db), user=Depends(current_user), llm=Depends(get_llm)):
    row = videos.owned(conn, str(video_id), user)
    if llm is None:
        raise HTTPException(409, 'Connect AI in Settings → AI to request a recap or watch guide.')
    if not row['transcript_json'] or not json.loads(row['transcript_json'])['segments']:
        raise HTTPException(409, 'Prepare a timed speech transcript first.')
    def work(cancel, progress):
        result = video_learning.learn_video(json.loads(row['transcript_json']), row['title'], payload, llm, cancel, progress)
        if cancel.is_set():
            raise engine.Cancelled('Learning cancelled. Your video and transcript are saved.')
        videos.persist_json(settings.database, str(video_id), user, 'learning_json', result)
    videos.queue_for(request).start(str(video_id), 'learn', work)
    return {'id': str(video_id), 'status': 'queued'}


@router.post('/companion/videos/{video_id}/cancel')
def cancel(video_id: UUID, request: Request, conn=Depends(get_db), user=Depends(current_user)):
    videos.owned(conn, str(video_id), user)
    videos.queue_for(request).cancel(str(video_id))
    return {'id': str(video_id), 'cancel_requested': True}


@router.post('/companion/videos/{video_id}/visual-review', status_code=202)
def visual_review(video_id: UUID, request: Request, settings=Depends(get_settings), conn=Depends(get_db), user=Depends(current_user)):
    row = videos.owned(conn, str(video_id), user)
    if not video_vision.configured():
        raise HTTPException(409, 'Course Watcher is not connected to this server.')
    if row['review_json']:
        review = json.loads(row['review_json'])
        learning = json.loads(row['learning_json']) if row['learning_json'] else None
        if review.get('request_key') == video_vision.review_request(dict(row), learning)[3]:
            return review
    def work(cancel, progress):
        result = video_vision.prepare_review(videos.source_path(settings.database, row), dict(row),
                                             json.loads(row['learning_json']) if row['learning_json'] else None)
        if cancel.is_set():
            raise engine.Cancelled('Review preparation cancelled. A prepared review may remain in Course Watcher.')
        videos.persist_json(settings.database, str(video_id), user, 'review_json', result)
        progress(1)
    videos.queue_for(request).start(str(video_id), 'visual-review', work)
    return {'id': str(video_id), 'status': 'queued'}
