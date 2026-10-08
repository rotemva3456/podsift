"""Rendering on synthetic tones: the right seconds in the right ORDER, the right length and
loudness, an index back to the source, and refusal of changed or hostile media.

A duration check alone cannot see a reordered splice (swap two spans and the length is the
same), so the order test reads the pitch back out of the render."""
import json
import os
import subprocess
import threading

import pytest

from companion.engine import render
from companion.engine.media import AudioError, MediaIdentityError, identify, probe_duration
from companion.engine.render import Cancelled, cut, render_set, shrink, source_audio

from .audio import loudness, needs_ffmpeg, pitch, tone

pytestmark = needs_ffmpeg

LOW, HIGH = 300, 900          # Hz, far enough apart that no window is ambiguous
URLS = ["https://example.invalid/low.mp3", "https://example.invalid/high.mp3"]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Two 40 s tones - the high one 12 dB quieter - as local files and as a filled cache."""
    root = tmp_path_factory.mktemp("render")
    low, high = tone(root / "low.mp3", LOW), tone(root / "high.mp3", HIGH, volume=0.25)
    cache = root / "cache"
    cache.mkdir()
    for url, path in zip(URLS, (low, high), strict=True):
        with open(path, "rb") as src, open(cache / render.cache_key(url), "wb") as dst:
            dst.write(src.read())
    return {"low": low, "high": high, "cache": str(cache), "root": root}


def leftovers(folder):
    return [name for name in os.listdir(folder) if name.startswith(".podcut-") or name.endswith(".part")]


def test_spans_play_in_order_across_two_sources(media, tmp_path):
    low, high = URLS
    want = [low, high, low, high, high, low]          # deliberately not grouped by source
    spans = [{"audio": url, "start": 5.0 + 4 * i, "end": 13.0 + 4 * i} for i, url in enumerate(want)]
    out = tmp_path / "out.mp3"
    result = cut(spans, out, cache=media["cache"], level=False)
    assert result.duration == pytest.approx(8.0 * len(spans), abs=0.2)
    for i, url in enumerate(want):
        got = pitch(out, 8.0 * i + 2, secs=4)         # mid-span, clear of the fades
        want_hz = LOW if url == low else HIGH
        assert abs(got - want_hz) < 40, f"span {i} should be {want_hz} Hz, measured {got:.0f} Hz"


def test_level_pass_hits_the_podcast_standard(media, tmp_path):
    out = tmp_path / "loud.mp3"
    cut([{"audio": URLS[0], "start": 2.0, "end": 32.0}], out, cache=media["cache"], level=True)
    assert abs(loudness(out) - render.LUFS) < 1.5


def test_three_span_cut_has_the_right_length_loudness_and_index(media, tmp_path):
    spans = [
        {"audio": media["low"], "start": 2.0, "end": 12.0, "episode_id": "ep-low", "title": "Low tone",
         "why": "the setup"},
        {"audio": media["high"], "start": 20.0, "end": 27.5, "episode_id": "ep-high", "title": "High tone"},
        {"audio": media["low"], "start": 30.0, "end": 36.0, "episode_id": "ep-low", "title": "Low tone"},
    ]
    seen = []
    result = cut(spans, tmp_path / "three.mp3", progress=seen.append)
    assert result.duration == pytest.approx(23.5, abs=0.5)
    assert abs(loudness(result.path) - render.LUFS) < 1.5
    assert result.loudness and result.loudness["input_i"] < -16
    assert seen and seen == sorted(seen) and seen[-1] == 1.0
    assert result.index == [
        {"cut_start": 0.0, "cut_end": 10.0, "episode_id": "ep-low", "source_start": 2.0, "source_end": 12.0,
         "title": "Low tone"},
        {"cut_start": 10.0, "cut_end": 17.5, "episode_id": "ep-high", "source_start": 20.0, "source_end": 27.5,
         "title": "High tone"},
        {"cut_start": 17.5, "cut_end": 23.5, "episode_id": "ep-low", "source_start": 30.0, "source_end": 36.0,
         "title": "Low tone"},
    ]
    saved = json.loads((tmp_path / "three.json").read_text())
    assert saved["spans"] == result.index and saved["audio"] == "three.mp3"
    sheet = (tmp_path / "three.md").read_text()
    assert "- **00:10** in the cut = High tone 00:20-00:27" in sheet and "the setup" in sheet
    assert leftovers(tmp_path) == []


def test_changed_media_is_rejected(media, tmp_path):
    identity = identify(media["low"], URLS[0])
    assert identity["duration_seconds"] == pytest.approx(40.0, abs=0.2)
    wrong = dict(identity, sha256="0" * 64)
    with pytest.raises(MediaIdentityError, match="hash changed"):
        cut([{"audio": media["low"], "start": 1, "end": 5, "media": wrong}], tmp_path / "x.mp3")
    moved = dict(identity, source_url="https://example.invalid/other.mp3")
    with pytest.raises(MediaIdentityError, match="different source URL"):
        cut([{"audio": URLS[0], "start": 1, "end": 5, "media": moved}], tmp_path / "x.mp3", cache=media["cache"])
    with pytest.raises(ValueError, match="different media identities"):
        cut([{"audio": media["low"], "start": 1, "end": 5, "media": identity},
             {"audio": media["low"], "start": 6, "end": 9, "media": wrong}], tmp_path / "x.mp3")
    assert not (tmp_path / "x.mp3").exists() and leftovers(tmp_path) == []
    # the right identity passes
    assert cut([{"audio": media["low"], "start": 1, "end": 5, "media": identity}], tmp_path / "ok.mp3",
               level=False, index=False).duration == pytest.approx(4.0, abs=0.2)


def test_a_playlist_disguised_as_mp3_is_refused(media, tmp_path):
    evil = tmp_path / "evil.mp3"
    evil.write_text(f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile://{media['low']}\n#EXT-X-ENDLIST\n")
    with pytest.raises(AudioError):
        cut([{"audio": str(evil), "start": 0, "end": 5}], tmp_path / "out.mp3")
    with pytest.raises(AudioError):
        probe_duration(evil)
    assert not (tmp_path / "out.mp3").exists() and leftovers(tmp_path) == []


def test_bad_spans_are_refused_before_ffmpeg_runs(media, tmp_path):
    for bad in ({"audio": media["low"], "start": 5, "end": 5}, {"audio": media["low"], "start": -1, "end": 3},
                {"audio": media["low"], "start": float("nan"), "end": 3}, {"start": 0, "end": 3}):
        with pytest.raises(ValueError):
            cut([bad], tmp_path / "out.mp3")
    with pytest.raises(ValueError, match="nothing to cut"):
        cut([], tmp_path / "out.mp3")


def test_cancel_stops_the_render(media, tmp_path):
    stop = threading.Event()
    spans = [{"audio": media["low"], "start": 0, "end": 30}]

    def cancel_on_first_report(_fraction):
        stop.set()
    with pytest.raises(Cancelled):
        cut(spans, tmp_path / "out.mp3", progress=cancel_on_first_report, cancel=stop)
    with pytest.raises(Cancelled):
        cut(spans, tmp_path / "out.mp3", cancel=stop)
    assert not (tmp_path / "out.mp3").exists() and leftovers(tmp_path) == []


def test_source_audio_downloads_once_into_the_cache(media, tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, dest, max_bytes, allow_private=False):     # stands in for net.safe_fetch
        calls.append((url, allow_private))
        with open(media["low"], "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
    monkeypatch.setattr(render, "safe_fetch", fake_fetch)
    url = "https://example.invalid/new-episode.mp3"
    first = source_audio(url, tmp_path, allow_private=True)
    again = source_audio(url, tmp_path, identity=identify(first, url))
    assert first == again and calls == [(url, True)]
    assert os.path.basename(first) == render.cache_key(url) and len(render.cache_key(url)) == 36
    with pytest.raises(ValueError, match="cache folder"):
        source_audio(url, None)
    with pytest.raises(AudioError, match="missing"):
        source_audio(str(tmp_path / "gone.mp3"))


def test_shrink_makes_a_16k_mono_mp3_window_never_opus(media, tmp_path):
    out = shrink(media["low"], tmp_path / "window.mp3", start=10, seconds=15)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels",
                            "-of", "json", out], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["codec_name"], stream["sample_rate"], stream["channels"]) == ("mp3", "16000", 1)
    assert probe_duration(out) == pytest.approx(15.0, abs=0.3)
    assert abs(pitch(out, 5) - LOW) < 40


def test_render_set_writes_one_mp3_per_episode_and_an_index(media, tmp_path):
    jobs = [{"name": "001 - low", "title": "Low", "source_secs": 40,
             "spans": [{"audio": media["low"], "start": 0, "end": 4}, {"audio": media["low"], "start": 30, "end": 34}]},
            {"name": "../002 - high", "title": "High", "source_secs": 40,
             "spans": [{"audio": media["high"], "start": 0, "end": 5}]}]
    finished = []
    done = render_set(jobs, tmp_path / "set", workers=1, on_done=lambda job, path, dur: finished.append(path))
    names = sorted(os.listdir(tmp_path / "set"))
    assert "001 - low.mp3" in names and "-002 - high.mp3" in names and "INDEX.md" in names
    assert len(done) == 2 and len(finished) == 2
    index = (tmp_path / "set" / "INDEX.md").read_text()
    assert "skipped 0.4 min (00:04-00:30 of the episode)" not in index      # 26 s is under 45 s
    assert "2 spans kept. No cuts longer than 45 s." in index
