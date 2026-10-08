"""Full video speech using the existing portable media and provider contracts.

Like Course Watcher's timeline, use accurate decoded slices, a measured duration,
cached responses and source-relative times. No Course Watcher credentials or repo
imports enter a Podsift install. Never infer words' times from untimed plain text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from . import engine
from .engine import media, render, transcript
from .llm import LLMError
from .providers.store import _private_write as _atomic

CHUNK_SECONDS = 600.0


def prepare_chunk(source: Path, target: Path, start: float, seconds: float, cancel) -> float:
    if not target.exists():
        part = target.with_suffix('.part.flac')
        try:
            render._run_ffmpeg([*media.ffmpeg_input(source), '-ss', f'{start:.6f}',
                               '-t', f'{seconds:.6f}', '-vn', '-ac', '1', '-ar', '16000',
                               '-c:a', 'flac', '-f', 'flac', str(part)], cancel=cancel)
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    measured = engine.probe_duration(target)
    if not measured or abs(measured - seconds) > 0.15:
        raise LLMError('The audio slice does not match the original video clock. Try importing the file again.')
    return measured


def _timed(payload: dict, seconds: float) -> tuple[list[dict], list[dict]]:
    def clean(items, key):
        result = []
        for item in items or []:
            try:
                start, end = float(item['start']), float(item['end'])
                text = str(item[key]).strip()
            except (KeyError, TypeError, ValueError):
                raise LLMError('The speech provider returned invalid timestamps.') from None
            # Whisper can pad the final WORD by up to a second even when its
            # duration and segment clock agree. Clamp that word's end only;
            # never accept a word starting beyond the source, or a stretched
            # segment/duration. Observed live: a 45 s FLAC ended its last word
            # at 45.5 s while the last segment and reported duration were 45 s.
            slack = 1.0 if key == 'word' else 0.15
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= seconds + slack
                    and (not text or start < seconds)):
                raise LLMError('The speech provider returned timestamps outside the audio slice.')
            if text and end > start:
                result.append({'start': start, 'end': min(end, seconds), key: text})
        return result
    reported = payload.get('duration')
    if reported is not None:
        try:
            duration = float(reported)
        except (ValueError, TypeError):
            raise LLMError('The speech provider returned an invalid duration.') from None
        if not math.isfinite(duration) or abs(duration - seconds) > max(1.0, seconds * 0.02):
            raise LLMError('The speech provider stretched the audio clock. Try a provider with accurate timestamps.')
    segments = clean(payload.get('segments'), 'text')
    try:
        words = clean(payload.get('words'), 'word')
    except LLMError:
        # Optional word stamps can fall beyond a decoder's final window while
        # its segment timestamps and measured duration are valid. Keep the
        # complete timed passage text, but withhold unusable word precision.
        # With no valid segments there is no safe fallback.
        if not segments:
            raise
        words = []
    if not segments and words:
        grouped = transcript.group_words([
            transcript.Segment(str(i), w['start'], w['end'], w['word']) for i, w in enumerate(words)
        ])
        segments = [{'start': s.start, 'end': s.end, 'text': s.text} for s in grouped]
    if str(payload.get('text') or '').strip() and not segments:
        raise LLMError('This speech provider returned words without timestamps. Use a provider with timed transcription.')
    compact = lambda text: ''.join(re.findall(r'\w+', text.casefold()))
    if payload.get('text') and compact(str(payload['text'])) != compact(' '.join(s['text'] for s in segments)):
        raise LLMError('The speech provider returned an incomplete timed transcript. Your completed chunks are saved; try again.')
    return segments, words


def full_transcript(source: Path, duration: float, sha256: str, folder: Path, speech: Any,
                    cancel, progress: Callable[[float], None]) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    segments, words, coverage = [], [], []
    clamped_words = 0
    word_fallback_chunks = 0
    # Cache identity includes the configured speech model; changing it does not reuse
    # a different provider's partial responses. Secrets are deliberately excluded.
    provider = '|'.join(str(getattr(speech, key, '')) for key in ('base_url', 'speech_model'))
    fingerprint = hashlib.sha256(f'{sha256}|{provider}|flac-v1'.encode()).hexdigest()[:24]
    start, index = 0.0, 0
    while start < duration:
        if cancel.is_set():
            raise engine.Cancelled('Transcription cancelled. Completed chunks are kept for retry.')
        seconds = min(CHUNK_SECONDS, duration - start)
        chunk = folder / f'{fingerprint}-{index}.flac'
        cache = folder / f'{fingerprint}-{index}.json'
        if cache.exists():
            payload = json.loads(cache.read_text())
        else:
            measured = prepare_chunk(source, chunk, start, seconds, cancel)
            # A digitally silent track must never become Whisper's invented subtitles.
            levels = render.envelope(chunk, [(0, measured)], frame=0.1, cancel=cancel)[0].levels
            if levels and max(levels) <= -90:
                payload = {'text': '', 'segments': [], 'words': [], 'duration': measured}
            else:
                method = getattr(speech, 'transcribe_words', None) or speech.transcribe_audio
                payload = method(chunk)
            _timed(payload, seconds)  # validate before committing a paid response
            _atomic(cache, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
        local_segments, local_words = _timed(payload, seconds)
        if local_words:
            clamped_words += sum(float(w['end']) > seconds for w in payload.get('words') or [])
        elif payload.get('words'):
            word_fallback_chunks += 1
        for item in local_segments:
            segments.append({**item, 'id': f'v{len(segments) + 1}',
                             'start': round(start + item['start'], 6), 'end': round(start + item['end'], 6)})
        for item in local_words:
            words.append({**item, 'start': round(start + item['start'], 6), 'end': round(start + item['end'], 6)})
        coverage.append({'start': start, 'end': start + seconds})
        progress((start + seconds) / duration)
        # Source slice starts are fixed on the original clock. Provider duration and
        # codec lead-in must never accumulate across ten-minute boundaries.
        start += seconds
        index += 1
    segments.sort(key=lambda s: (s['start'], s['end']))
    text = '\n'.join(s['text'] for s in segments)
    digest = hashlib.sha256(json.dumps(segments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'timed': bool(segments), 'text': text, 'segments': segments, 'words': words,
            'digest': digest, 'coverage': coverage, 'processed_seconds': duration,
            'duration': duration, 'coverage_kind': 'full_audio', 'visual_coverage': 'none',
            'media_sha256': sha256, 'clamped_word_ends': clamped_words,
            'word_timing': 'partial' if words and word_fallback_chunks else 'word' if words else 'segment',
            'word_timing_fallback_chunks': word_fallback_chunks}
