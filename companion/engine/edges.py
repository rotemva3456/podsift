"""Where exactly to cut: in the pause before a passage's first word and after its last one.

A plan's edges sit on sentence edges from the transcript (`select.snap`), and transcript times
are only near the audio: Whisper's word times run up to ~0.1 s early or late, a publisher's
cue up to a second. Cutting right on them clips a first syllable or keeps the start of the
next sentence, and the render's fade then softens whatever speech is left at the edge. So every
edge moves to the quietest stretch near it, measured on the audio itself (`render.envelope`),
and gets a fade that fits inside that stretch.

Measured on N4N064 (two hosts, 320 kbps): between "...iterated on." and "And I've got" at
290.2 s no 10 ms frame drops below -30 dB, because the host runs the sentences together, and
the pause before "Going" at 302.84 s is mostly a breath at -32 to -39 dB. The transcript also
ends "history." at 302.16 s where the audio ends it at ~302.36 s: a cut placed on the
transcript played its last syllable, which speech-to-text heard as "tree". A fixed silence
threshold finds neither pause. A pause here is relative to the speaker: `QUIET` dB below the
window's speech level (its 90th percentile). Where even that finds nothing, the cut goes to the
quietest 10 ms near the planned time with a 10 ms fade, and the listen-back check (`verify`)
reports it.

No I/O here: callers pass the envelopes in.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .render import Envelope

LEAD = 0.20          # silence kept before a passage's first word
TAIL = 0.30          # ... and after its last word
SEARCH_OUT = 0.8     # how far past the planned edge (away from the passage) a pause is looked for
SEARCH_IN = 0.35     # ... and into the passage, where a late transcript time hides the real gap
LIMIT_SLACK = 0.15   # neighbouring words' transcript times may be this far off, too
QUIET = 22.0         # dB under the window's speech level that counts as a pause
MIN_QUIET = 0.02     # a pause is at least two 10 ms frames
BRIDGE = 0.03        # a click or breath this short inside a pause doesn't end it
REAL_PAUSE = 0.08    # shorter quiet stretches are the gaps inside run-together speech
LONG_PAUSE = 0.5     # choosing a pause, each second of its length counts as a second nearer, up to this
MAX_FADE = 0.12
MIN_FADE = 0.01
KEPT_FADE = 0.05     # no information at all (a flat signal): leave the edge, fade briefly
DIP_WINDOW = 0.3     # with no pause, the quietest moment this close to the planned edge ...
DIP_DISTANCE = 20.0  # ... where each second away from the planned time costs this many dB ...
FLAT = 3.0           # ... and only when it is at least this much quieter than the planned point
WINDOW = 1.5         # envelope seconds each side of a planned edge that `window` asks for


@dataclass(frozen=True)
class Cut:
    """One edge of a piece: where to cut in the source, the fade that fits, and how it was found.

    kind: "pause" (inside a real pause), "dip" (the quietest moment of run-together speech) or
    "kept" (the audio gave no clue; left where the transcript put it). room: seconds of pause the
    piece keeps at this edge, before its first word or after its last."""
    time: float
    fade: float
    kind: str
    level: float
    room: float = 0.0


def window(planned: float) -> tuple[float, float]:
    """The envelope window an edge at `planned` needs."""
    return max(0.0, planned - WINDOW), planned + WINDOW


def speech_level(levels: Sequence[float]) -> float:
    """The window's speech loudness: its 90th percentile, so pauses don't pull it down."""
    ordered = sorted(level for level in levels if level > -100)
    if not ordered:
        return -100.0
    return ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]


def quiet_runs(env: Envelope, lo: float, hi: float, threshold: float) -> list[tuple[float, float]]:
    """Stretches in [lo, hi] where the 10 ms frames are at or under `threshold`; a louder blip
    of up to `BRIDGE` seconds inside one doesn't split it."""
    runs: list[tuple[float, float]] = []
    first = None
    lo_i, hi_i = env.index(lo), env.index(hi)
    for i in range(lo_i, hi_i + 1):
        quiet = i < hi_i and env.levels[i] <= threshold
        if quiet and first is None:
            first = i
        elif not quiet and first is not None:
            start, end = max(lo, env.time(first)), min(hi, env.time(i))
            if runs and start - runs[-1][1] <= BRIDGE + 1e-9:
                runs[-1] = (runs[-1][0], end)
            elif end - start >= MIN_QUIET - 1e-9:
                runs.append((start, end))
            first = None
    return [run for run in runs if run[1] - run[0] >= MIN_QUIET - 1e-9]


