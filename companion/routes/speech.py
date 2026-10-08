"""Speech to text for hands-free notes and Ask-by-voice.

    POST /companion/speech/transcribe   raw audio bytes -> {text, duration}
    GET  /companion/speech/status       {"configured": bool}

The body is whatever the browser's MediaRecorder produced (commonly webm/opus, ogg/opus or
mp4/aac) sent as-is, with its ``Content-Type`` set to the clip's MIME type; no multipart, so this
route needs no extra dependency. It is always converted to a 16 kHz mono mp3 with
``engine.shrink`` (the same step ``cuts.spot_check_file`` uses) before it reaches the AI provider,
because a provider must never see opus directly: Groq stretches an opus timeline 1.74x
(``providers/openai_compat.py``). Speech-to-text is the user's own AI provider's
``POST /audio/transcriptions``, through ``cuts.get_speech`` (the provider only when it implements
``transcribe_audio``; OpenAI and Groq Whisper do) - the same dependency the render job's spot
checks use. 409 when no such provider is connected.

Recordings are capped at MAX_SECONDS and MAX_UPLOAD_BYTES, checked before anything is decoded.
The uploaded bytes and the converted clip live only in a per-request temp folder, deleted (even on
error) before the response is sent.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import engine
from ..cuts import get_speech
from ..llm import LLMError

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # a 30 s clip is a few hundred KB compressed, a few MB as wav
MAX_SECONDS = 30.0
DURATION_SLACK = 1.5                  # container/encoder overhead; only reject clearly-too-long clips

NEED_SPEECH = "Connect AI in Settings → AI to use voice."
TOO_LARGE = "That recording is too large. Recordings are limited to 30 seconds."
EMPTY_RECORDING = "No audio was recorded. Try again."
TOO_LONG = "Recordings are limited to 30 seconds."
UNREADABLE = "That recording couldn't be read. Try again."
NO_SPEECH = "No speech was recognized. Try again in a quieter place."


@router.get("/companion/speech/status")
def speech_status(speech: Any = Depends(get_speech)) -> dict[str, bool]:
    return {"configured": speech is not None}


@router.post("/companion/speech/transcribe")
async def transcribe(request: Request, speech: Any = Depends(get_speech)) -> dict[str, Any]:
    if speech is None:
        raise HTTPException(409, NEED_SPEECH)
    audio = await _read_capped(request)
    if not audio:
        raise HTTPException(422, EMPTY_RECORDING)
    with tempfile.TemporaryDirectory(prefix="speech-") as scratch:
        upload_path = os.path.join(scratch, "upload.bin")
        with open(upload_path, "wb") as handle:
            handle.write(audio)
        try:
            duration = engine.probe_duration(upload_path)
        except engine.AudioError as exc:
            raise HTTPException(422, UNREADABLE) from exc
        if duration <= 0:
            raise HTTPException(422, EMPTY_RECORDING)
        if duration > MAX_SECONDS + DURATION_SLACK:
            raise HTTPException(422, TOO_LONG)
        clip_path = os.path.join(scratch, "clip.mp3")
        try:
            engine.shrink(upload_path, clip_path)
        except engine.RenderError as exc:
            raise HTTPException(422, UNREADABLE) from exc
        try:
            result = speech.transcribe_audio(clip_path) or {}
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
    text = str(result.get("text") or "").strip()
    if not text:
        raise HTTPException(422, NO_SPEECH)
    return {"text": text, "duration": round(duration, 1)}


async def _read_capped(request: Request) -> bytes:
    """The request body, refused as soon as it (or its declared Content-Length) is too big."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, TOO_LARGE)
    return bytes(body)
