from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..deps import TranscriptLoader, get_transcripts

router = APIRouter()


@router.get("/companion/episodes/{episode_id}/transcript")
def transcript(episode_id: UUID, transcripts: TranscriptLoader = Depends(get_transcripts)):
    return transcripts(episode_id)