def _level_at(env: Envelope, seconds: float) -> float:
    if not env.levels:
        return -100.0
    return env.levels[min(len(env.levels) - 1, env.index(seconds))]


def smoothed(env: Envelope, index: int) -> float:
    """The loudness of 30 ms around a frame (power mean): one quiet frame between two sounds
    inside a word is not a gap between words."""
    near = env.levels[max(0, index - 1):index + 2]
    if not near:
        return -100.0
    power = sum(10 ** (level / 10) for level in near) / len(near)
    return 10 * math.log10(power) if power > 0 else -120.0


def _fade(room: float) -> float:
    return min(MAX_FADE, max(MIN_FADE, 0.7 * room))


def _dip(env: Envelope, planned: float, lo: float, hi: float) -> Cut:
    """No pause: the quietest moment near `planned` - each second away costs `DIP_DISTANCE` dB,
    so a dip inside the next word loses to a slightly louder one between the words - when it
    is clearly quieter than `planned` itself."""
    if not env.levels:
        return Cut(round(planned, 3), KEPT_FADE, "kept", -100.0)
    here = smoothed(env, min(len(env.levels) - 1, env.index(planned)))
    frames = [i for i in range(env.index(max(lo, planned - DIP_WINDOW)), env.index(min(hi, planned + DIP_WINDOW)))
              if i < len(env.levels)]
    if frames:
        best = min(frames, key=lambda i: smoothed(env, i) + DIP_DISTANCE * abs(env.time(i) + env.frame / 2 - planned))
        if smoothed(env, best) <= here - FLAT:
            return Cut(round(env.time(best) + env.frame / 2, 3), MIN_FADE, "dip", env.levels[best])
    return Cut(round(planned, 3), KEPT_FADE, "kept", env.levels[min(len(env.levels) - 1, env.index(planned))])


def start_cut(env: Envelope, planned: float, *, earliest: float | None = None,
              latest: float | None = None) -> Cut:
    """Where a passage should start: in the pause right before its first word, which the
    transcript puts at `planned`. `earliest` is where the previous (unwanted) words end."""
    lo = max(env.start, planned - SEARCH_OUT, -math.inf if earliest is None else earliest)
    hi = min(env.end, planned + SEARCH_IN, math.inf if latest is None else latest)
    if hi - lo < MIN_QUIET:
        return Cut(round(planned, 3), KEPT_FADE, "kept", _level_at(env, planned))
    threshold = speech_level(env.levels) - QUIET
    runs = quiet_runs(env, lo, hi, threshold)
    if not runs:
        return _dip(env, planned, lo, hi)
    # the pause whose end - the first word's onset - is nearest the planned time, where a longer
    # pause counts as nearer (the pause before a sentence is longer than the gaps between its
    # words), and one that ends after the planned time is probably between the first words
    best = min(runs, key=lambda r: abs(r[1] - planned) + (0.15 if r[1] > planned + 0.05 else 0.0)
               - min(r[1] - r[0], LONG_PAUSE))
    cut = max(best[0], best[1] - LEAD)
    kind = "pause" if best[1] - best[0] >= REAL_PAUSE else "dip"
    return Cut(round(cut, 3), round(_fade(best[1] - cut), 3), kind, _level_at(env, cut), round(best[1] - cut, 3))


