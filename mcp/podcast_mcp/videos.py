"""Video learning tools use the same user-owned HTTP records as the browser."""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from .client import CompanionClient
from .formatting import with_clocks


def public_video(client: CompanionClient, source: dict) -> dict:
    return with_clocks({key: value for key, value in source.items() if key != 'media_url'}, 'duration') | {
        'open_url': client.link('/ui/learn?video=' + quote(source['id'], safe=''))}


async def list_videos(client: CompanionClient, cursor: int = 0, limit: int = 20) -> dict:
    result = await client.get('/companion/videos', params={'cursor': cursor, 'limit': limit})
    return {**result, 'videos': [public_video(client, source) for source in result['videos']]}


async def get_video(client: CompanionClient, video_id: str) -> dict:
    return public_video(client, await client.get('/companion/videos/' + quote(video_id, safe='')))


async def get_video_transcript(client: CompanionClient, video_id: str, cursor: int = 0, limit: int = 100) -> dict:
    result = await client.get('/companion/videos/' + quote(video_id, safe='') + '/transcript',
                              params={'cursor': cursor, 'limit': limit})
    return {**result, 'complete': result['next_cursor'] is None,
            'segments': [with_clocks(segment, 'start', 'end') for segment in result['segments']],
            'open_url': client.link('/ui/learn?video=' + quote(video_id, safe=''))}


async def transcribe_video(client: CompanionClient, video_id: str) -> dict:
    return await client.post('/companion/videos/' + quote(video_id, safe='') + '/transcribe')


async def request_video_learning(client: CompanionClient, video_id: str, task: Literal['summary', 'watch_plan'],
                                 goal: str = '', minutes: int | None = None) -> dict:
    return await client.post('/companion/videos/' + quote(video_id, safe='') + '/learn',
                             json={'task': task, 'goal': goal, 'minutes': minutes})
