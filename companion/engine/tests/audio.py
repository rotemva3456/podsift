"""Synthetic audio for the engine tests: ffmpeg's `sine` source only, no network, no fixtures."""
from __future__ import annotations

import array
import shutil
import subprocess

import pytest

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe are not installed")


def tone(path, hz: int, secs: float = 40, volume: float = 1.0) -> str:
    """A pure sine as mp3. `volume` scales it, so two sources can differ in loudness."""
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency={hz}:duration={secs}:sample_rate=44100",
                    "-af", f"volume={volume}", "-c:a", "libmp3lame", "-q:a", "3", str(path)],
                   check=True)
    return str(path)


def pitch(path, start: float, secs: float = 4) -> float:
    """Dominant frequency by zero crossings - a pure sine crosses zero 2f times a second."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(start), "-t", str(secs),
                          "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
                         capture_output=True, check=True).stdout
    pcm = array.array("h", raw[:len(raw) // 2 * 2])
    crossings = sum(1 for a, b in zip(pcm, pcm[1:], strict=False) if (a < 0) != (b < 0))
    return crossings / 2 / (len(pcm) / 44100.0)


def loudness(path) -> float:
    """Integrated loudness in LUFS, measured by ffmpeg's ebur128 filter."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(err.rsplit("I:", 1)[1].split("LUFS")[0])
