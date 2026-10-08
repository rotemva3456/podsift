"""Media identity: stale timing is refused when the file it was made from changes."""
import pytest

from companion.engine.media import MediaIdentityError, describe_media, media_duration, same_media, verify_media

URL = "https://example.invalid/ep.mp3"


def test_verify_media_rejects_a_changed_file(tmp_path):
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"original audio bytes")
    identity = describe_media(URL, audio, 61.5)
    assert verify_media(identity, URL, audio) is True
    assert verify_media(None, URL, audio) is False               # legacy timing with no identity
    audio.write_bytes(b"new ads, longer file!!")
    with pytest.raises(MediaIdentityError, match="size changed"):
        verify_media(identity, URL, audio)
    audio.write_bytes(b"original audio byteZ")                   # same size, other bytes
    with pytest.raises(MediaIdentityError, match="hash changed"):
        verify_media(identity, URL, audio)
    with pytest.raises(MediaIdentityError, match="different source URL"):
        verify_media(identity, "https://example.invalid/other.mp3")


def test_same_media_and_duration():
    a = {"sha256": "a" * 64, "duration_seconds": 61.5}
    assert same_media(a, dict(a)) is True
    assert same_media(a, {"sha256": "b" * 64}) is False
    assert same_media(a, {"source_url": URL}) is None
    assert media_duration(a) == 61.5 and media_duration({"duration": "nan"}) is None and media_duration(None) is None
