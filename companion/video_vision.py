"""Opt-in Course Watcher handoff: speech suggests times, the watcher checks pixels.

Only a server-configured service is contacted. Preparing a review never runs vision.
The learner inspects/edits the saved plan in Course Watcher before running it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from .llm import LLMError

MAX_REVIEW_SECONDS = 1800


def configured() -> bool:
    return bool(os.getenv('COURSE_WATCHER_URL', '').strip() and os.getenv('COURSE_WATCHER_PUBLIC_URL', '').strip())


def review_request(video: dict, learning: dict | None) -> tuple[str, list, dict | None, str]:
    question = (learning or {}).get('goal') or 'Explain the important visual demonstrations in this lesson.'
    selected = [m for m in (learning or {}).get('moments', []) if m['action'] == 'check_screen']
    # Without a screen-specific nomination, focus on the suggested watch intervals.
    selected = selected or (learning or {}).get('moments', [])
    plan = {'instruction': question, 'intervals': [
        {'id': f'focus-{i}', 'start_s': m['start'], 'end_s': m['end'], 'sample_fps': 1,
         'instruction': f"Check what is actually shown; speech-only reason: {m['why']}", 'role': 'evidence'}
        for i, m in enumerate(selected)
    ], 'frames': []} if selected else None
    key = hashlib.sha256(json.dumps({'id': video['id'], 'sha256': video['sha256'],
                                    'question': question, 'plan': plan}, sort_keys=True).encode()).hexdigest()
    return question, selected, plan, key


def prepare_review(source: Path, video: dict, learning: dict | None, *, transport=None) -> dict:
    if video['duration'] > MAX_REVIEW_SECONDS:
        raise LLMError('Course Watcher reviews currently support videos up to 30 minutes. The full speech transcript remains available.')
    question, selected, plan, key = review_request(video, learning)
    base = os.environ['COURSE_WATCHER_URL'].rstrip('/')
    public = os.environ['COURSE_WATCHER_PUBLIC_URL'].rstrip('/')
    for address in (base, public):
        url = urlsplit(address)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.query or url.fragment:
            raise LLMError('Course Watcher needs valid server and browser addresses.')
    try:
        with httpx.Client(base_url=base, timeout=300, follow_redirects=False, transport=transport) as client:
            with source.open('rb') as handle:
                uploaded = client.post('/api/sources/upload', params={'filename': video['filename']},
                                       content=iter(lambda: handle.read(1024 * 1024), b''),
                                       headers={'Content-Type': 'application/octet-stream'})
            uploaded.raise_for_status()
            payload = {'source_id': uploaded.json()['source_id'], 'question': question,
                       'request_key': 'podsift-' + key, 'task_profile': 'action', 'review_mode': 'assisted',
                       'engine': 'orchestrated', 'plan': plan}
            response = client.post('/api/reviews', json=payload)
            if response.status_code == 409:
                raise LLMError('Course Watcher already has an active review of this video. Open it to edit or cancel that review before preparing another.')
            response.raise_for_status()
            review = response.json()
            sid = review['session_id']
            return {'session_id': sid, 'url': public + '/?' + urlencode({'review': sid}) + '#plan',
                    'status': 'prepared', 'visual_coverage': 'none', 'selected_intervals': selected,
                    'transcript_digest': (learning or {}).get('transcript_digest'), 'request_key': key}
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise LLMError('Course Watcher could not prepare this review. Your video and transcript are saved; try again.') from None
