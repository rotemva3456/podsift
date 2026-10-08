from __future__ import annotations

import asyncio
import json

import httpx

from podcast_mcp import videos
from podcast_mcp.client import CompanionClient


def test_agent_reads_every_video_page_and_cannot_confuse_text_with_vision():
    calls = []
    def handle(request):
        calls.append(request)
        cursor = int(request.url.params['cursor'])
        return httpx.Response(200, json={'video_id': 'source', 'digest': 'version', 'visual_coverage': 'none',
                                         'segments': [{'id': f'v{cursor}', 'start': cursor*60, 'end': cursor*60+9, 'text': 'Speech.'}],
                                         'next_cursor': cursor+1 if cursor < 2 else None})
    client = CompanionClient('http://podsift.test', 'Bearer user', transport=httpx.MockTransport(handle))
    pages = [asyncio.run(videos.get_video_transcript(client, 'source', cursor)) for cursor in range(3)]
    assert [p['complete'] for p in pages] == [False, False, True]
    assert pages[-1]['segments'][0]['start_clock'] == '2:00'
    assert all(p['digest'] == 'version' and p['visual_coverage'] == 'none' for p in pages)
    assert all(r.headers['Authorization'] == 'Bearer user' for r in calls)


def test_listing_never_processes_and_does_not_expose_native_media_token():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'videos': [{'id': 'v', 'title': 'lesson', 'duration': 75, 'media_url': '/media?token=private'}], 'next_cursor': None})
    result = asyncio.run(videos.list_videos(CompanionClient('http://podsift.test', transport=httpx.MockTransport(handle))))
    assert 'media_url' not in result['videos'][0]
    assert result['videos'][0]['open_url'] == 'http://podsift.test/ui/learn?video=v'
    assert [r.method for r in seen] == ['GET']


def test_request_generation_is_one_explicit_http_job_with_requested_task():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(202, json={'id': 'v', 'status': 'queued'})
    client = CompanionClient('http://podsift.test', transport=httpx.MockTransport(handle))
    result = asyncio.run(videos.request_video_learning(client, 'v', 'watch_plan', 'Find the diagram', 5))
    assert result['status'] == 'queued'
    assert json.loads(seen[0].content) == {'task': 'watch_plan', 'goal': 'Find the diagram', 'minutes': 5}
