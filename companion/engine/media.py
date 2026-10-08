"""Media identity: which exact audio file a set of times belongs to.

Every stored time is seconds into one downloaded file. A different file for the same episode
(re-encoded, or with other ads inserted) can move every second, so timing data carries the
identity of the file it was made from, and cuts verify it before they touch the audio.
"""
from __future__ import annotations

import hashlib
import math
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The demuxers ffmpeg may use on episode audio. Anything else - an HLS playlist or a concat
# script saved as ".mp3" - is refused, so a hostile file cannot make ffmpeg open other files
# or URLs.
AUDIO_DEMUXERS = "mp3,mov,aac,ogg,matroska,wav,flac"


class MediaIdentityError(ValueError):
    """A timed transcript no longer describes the media it was created from."""


class AudioError(RuntimeError):
    """ffmpeg or ffprobe could not read or write the audio."""


def ffmpeg_input(path: str | Path) -> list[str]:
    """ffmpeg/ffprobe arguments that open one local audio file and nothing else."""
    return ["-protocol_whitelist", "file", "-format_whitelist", AUDIO_DEMUXERS,
            "-i", "file:" + str(Path(path).resolve())]


def probe_duration(path: str | Path) -> float:
    """Length of a local audio file in seconds, from ffprobe (0.0 when it reports none)."""
    out = subprocess.run(["ffprobe", "-v", "error", *ffmpeg_input(path), "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        detail = (out.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        raise AudioError(f"This file is not audio ffprobe can read ({detail[0][:200]}).")
    try:
        value = float((out.stdout or "").strip() or 0)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_media(
    source_url: str,
    path: str | Path | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Create the portable identity stored beside source-relative timing data."""
    identity: dict[str, Any] = {"source_url": source_url}
    if path is not None:
        media_path = Path(path)
        identity.update({
            "sha256": sha256_file(media_path),
            "byte_length": media_path.stat().st_size,
        })
    if duration_seconds is not None:
        identity["duration_seconds"] = float(duration_seconds)
    return identity


def identify(path: str | Path, source_url: str = "") -> dict[str, Any]:
    """Identity of a downloaded file: sha256, size and the duration ffprobe measures."""
    return describe_media(source_url, path, probe_duration(path))


def verify_media(
    identity: Mapping[str, Any] | None,
    source_url: str,
    path: str | Path | None = None,
) -> bool:
    """Verify known identity fields, returning False only for legacy unbound timing."""
    if not identity:
        return False
    recorded_url = str(identity.get("source_url") or "")
    if recorded_url and source_url and recorded_url != source_url:
        raise MediaIdentityError(
            "timed transcript belongs to a different source URL; realign this episode"
        )
    if path is None:
        return True
    media_path = Path(path)
    expected_size = identity.get("byte_length")
    if expected_size is not None and media_path.stat().st_size != int(expected_size):
        raise MediaIdentityError(
            "source media size changed after alignment; refusing stale timestamps"
        )
    expected_hash = str(identity.get("sha256") or "")
    if expected_hash and sha256_file(media_path) != expected_hash:
        raise MediaIdentityError(
            "source media hash changed after alignment; refusing stale timestamps"
        )
    return True


def media_duration(identity: Mapping[str, Any] | None) -> float | None:
    """The identity's duration in seconds, when it has a usable one."""
    if not identity:
        return None
    for key in ("duration_seconds", "duration"):
        try:
            value = float(identity.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def same_media(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool | None:
    """True when both identities name the same bytes (sha256), False when their hashes differ,
    None when either has no hash to compare."""
    hash_a = str((a or {}).get("sha256") or "")
    hash_b = str((b or {}).get("sha256") or "")
    if not hash_a or not hash_b:
        return None
    return hash_a == hash_b
