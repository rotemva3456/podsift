from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..answers import AnswerInput, AnswerResult, ProviderError, answer_context, answer_provider, select_context
from ..deps import PodFetch, Settings, TranscriptLoader, get_podfetch, get_settings, get_transcripts
from ..llm import LLM, get_llm

router = APIRouter()


@router.get("/companion/answers/status")
def answer_status(settings: Settings = Depends(get_settings), llm: LLM | None = Depends(get_llm)):
    return {"configured": answer_provider(settings.answer_provider, llm) is not None}


@router.post("/companion/episodes/{episode_id}/answer", response_model=AnswerResult)
def answer(episode_id: UUID, request: AnswerInput, podfetch: PodFetch = Depends(get_podfetch),
           transcripts: TranscriptLoader = Depends(get_transcripts),
           settings: Settings = Depends(get_settings), llm: LLM | None = Depends(get_llm)):
    episode = podfetch.episode(episode_id)
    if episode.get("total_time") and request.position > episode["total_time"]:
        raise HTTPException(422, "The question timestamp is past the end of this episode.")
    # The URL owns episode identity; no client or provider may supply source passages.
    episode = {**episode, "episode_id": str(episode_id)}
    doc = transcripts(episode_id, episode)
    context = select_context(episode, doc, request)
    try:
        return answer_context(context, answer_provider(settings.answer_provider, llm))
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
