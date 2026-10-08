from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


class MediaIdentityError(ValueError):
    """A timed transcript no longer describes the media it was created from."""


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
