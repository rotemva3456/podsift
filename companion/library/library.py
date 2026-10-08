from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .media import MediaIdentityError, verify_media
from .models import (
    EpisodeDetail,
    EpisodeSummary,
    LibraryResponse,
    ShowSummary,
    TimingGranularity,
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)


class LibraryError(ValueError):
    pass


class FilesystemLibrary:
    """Read the existing on-disk library without importing the private CLI runtime."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def _title(show_id: str) -> str:
        return " ".join(part.capitalize() for part in show_id.replace("_", "-").split("-") if part)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise LibraryError(f"cannot read {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise LibraryError(f"{path.name} must contain a JSON object")
        return value

    def _show_dirs(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            path for path in self.root.iterdir()
            if path.is_dir() and not path.name.startswith("_") and (path / "_feed.json").is_file()
        )

    @staticmethod
    def _episode_id(show_id: str, episode: dict[str, Any]) -> str:
        stable = str(episode.get("key") or episode.get("guid") or episode.get("n") or "episode")
        return f"{show_id}--{stable}"

    @staticmethod
    def _local_transcript_path(show_dir: Path, episode: dict[str, Any]) -> Path | None:
        value = episode.get("words_file")
        if value:
            return show_dir / Path(str(value)).name
        key = episode.get("key")
        if key:
            matches = sorted(show_dir.glob(f"*--{key}.txt"))
            return matches[0] if matches else None
        return None

    @classmethod
    def _timed_path(cls, show_dir: Path, episode: dict[str, Any]) -> Path | None:
        transcript = cls._local_transcript_path(show_dir, episode)
        if transcript:
            candidate = transcript.with_suffix(".timed.json")
            if candidate.is_file():
                return candidate
        key = episode.get("key")
        if key:
            matches = sorted(show_dir.glob(f"*--{key}.timed.json"))
            return matches[0] if matches else None
        return None

    @staticmethod
    def _local_audio_path(show_dir: Path, episode: dict[str, Any], timed_path: Path) -> Path | None:
        configured = episode.get("audio_file")
        if configured:
            candidate = show_dir / Path(str(configured)).name
            if candidate.is_file():
                return candidate
        suffix = ".timed.json"
        candidate = Path(str(timed_path)[:-len(suffix)] + ".mp3")
        return candidate if candidate.is_file() else None

    @classmethod
    def _verify_timing_media(
        cls, show_dir: Path, episode: dict[str, Any], timed_path: Path, timed: dict[str, Any]
    ) -> bool:
        try:
            return verify_media(
                timed.get("media") if isinstance(timed.get("media"), dict) else None,
                str(episode.get("audio") or ""),
                cls._local_audio_path(show_dir, episode, timed_path),
            )
        except (MediaIdentityError, OSError, TypeError, ValueError) as exc:
            raise LibraryError(f"stale timing for {timed_path.name}: {exc}") from exc

    @classmethod
    def _timing(cls, show_dir: Path, episode: dict[str, Any]) -> TimingGranularity:
        path = cls._timed_path(show_dir, episode)
        if not path:
            return TimingGranularity.NONE
        timed = cls._read_json(path)
        cls._verify_timing_media(show_dir, episode, path, timed)
        if timed.get("words") or any(item.get("words") for item in timed.get("segments") or []):
            return TimingGranularity.WORD
        return TimingGranularity.SEGMENT if timed.get("segments") else TimingGranularity.NONE

    @classmethod
    def _episode(cls, show_id: str, show_dir: Path, raw: dict[str, Any]) -> EpisodeSummary:
        transcript = cls._local_transcript_path(show_dir, raw)
        timing = cls._timing(show_dir, raw)
        try:
            duration = max(0.0, float(raw.get("duration") or 0))
        except (TypeError, ValueError):
            duration = 0.0
        return EpisodeSummary(
            id=cls._episode_id(show_id, raw),
            show_id=show_id,
            number=raw.get("n") if isinstance(raw.get("n"), int) else None,
            title=str(raw.get("title") or "Untitled episode"),
            duration_seconds=duration,
            published=str(raw.get("published") or ""),
            audio_url=str(raw.get("audio") or ""),
            transcript_ready=bool(transcript and transcript.is_file()),
            timing=timing,
        )

    def list_library(self, episode_limit: int | None = 80) -> LibraryResponse:
        shows: list[ShowSummary] = []
        episodes: list[EpisodeSummary] = []
        for show_dir in self._show_dirs():
            manifest = self._read_json(show_dir / "_feed.json")
            raw_episodes = [item for item in manifest.get("episodes") or [] if isinstance(item, dict)]
            normalized = [self._episode(show_dir.name, show_dir, item) for item in raw_episodes]
            shows.append(ShowSummary(
                id=show_dir.name,
                title=str(manifest.get("title") or self._title(show_dir.name)),
                feed_url=str(manifest.get("feed") or ""),
                episode_count=len(normalized),
                prepared_count=sum(item.timing != TimingGranularity.NONE for item in normalized),
                total_seconds=sum(item.duration_seconds for item in normalized),
            ))
            episodes.extend(normalized)
        episodes.sort(key=lambda item: (item.published, item.number or 0), reverse=True)
        if episode_limit is not None:
            episodes = episodes[:max(0, episode_limit)]
        return LibraryResponse(shows=shows, episodes=episodes)

    def _locate(self, episode_id: str) -> tuple[Path, dict[str, Any]]:
        for show_dir in self._show_dirs():
            manifest = self._read_json(show_dir / "_feed.json")
            for raw in manifest.get("episodes") or []:
                if isinstance(raw, dict) and self._episode_id(show_dir.name, raw) == episode_id:
                    return show_dir, raw
        raise LibraryError(f"unknown episode: {episode_id}")

    def get_episode(self, episode_id: str) -> EpisodeDetail:
        show_dir, raw = self._locate(episode_id)
        summary = self._episode(show_dir.name, show_dir, raw)
        transcript = raw.get("transcript") if isinstance(raw.get("transcript"), dict) else {}
        return EpisodeDetail(**summary.model_dump(), transcript_source=transcript.get("url"))

    def transcript(self, episode_id: str, *, include_words: bool = True) -> TranscriptDocument:
        show_dir, raw = self._locate(episode_id)
        transcript_path = self._local_transcript_path(show_dir, raw)
        plain_text = ""
        if transcript_path and transcript_path.is_file():
            plain_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        timed_path = self._timed_path(show_dir, raw)
        if not timed_path:
            return TranscriptDocument(episode_id=episode_id, granularity=TimingGranularity.NONE,
                                      source=str(raw.get("source") or ""), text=plain_text)

        timed = self._read_json(timed_path)
        media_verified = self._verify_timing_media(show_dir, raw, timed_path, timed)
        top_words = timed.get("words") if include_words and isinstance(timed.get("words"), list) else []
        segments: list[TranscriptSegment] = []
        for index, item in enumerate(timed.get("segments") or []):
            if not isinstance(item, dict):
                continue
            try:
                start, end = float(item["start"]), float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                continue
            raw_words = item.get("words") if include_words and isinstance(item.get("words"), list) else []
            if not raw_words:
                for word in top_words:
                    if not isinstance(word, dict):
                        continue
                    try:
                        word_start = float(word.get("start", -1))
                        word_end = float(word.get("end", -1))
                    except (TypeError, ValueError):
                        continue
                    if word_start < end and word_end > start:
                        raw_words.append(word)
            words: list[TranscriptWord] = []
            for word in raw_words:
                try:
                    word_start, word_end = float(word["start"]), float(word["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                text = str(word.get("word") or word.get("text") or "").strip()
                if text and math.isfinite(word_start) and math.isfinite(word_end) and word_end >= word_start:
                    words.append(TranscriptWord(text=text, start=word_start, end=word_end))
            segments.append(TranscriptSegment(
                id=f"seg-{index + 1:04d}", start=start, end=end,
                text=str(item.get("text") or "").strip(), words=words,
            ))
        granularity = TimingGranularity.WORD if any(segment.words for segment in segments) else (
            TimingGranularity.SEGMENT if segments else TimingGranularity.NONE
        )
        text = plain_text or " ".join(segment.text for segment in segments)
        return TranscriptDocument(episode_id=episode_id, granularity=granularity,
                                  source=str(timed.get("source") or raw.get("source") or ""),
                                  media_identity=timed.get("media"), media_verified=media_verified,
                                  text=text, segments=segments)

    def all_episodes(self) -> list[EpisodeSummary]:
        return self.list_library(episode_limit=None).episodes

    def searchable_text(self, episode_id: str) -> str:
        episode = self.get_episode(episode_id)
        transcript = self.transcript(episode_id)
        return f"{episode.title}\n{transcript.text}".lower()
