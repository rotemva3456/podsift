"""Does a transcript's timing fit the downloaded audio file?

A publisher's transcript can be timed for different audio than the file you downloaded: with
dynamic ad insertion each download can carry other ads, so the same words sit at other seconds.
`check_timing` is the cheap first check and needs no audio. `spot_check` compares a few short
windows of the real file - which the caller transcribed - against the transcript and finds the
offset. There is no network here: the caller supplies any audio text.

Both return a `TimingCheck`, which equals "ok", "mismatch" or "unverified" and carries a
`.reason` the user can read.
"""
from __future__ import annotations

import collections
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .media import media_duration, same_media
from .transcript import Segment, Word, as_segments, clean, hms

OK, MISMATCH, UNVERIFIED = "ok", "mismatch", "unverified"
FILE_MATCH = 1.0          # two files whose durations differ by more than this are not the same cut
NOISE = 1.0               # a spot-check offset smaller than this is measurement noise


class TimingCheck(str):
    """"ok", "mismatch" or "unverified" (it compares equal to that string), with `.reason`.
    Spot checks also set `.offset` (seconds added to every transcript time) and `.segments`
    (the transcript with that offset applied)."""

    reason: str
    offset: float
    segments: list[Segment] | None

    def __new__(cls, status: str, reason: str = "", offset: float = 0.0,
                segments: list[Segment] | None = None) -> TimingCheck:
        if status not in (OK, MISMATCH, UNVERIFIED):
            raise ValueError(f"unknown timing status {status!r}")
        check = super().__new__(cls, status)
        check.reason, check.offset, check.segments = reason, offset, segments
        return check

    @property
    def status(self) -> str:
        return str.__str__(self)


def end_tolerance(duration: float) -> float:
    """How far a transcript may run past the file's end before it counts as a mismatch."""
    return max(2.0, 0.005 * duration)


def duration_agrees(reported: float, actual: float, tolerance: float = 0.02) -> bool:
    """A speech-to-text result whose duration is off by more than 2% has a stretched timeline
    (Groq did this for opus input); every time from it would land in the wrong place."""
    return actual > 0 and abs(reported - actual) / actual <= tolerance


def check_timing(segments: Iterable[Any], origin: str, media: Mapping[str, Any] | None,
                 made_from: Mapping[str, Any] | None = None, *,
                 tolerance: float | None = None) -> TimingCheck:
    """Whether `segments` can be trusted against the downloaded file `media` (its identity
    from `media.identify`, with its duration).

    origin: "generated" (PodFetch's Whisper run on this file), "library" (a stored transcript
    whose own identity is `made_from`) or "feed" (the publisher's).
    - A transcript that runs past the end of the file: mismatch.
    - generated, or library made from this exact file (same sha256): ok.
    - library made from a file of another length: mismatch.
    - feed, or anything that can't be proven: unverified - run `spot_check`, or label the
      result "timing not checked".
    Pass segments as parsed, without a `duration` clamp, or the overrun can't be seen."""
    segs = as_segments(segments)
    if not segs:
        return TimingCheck(UNVERIFIED, "The transcript has no timed segments.")
    duration = media_duration(media)
    if duration is not None:
        allowed = end_tolerance(duration) if tolerance is None else tolerance
        last = max(s.end for s in segs)
        if last > duration + allowed:
            return TimingCheck(MISMATCH, f"The transcript runs to {hms(last)}, past the end of "
                                         f"your audio file ({hms(duration)}).")
    origin = (origin or "").strip().lower()
    if origin == "generated":
        return TimingCheck(OK, "The transcript was made from this audio file.")
    if origin == "library":
        same = same_media(made_from, media)
        if same:
            return TimingCheck(OK, "The transcript was made from this exact audio file.")
        made_duration = media_duration(made_from)
        if duration is not None and made_duration is not None and abs(made_duration - duration) > FILE_MATCH:
            return TimingCheck(MISMATCH, f"The transcript was made from a {hms(made_duration)} "
                                         f"version of this episode; your file is {hms(duration)}.")
        if same is False:
            return TimingCheck(UNVERIFIED, "The transcript was made from another copy of this episode.")
        return TimingCheck(UNVERIFIED, "The transcript does not say which audio file it was made from.")
    if origin == "feed":
        return TimingCheck(UNVERIFIED, "The publisher's transcript may be timed for another version "
                                       "of the audio.")
    return TimingCheck(UNVERIFIED, "Where this transcript came from is unknown.")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _timeline(segments: Sequence[Segment]) -> tuple[list[str], list[float]]:
    """Every transcript word with the second it is said: word timings when the segment has
    them, else spread evenly across the segment."""
    tokens, times = [], []
    for seg in segments:
        if seg.words:
            for word in seg.words:
                for token in _tokens(word.text):
                    tokens.append(token)
                    times.append(word.start)
            continue
        words = _tokens(seg.text)
        for k, token in enumerate(words):
            tokens.append(token)
            times.append(seg.start + (seg.end - seg.start) * k / len(words))
    return tokens, times