def end_cut(env: Envelope, planned: float, *, earliest: float | None = None,
            latest: float | None = None) -> Cut:
    """Where a passage should end: in the pause right after its last word, which the transcript
    ends at `planned`. `latest` is where the next (unwanted) words start."""
    lo = max(env.start, planned - SEARCH_IN, -math.inf if earliest is None else earliest)
    hi = min(env.end, planned + SEARCH_OUT, math.inf if latest is None else latest)
    if hi - lo < MIN_QUIET:
        return Cut(round(planned, 3), KEPT_FADE, "kept", _level_at(env, planned))
    threshold = speech_level(env.levels) - QUIET
    runs = quiet_runs(env, lo, hi, threshold)
    if not runs:
        return _dip(env, planned, lo, hi)
    best = min(runs, key=lambda r: abs(r[0] - planned) + (0.15 if r[0] < planned - 0.05 else 0.0)
               - min(r[1] - r[0], LONG_PAUSE))
    cut = min(best[1], best[0] + TAIL)
    kind = "pause" if best[1] - best[0] >= REAL_PAUSE else "dip"
    return Cut(round(cut, 3), round(_fade(cut - best[0]), 3), kind, _level_at(env, cut), round(cut - best[0], 3))


def neighbours(lines: Sequence[Any], start: float, end: float) -> tuple[float | None, float | None]:
    """Where the unwanted words around [start, end] are: the end of the last line before it and
    the start of the first line after it (with `LIMIT_SLACK`, since those times are near too)."""
    before = [line.end for line in lines if line.end <= start + 0.05]
    after = [line.start for line in lines if line.start >= end - 0.05]
    return (max(before) - LIMIT_SLACK if before else None,
            min(after) + LIMIT_SLACK if after else None)


def place(piece: Mapping[str, Any], start_env: Envelope, end_env: Envelope, lines: Sequence[Any] = (), *,
          floor: float = 0.0, ceiling: float | None = None) -> dict[str, Any]:
    """A render piece ({start, end, ...}) with its edges moved into pauses, `fade_in`/`fade_out`
    set, and `edges` recording what was planned and what was found. `floor`/`ceiling` keep it
    clear of the pieces around it in the same file.

    An edge that lies in the silence before the piece's first line (right after a skipped line,
    say) aims at that line's start instead: the pause to cut in is the one before the first
    word, not wherever the silence began. Likewise after the last line."""
    planned_start, planned_end = float(piece["start"]), float(piece["end"])
    earliest, latest = neighbours(lines, planned_start, planned_end)
    held = [line for line in lines if planned_start - 0.05 <= (line.start + line.end) / 2 <= planned_end + 0.05]
    if held:
        planned_start = max(planned_start, min(line.start for line in held))
        planned_end = min(planned_end, max(line.end for line in held))
    earliest = floor if earliest is None else max(floor, earliest)
    if ceiling is not None:
        latest = ceiling if latest is None else min(ceiling, latest)
    first = start_cut(start_env, planned_start, earliest=earliest, latest=planned_end)
    last = end_cut(end_env, planned_end, earliest=first.time, latest=latest)
    if last.time - first.time < 0.25:                # never let the edges eat the passage
        return {**piece, "lead": 0.0, "tail": 0.0,
                "edges": {"start": _record(planned_start, Cut(planned_start, KEPT_FADE, "kept", 0.0)),
                          "end": _record(planned_end, Cut(planned_end, KEPT_FADE, "kept", 0.0))}}
    return {**piece, "start": first.time, "end": last.time, "fade_in": first.fade, "fade_out": last.fade,
            "lead": first.room, "tail": last.room,
            "edges": {"start": _record(planned_start, first), "end": _record(planned_end, last)}}


def _record(planned: float, cut: Cut) -> dict[str, Any]:
    return {"planned": round(planned, 3), "cut": cut.time, "kind": cut.kind, "fade": cut.fade}


JOIN_PAUSE = 0.3     # the least pause where two pieces meet


def pace(pieces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Where two pieces would meet with less than `JOIN_PAUSE` of pause between them - both
    edges cut into run-together speech - the first one gets `gap_after`: that much silence,
    added when rendering. Without it the last word of one passage runs into the first of the
    next ("iterated on" + "going all the way" was heard as "iterated. Going all the way" on
    N4N064), which is as hard on a listener as on speech-to-text. `lead`/`tail` are the pauses
    each piece keeps inside itself (`place`)."""
    out = [dict(piece) for piece in pieces]
    for a, b in zip(out, out[1:]):
        pause = float(a.get("tail") or 0.0) + float(b.get("lead") or 0.0)
        a["gap_after"] = round(JOIN_PAUSE - pause, 3) if pause < JOIN_PAUSE else 0.0
    if out:
        out[-1]["gap_after"] = 0.0
    return out