def _locate(heard: list[str], tokens: list[str],
            grams: dict[int, dict[tuple[str, ...], list[int]]]) -> int | None:
    """Where the heard words start in the transcript (a token position), by n-gram votes."""
    n = 3 if len(heard) >= 6 else 2
    if len(heard) < n + 1:
        return None
    if n not in grams:
        table: dict[tuple[str, ...], list[int]] = collections.defaultdict(list)
        for j in range(len(tokens) - n + 1):
            table[tuple(tokens[j:j + n])].append(j)
        grams[n] = table
    votes: collections.Counter[int] = collections.Counter()
    for i in range(len(heard) - n + 1):
        for j in grams[n].get(tuple(heard[i:i + n]), ()):
            votes[j - i] += 1
    if not votes:
        return None

    def support(shift: int) -> int:                  # an inserted or dropped word moves a vote
        return sum(votes.get(shift + d, 0) for d in range(-2, 3))

    best = max(votes, key=lambda shift: (support(shift), votes[shift]))
    if support(best) < max(2, 0.3 * (len(heard) - n + 1)):
        return None
    return max(0, min(len(tokens) - 1, best))


def _windows(heard: Iterable[Any]) -> list[tuple[float, str]]:
    out = []
    for item in heard or ():
        if isinstance(item, Mapping):
            item = (item.get("start"), item.get("text"))
        try:
            out.append((float(item[0]), str(item[1] or "")))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def spot_check(segments: Iterable[Any], heard: Iterable[Any], *, tolerance: float = 3.0,
               duration: float | None = None, min_windows: int = 2) -> TimingCheck:
    """Match short windows of the real audio to the transcript.

    heard: [(start, text)] - `start` is where the window sits in the downloaded file (the
    window start, or better, the first word's time from the speech-to-text result) and `text`
    what was said there. Take ~15 s at the start, middle and end.
    - Windows that agree on one offset: ok, with `.offset` and `.segments` shifted by it
      (clamped to `duration` when given; an offset under 1 s is noise and is not applied).
    - Windows whose offsets differ by more than `tolerance`: mismatch (ads were inserted
      between them).
    - Fewer than `min_windows` windows found in the transcript: unverified."""
    segs = as_segments(segments)
    tokens, times = _timeline(segs)
    windows = _windows(heard)
    grams: dict[int, dict[tuple[str, ...], list[int]]] = {}
    found = []
    for start, text in windows:
        position = _locate(_tokens(text), tokens, grams) if tokens else None
        if position is not None:
            found.append((start, start - times[position]))
    if len(found) < max(1, min_windows):
        return TimingCheck(UNVERIFIED, f"Only {len(found)} of {len(windows)} audio samples could "
                                       "be found in the transcript.")
    offsets = [offset for _start, offset in found]
    if max(offsets) - min(offsets) > tolerance:
        where = ", ".join(f"{offset:+.0f} s at {hms(start)}" for start, offset in found)
        return TimingCheck(MISMATCH, "The transcript's timing drifts against your audio "
                                     f"({where}): ads were probably inserted.")
    offset = statistics.median(offsets)
    if abs(offset) < NOISE:
        return TimingCheck(OK, "The transcript matches your audio file.", 0.0, segs)
    shifted = clean((Segment(s.id, s.start + offset, s.end + offset, s.text,
                             tuple(Word(w.text, w.start + offset, w.end + offset) for w in s.words),
                             s.speaker) for s in segs), duration)
    direction = "later" if offset > 0 else "earlier"
    return TimingCheck(OK, f"Your audio runs {abs(offset):.0f} s {direction} than the transcript; "
                           "its times were shifted to match.", round(offset, 3), shifted)
