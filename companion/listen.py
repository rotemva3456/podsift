"""Listen back to a rendered cut, and cut again from the source when it isn't what was planned.

The render job (``jobs.render``) calls ``export``:
1. Every piece's edges move into the pauses around them (``engine.edges``), measured on the
   source file (``engine.render.envelope``: one decode per episode).
2. The pieces are rendered (``engine.cut``).
3. Speech-to-text hears the whole MP3, in clips of up to 8 minutes cut in one decode, and
   ``engine.verify`` compares what it heard with the plan's script. The MP3's loudness at every
   join shows dead air.
4. A word problem at an edge is only a suspicion until the original audio confirms it: a clip
   of the source around that edge is transcribed too, next to the unwanted words around it.
   Only when the original has the missing words inside the planned passage (or the extra words
   outside it) does the edge move, to the pause the original shows. Speech-to-text dropping a
   word the audio has, or a transcript wording the audio doesn't have, is reported and left.
5. Every fix is a new render of the whole cut from the source files, never a patch of the MP3:
   splicing the MP3 again would add a second set of joins, fades and encoder gaps. The fixed
   edges are heard again; at most ``MAX_RENDERS`` renders, and the best one is kept.

Without speech-to-text (the AI provider offers none) only the joins' silence is checked. A
speech-to-text failure never fails the export: the cut says what was and wasn't checked.
"""
from __future__ import annotations

import difflib
import logging
import math
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import engine
from .engine import edges, verify
from .engine.verify import Finding, Heard
from .llm import LLMError

log = logging.getLogger(__name__)

MAX_RENDERS = 3
CLIP_SECONDS = 480.0      # one upload: 8 minutes of 16 kHz mono mp3 is under 2 MB
CLIP_OVERLAP = 3.0        # each clip also hears this much of its neighbours, so no word is split
PROBE = 6.0               # seconds of the original each side of an edge that a probe hears
MAX_PROBES = 12           # probes per render
RELISTEN = 12.0           # after a fix, seconds each side of a changed edge that are heard again
EDGE_TOKENS = 25          # script words next to an edge that a probe is aligned with
CONTEXT_LINES = 2         # ... plus the unwanted lines just outside the edge
SLACK = 0.03              # an edge this close to a word counts as touching it
OVERLAP = 0.15            # where two words' times overlap, how far the boundary may be from their middle
WORD_KINDS = ("clipped_start", "clipped_end", "extra_start", "extra_end")

NO_SPEECH = ("Checked for silence at the joins only. Connect an AI provider with speech-to-text "
             "(OpenAI or Groq) in Settings → AI to check every word.")
FAILED = "The listen-back check stopped with an error, so this cut is not checked."


class Unheard(Exception):
    """Speech-to-text gave nothing usable; the message says why."""


@dataclass
class Source:
    """One episode's downloaded file and its script lines, in that file's time."""
    episode_id: str
    path: str
    duration: float
    lines: list[engine.Segment]


Progress = Callable[..., None]
Move = tuple[float, float, str, float]      # a new edge: (source time, fade, why, pause kept there)


# ------------------------------------------------------------------ hearing


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def words_of(speech: Any, clip: str, seconds: float) -> list[Heard]:
    """The words speech-to-text hears in one clip, with times in the clip. Word times when the
    provider gives them (``transcribe_words``), else each segment's words spread over it."""
    call = getattr(speech, "transcribe_words", None)
    result = (call if callable(call) else speech.transcribe_audio)(clip) or {}
    reported = _num(result.get("duration"))
    if reported and not engine.duration_agrees(reported, seconds, tolerance=0.1):
        raise Unheard("The speech-to-text service returned a stretched timeline, so its times can't be trusted.")
    heard = []
    for word in result.get("words") or []:
        if not isinstance(word, Mapping):
            continue
        text = str(word.get("word") or word.get("text") or "").strip()
        start, end = _num(word.get("start")), _num(word.get("end"))
        if text and start is not None and end is not None and 0 <= start <= end:
            heard.append(Heard(text, start, end))
    if heard:
        return heard
    for segment in result.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        parts = str(segment.get("text") or "").split()
        start, end = _num(segment.get("start")), _num(segment.get("end"))
        if not parts or start is None or end is None or end < start:
            continue
        step = (end - start) / len(parts)
        heard += [Heard(part, start + k * step, start + (k + 1) * step) for k, part in enumerate(parts)]
    return heard


def hear(speech: Any, path: str, windows: Sequence[tuple[float, float]], folder: str, name: str) -> list[Heard]:
    """Words heard inside `windows` of a file (times in that file), in clips of at most
    ``CLIP_SECONDS`` that overlap by ``CLIP_OVERLAP``; a word belongs to the clip its middle is
    in, and the last clip of a window keeps everything up to its end. The words stay in the
    order the service gave them (see ``engine.verify.check_words``)."""
    plan: list[tuple[float, float, float, float]] = []       # clip start, clip end, owned from, owned to
    for low, high in windows:
        at = low
        while at < high - 1e-6:
            own_end = min(high, at + CLIP_SECONDS)
            last = own_end >= high - 1e-6
            plan.append((max(low, at - CLIP_OVERLAP), min(high, own_end + CLIP_OVERLAP), at,
                         math.inf if last else own_end))
            at = own_end
    clips = engine.render.clips(path, [(a, b) for a, b, _c, _d in plan], folder, name=name)
    heard: list[Heard] = []
    for (start, end, own_start, own_end), clip in zip(plan, clips, strict=True):
        for word in words_of(speech, clip, end - start):
            if own_start <= start + (word.start + word.end) / 2 < own_end:
                heard.append(Heard(word.text, start + word.start, start + word.end))
    return heard                # in clip order and the service's own word order: never re-sorted by time


def merge_windows(windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in engine.merge_intervals(windows)]


# ------------------------------------------------------------------ one listen


@dataclass
class Listening:
    """One render and what listening to it found."""
    render: int
    path: str
    result: engine.CutResult
    pieces: list[dict[str, Any]]
    findings: list[Finding]
    judged: set[tuple[int, str]]
    expected: int = 0
    matched: int = 0
    heard: bool = False
    reason: str | None = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]


def checks_for(pieces: Sequence[Mapping[str, Any]], index: Sequence[Mapping[str, Any]],
               sources: Mapping[str, Source]) -> list[verify.Piece]:
    """The pieces as ``engine.verify`` sees them: where each sits in the cut and its script."""
    out = []
    for number, (piece, entry) in enumerate(zip(pieces, index, strict=True), 1):
        source = sources[piece["episode_id"]]
        words = verify.expected_words(source.lines, piece["planned_start"], piece["planned_end"])
        out.append(verify.Piece(number, piece["episode_id"], float(entry["cut_start"]), float(entry["cut_end"]),
                                float(piece["start"]), words))
    return out


def join_envelopes(path: str, checks: Sequence[verify.Piece], duration: float) -> dict[int, engine.render.Envelope]:
    """The rendered file's loudness around every join, its start (key 1) and its end (key 0)."""
    windows = {c.number: (max(0.0, c.cut_start - verify.JOIN_WINDOW), min(duration, c.cut_start + verify.JOIN_WINDOW))
               for c in checks}
    windows[0] = (max(0.0, duration - verify.JOIN_WINDOW), duration)
    found = engine.render.envelope(path, list(windows.values()))
    return dict(zip(windows, found, strict=True))


def listen(render: int, path: str, result: engine.CutResult, pieces: list[dict[str, Any]],
           sources: Mapping[str, Source], speech: Any, covered: Sequence[tuple[float, float]] | None,
           folder: str) -> Listening:
    """Check one render: the joins' silence always, the words inside `covered` (the whole cut
    when None) when there is speech-to-text."""
    checks = checks_for(pieces, result.index, sources)
    found = verify.check_joins(checks, join_envelopes(path, checks, result.duration), result.duration)
    done = Listening(render, path, result, pieces, found, set())
    if speech is None:
        done.reason = NO_SPEECH
        return done
    windows = merge_windows(covered if covered is not None else [(0.0, result.duration)])
    try:
        heard = hear(speech, path, windows, folder, f"heard{render}")
    except (LLMError, Unheard) as exc:
        done.reason = f"Checked for silence at the joins only: speech-to-text failed ({exc})"
        return done
    if not heard:
        done.reason = "Checked for silence at the joins only: speech-to-text heard no words in the cut."
        return done
    words = verify.check_words(checks, heard, windows)
    done.findings += words.findings
    done.judged, done.expected, done.matched, done.heard = words.judged, words.expected, words.matched, True
    return done


def _missing(findings: Sequence[Finding], edge: tuple[int, str | None], errors_only: bool) -> int:
    return sum(int(f.detail.get("missing", len(f.words))) for f in findings
               if f.kind in ("clipped_start", "clipped_end") and (f.piece, f.edge) == edge
               and (f.severity == "error" or not errors_only))


def carry(previous: Listening, current: Listening) -> None:
    """Word findings for edges this render didn't hear again (their audio didn't change) come
    from the render before; so do whole-piece findings, which only a full listen makes. The
    word count is the full listen's, plus the words the re-heard edges no longer miss."""
    if not previous.heard:
        return
    regained = sum(_missing(previous.findings, edge, True) - _missing(current.findings, edge, False)
                   for edge in current.judged)
    fresh = {(f.piece, f.edge) for f in current.findings if f.kind in WORD_KINDS or f.kind.startswith("stt_")
             or f.kind == "transcript_differs"}
    for finding in previous.findings:
        edge_words = finding.kind in WORD_KINDS or finding.kind.startswith("stt_") or finding.kind == "transcript_differs"
        if edge_words and (finding.piece, finding.edge) not in current.judged and (finding.piece, finding.edge) not in fresh:
            current.findings.append(finding)
        elif finding.kind in ("wrong_audio", "low_match"):
            current.findings.append(finding)
    current.expected, current.heard = previous.expected, True
    current.matched = max(0, min(previous.expected, previous.matched + regained))


# ------------------------------------------------------------------ confirming against the original


def _tokens(lines: Sequence[engine.Segment]) -> list[str]:
    return [token for line in lines for token in verify.tokens(line.text)]


def _outside(source: Source, piece: Mapping[str, Any], edge: str) -> list[engine.Segment]:
    """The unwanted lines just outside a piece's planned edge."""
    if edge == "start":
        return [line for line in source.lines if line.end <= piece["planned_start"] + 0.05][-CONTEXT_LINES:]
    return [line for line in source.lines if line.start >= piece["planned_end"] - 0.05][:CONTEXT_LINES]


def judge_edge(source: Source, piece: Mapping[str, Any], edge: str, heard: Sequence[Heard]) -> dict[str, Any] | None:
    """Which words the original has around one edge, from a probe of it (`heard`: source times,
    in the order the service gave them). Returns {wanted, unwanted, missing}: the first (or
    last) word of the passage heard, the unwanted word heard next to it (None when none was),
    and script words the original doesn't have. Words are placed by their ORDER; their times
    are only used later, and only where they don't contradict each other (``_region``). None
    when the probe can't find the passage's words at all."""
    inside = [token for token, _t in verify.expected_words(source.lines, piece["planned_start"], piece["planned_end"])]
    inside = inside[:EDGE_TOKENS] if edge == "start" else inside[-EDGE_TOKENS:]
    outside = _tokens(_outside(source, piece, edge))
    script = outside + inside if edge == "start" else inside + outside
    first_inside = len(outside) if edge == "start" else 0
    last_inside = len(script) - 1 if edge == "start" else len(inside) - 1
    said = [verify.tokens(w.text) for w in heard]
    flat = [(token, k) for k, parts in enumerate(said) for token in parts]
    matcher = difflib.SequenceMatcher(None, script, [t for t, _k in flat], autojunk=False)
    pairs = [(block.a + j, flat[block.b + j][1]) for block in matcher.get_matching_blocks() for j in range(block.size)]
    wanted = [(e, k) for e, k in pairs if first_inside <= e <= last_inside]
    unwanted = [k for e, k in pairs if not first_inside <= e <= last_inside]
    if not wanted:
        return None
    matched = {k for _e, k in pairs}
    missing: list[str] = []
    if edge == "start":
        e0, k0 = min(wanted)
        near = max((k for k in unwanted if k < k0), default=None)
        first = k0
        if e0 > first_inside:                           # script words before the first one heard
            loose = [k for k in range(0 if near is None else near + 1, k0) if k not in matched and said[k]]
            if loose:
                first = loose[0]                        # there, but heard as other words
            else:
                missing = script[first_inside:e0]
        return {"wanted": heard[first], "unwanted": None if near is None else heard[near], "missing": missing}
    e1, k1 = max(wanted)
    near = min((k for k in unwanted if k > k1), default=None)
    last = k1
    if e1 < last_inside:
        loose = [k for k in range(k1 + 1, len(heard) if near is None else near) if k not in matched and said[k]]
        if loose:
            last = loose[-1]
        else:
            missing = script[e1 + 1:last_inside + 1]
    return {"wanted": heard[last], "unwanted": None if near is None else heard[near], "missing": missing}


def _limits(pieces: Sequence[Mapping[str, Any]], number: int) -> tuple[float, float | None]:
    """How far piece `number` may grow: never into the pieces of the same file beside it."""
    piece = pieces[number - 1]
    same = [p for p in pieces if p["episode_id"] == piece["episode_id"] and p is not piece]
    floor = max((p["end"] for p in same if p["end"] <= piece["start"] + 1e-6), default=0.0)
    ceiling = min((p["start"] for p in same if p["start"] >= piece["end"] - 1e-6), default=None)
    return floor, ceiling


def confirm(listening: Listening, sources: Mapping[str, Source], speech: Any, folder: str,
            render: int) -> tuple[dict[tuple[int, str], Move], list[dict[str, Any]]]:
    """Check each word suspicion against the original and work out the fixes. Returns the new
    source edges {(piece, edge): (time, fade, why, room)} and the fixes as the cut reports them;
    cleared suspicions become warnings in `listening.findings`."""
    pieces = listening.pieces
    moves: dict[tuple[int, str], Move] = {}
    report: list[dict[str, Any]] = []
    suspects: dict[tuple[int, str], list[Finding]] = defaultdict(list)
    for finding in listening.findings:
        if finding.kind in WORD_KINDS and finding.severity == "error":
            suspects[(finding.piece, finding.edge or "start")].append(finding)
    targets = list(suspects)[:MAX_PROBES]
    by_source: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for number, edge in targets:
        by_source[pieces[number - 1]["episode_id"]].append((number, edge))
    hints: dict[str, list[tuple[int, str, Heard, Heard | None]]] = defaultdict(list)
    for episode_id, edges_here in by_source.items():
        source = sources[episode_id]
        windows = []
        for number, edge in edges_here:
            at = pieces[number - 1]["start" if edge == "start" else "end"]
            windows.append((max(0.0, at - PROBE), min(source.duration, at + PROBE)))
        clips = engine.render.clips(source.path, windows, folder, name=f"probe{render}-{len(hints)}")
        for (number, edge), (low, high), clip in zip(edges_here, windows, clips, strict=True):
            try:
                heard = [Heard(w.text, low + w.start, low + w.end) for w in words_of(speech, clip, high - low)]
            except (LLMError, Unheard) as exc:
                log.info("probe of piece %s %s failed: %s", number, edge, exc)
                continue
            piece = pieces[number - 1]
            found = judge_edge(source, piece, edge, heard)
            if found is None:
                for finding in suspects[(number, edge)]:
                    finding.detail["unconfirmed"] = True
                continue
            _decide(listening, piece, number, edge, found, suspects[(number, edge)], hints[episode_id])
    for episode_id, wanted in hints.items():
        source = sources[episode_id]
        regions = [_region(edge, word, beside) for _n, edge, word, beside in wanted]
        envs = engine.render.envelope(source.path, [edges.window(target) for target, _lo, _hi in regions])
        for (number, edge, _at, _border), (target, low, high), env in zip(wanted, regions, envs, strict=True):
            piece = pieces[number - 1]
            floor, ceiling = _limits(pieces, number)
            if edge == "start":
                cut = edges.start_cut(env, target, earliest=max(floor, low), latest=min(high, piece["end"] - 0.25))
            else:
                high = high if ceiling is None else min(high, ceiling)
                cut = edges.end_cut(env, target, earliest=max(low, piece["start"] + 0.25), latest=high)
            suspicions = suspects[(number, edge)]
            if abs(cut.time - float(piece[edge])) < 0.02:     # the best cut is the one it has: no clean place here
                for finding in suspicions:
                    finding.detail["stuck"] = True
                continue
            why = suspicions[0].kind
            moves[(number, edge)] = (cut.time, cut.fade, why, cut.room)
            report.append({"render": render, "piece": number, "edge": edge, "episode_id": episode_id,
                           "from": round(float(piece[edge]), 3), "to": round(cut.time, 3), "why": why})
    return moves, report


def _region(edge: str, wanted: Heard, unwanted: Heard | None) -> tuple[float, float, float]:
    """Where to look for the cut between the passage's edge word (`wanted`) and the unwanted word
    heard beside it, as (target, earliest, latest). Normally the gap between them. Where their
    times overlap - the service "ends" one word after the next has begun, as it does where
    speakers overlap - neither time can be trusted: the middle of the pair is, within
    ``OVERLAP``, and the quietest moment there decides."""
    if edge == "start":                          # unwanted words come before the passage
        if unwanted is None:
            return wanted.start, wanted.start - edges.SEARCH_OUT, wanted.start
        if unwanted.end <= wanted.start + SLACK:
            return wanted.start, unwanted.end - 0.02, wanted.start
        middle = (unwanted.start + wanted.end) / 2
    else:                                        # unwanted words come after it
        if unwanted is None:
            return wanted.end, wanted.end, wanted.end + edges.SEARCH_OUT
        if wanted.end <= unwanted.start + SLACK:
            return wanted.end, wanted.end, unwanted.start + 0.02
        middle = (wanted.start + unwanted.end) / 2
    return middle, middle - OVERLAP, middle + OVERLAP


def _decide(listening: Listening, piece: Mapping[str, Any], number: int, edge: str, found: Mapping[str, Any],
            suspicions: list[Finding], hints: list[tuple[int, str, Heard, Heard | None]]) -> None:
    """Confirm or clear the suspicions at one edge, given where the original has its words.
    Where the probe's times for the two words overlap, they can't clear anything: the quietest
    moment around the pair is looked for, and if that is where the cut already is, the finding
    stays, marked ``stuck``."""
    wanted, unwanted = found["wanted"], found["unwanted"]
    cut = float(piece[edge])
    if edge == "start":
        if unwanted is None or unwanted.end <= wanted.start + SLACK:      # a clean gap: the times decide
            wrong = cut > wanted.start - SLACK or (unwanted is not None and cut < unwanted.end - SLACK)
        else:                                                             # overlapping times: the audio decides
            wrong = True
    elif unwanted is None or wanted.end <= unwanted.start + SLACK:
        wrong = cut < wanted.end + SLACK or (unwanted is not None and cut > unwanted.start + SLACK)
    else:
        wrong = True
    if wrong:
        hints.append((number, edge, wanted, unwanted))
        for finding in suspicions:
            finding.detail["confirmed"] = True
        return
    for finding in suspicions:                       # the cut is between the right words
        if finding.kind.startswith("clipped"):
            if found["missing"] and set(found["missing"]) >= set(finding.words):
                finding.kind = "transcript_differs"
            else:
                finding.kind = "stt_missed"
        else:
            finding.kind = "stt_extra"
            finding.heard = " ".join(finding.words)
        finding.severity = "warning"


def silence_moves(listening: Listening) -> tuple[dict[tuple[int, str], Move], list[dict[str, Any]]]:
    """Dead air at a join shrinks to ``edges.TAIL`` + ``edges.LEAD``: the extra silence is cut
    from the source on the side that has it. No speech-to-text needed."""
    pieces = listening.pieces
    moves: dict[tuple[int, str], Move] = {}
    report = []
    for finding in listening.findings:
        if finding.kind != "dead_air" or finding.severity != "error":
            continue
        before, after = finding.detail.get("before", 0.0), finding.detail.get("after", 0.0)
        if finding.edge == "end":                       # silence at the very end of the cut
            targets = [(finding.piece, "end", before - edges.TAIL)]
        else:
            targets = [(finding.piece, "start", after - edges.LEAD)]
            if finding.piece > 1:
                targets.append((finding.piece - 1, "end", before - edges.TAIL))
        for number, edge, excess in targets:
            if excess <= 0.1:
                continue
            piece = pieces[number - 1]
            at = piece["start"] + excess if edge == "start" else piece["end"] - excess
            if piece["end"] - piece["start"] - excess < 0.5:
                continue
            fade = min(float(piece.get("fade_in" if edge == "start" else "fade_out") or edges.MAX_FADE), edges.MAX_FADE)
            moves[(number, edge)] = (round(at, 3), fade, "dead_air", edges.LEAD if edge == "start" else edges.TAIL)
            report.append({"render": listening.render, "piece": number, "edge": edge, "episode_id": piece["episode_id"],
                           "from": round(float(piece[edge]), 3), "to": round(at, 3), "why": "dead_air"})
    return moves, report


def moved(pieces: Sequence[Mapping[str, Any]], moves: Mapping[tuple[int, str], Move]) -> list[dict[str, Any]]:
    out = [dict(p) for p in pieces]
    for (number, edge), (at, fade, why, room) in moves.items():
        piece = out[number - 1]
        piece[edge] = at
        piece["fade_in" if edge == "start" else "fade_out"] = fade
        piece["lead" if edge == "start" else "tail"] = room
        if isinstance(piece.get("edges"), dict) and edge in piece["edges"]:
            piece["edges"] = {**piece["edges"], edge: {**piece["edges"][edge], "cut": round(at, 3), "fade": fade,
                                                       "fixed": why}}
    return out


# ------------------------------------------------------------------ the export


def place(pieces: Sequence[Mapping[str, Any]], sources: Mapping[str, Source],
          cancel: Any = None) -> list[dict[str, Any]]:
    """Every piece's edges moved into the pauses around them, one decode per source file."""
    wanted: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for piece in pieces:
        wanted[piece["episode_id"]] += [edges.window(piece["start"]), edges.window(piece["end"])]
    found: dict[str, list[engine.render.Envelope]] = {}
    for episode_id, windows in wanted.items():
        found[episode_id] = engine.render.envelope(sources[episode_id].path, windows, cancel=cancel)
    out: list[dict[str, Any]] = []
    used: dict[str, int] = defaultdict(int)
    for number, piece in enumerate(pieces, 1):
        k = used[piece["episode_id"]]
        used[piece["episode_id"]] += 1
        start_env, end_env = found[piece["episode_id"]][2 * k], found[piece["episode_id"]][2 * k + 1]
        floor, ceiling = _limits(pieces, number)
        out.append(edges.place(piece, start_env, end_env, sources[piece["episode_id"]].lines,
                               floor=floor, ceiling=ceiling))
    return out


def _rank(listening: Listening) -> tuple[int, int, int]:
    return (len(listening.errors), -int(listening.heard), -listening.render)


def export(pieces: Sequence[Mapping[str, Any]], sources: Mapping[str, Source], out: Path, *, speech: Any,
           tags: Mapping[str, Any] | None = None, cancel: Any = None,
           progress: Progress | None = None) -> tuple[engine.CutResult, dict[str, Any]]:
    """Render `pieces` ({audio, start, end, episode_id, planned_start, planned_end, ...}) into
    `out`, listen to it, fix it by rendering again, and return the kept render and its check."""
    report = progress or (lambda *_a, **_k: None)
    out = Path(out)
    scratch = tempfile.mkdtemp(prefix=".listen-", dir=out.parent)
    tries: list[Listening] = []
    fixes: list[dict[str, Any]] = []
    try:
        report(0.0, "render")
        current = place(pieces, sources, cancel)
        moves: dict[tuple[int, str], Move] = {}
        for render in range(1, MAX_RENDERS + 1):
            current = edges.pace(current)
            covered = None if render == 1 else _relisten(current, moves)
            path = os.path.join(scratch, f"render-{render}.mp3")
            share = (0.0, 0.3) if render == 1 else (0.6 + 0.2 * (render - 2), 0.7 + 0.2 * (render - 2))
            try:
                result = engine.cut(current, path, tags=tags, index=False, cancel=cancel,
                                    progress=lambda f, a=share: report(a[0] + (a[1] - a[0]) * f))
            except engine.Cancelled:
                raise
            except (engine.AudioError, OSError, ValueError):
                if not tries:                           # the first render has to work
                    raise
                log.exception("render %s failed; keeping render %s", render, tries[-1].render)
                break
            report(share[1] + 0.02, "listen" if render == 1 else "fix")
            try:
                listening = listen(render, path, result, current, sources, speech, covered, scratch)
            except engine.Cancelled:
                raise
            except (engine.AudioError, OSError, ValueError):
                log.exception("listening to render %s failed", render)
                listening = Listening(render, path, result, current, [], set(), reason=FAILED)
            if tries and tries[-1].heard and not listening.heard:
                # a fixed render nobody could hear again is not known to be better: keep the last one heard
                log.info("render %s could not be heard (%s); keeping render %s", render, listening.reason,
                         tries[-1].render)
                break
            if tries:
                carry(tries[-1], listening)
            tries.append(listening)
            report(0.6 if render == 1 else share[1] + 0.1)
            if not listening.errors or render == MAX_RENDERS:
                break
            moves = {}
            made: list[dict[str, Any]] = []
            if speech is not None and listening.heard:
                try:
                    moves, made = confirm(listening, sources, speech, scratch, render)
                except engine.Cancelled:
                    raise
                except (engine.AudioError, OSError, ValueError, LLMError):
                    log.exception("checking render %s against the original failed", render)
            silence, quiet = silence_moves(listening)
            for fix in quiet:                          # a word fix at the same edge wins
                if (fix["piece"], fix["edge"]) not in moves:
                    moves[(fix["piece"], fix["edge"])] = silence[(fix["piece"], fix["edge"])]
                    made.append(fix)
            if not moves:
                break
            fixes += made
            current = moved(current, moves)
        best = min(tries, key=_rank)
        os.replace(best.path, out)
        if best.result.path != str(out):
            best.result = engine.CutResult(str(out), best.result.duration, best.result.size_bytes,
                                           best.result.index, best.result.loudness)
        engine.write_index(str(out), best.pieces, best.result.index, best.result.duration)
        return best.result, public(best, tries, fixes)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _relisten(pieces: Sequence[Mapping[str, Any]], moves: Mapping[tuple[int, str], Any]) -> list[tuple[float, float]]:
    """Where the changed edges will sit in the next render, and the stretch around each to hear."""
    index = engine.build_index(pieces)
    windows = []
    for number, edge in moves:
        at = index[number - 1]["cut_start" if edge == "start" else "cut_end"]
        windows.append((max(0.0, at - RELISTEN), at + RELISTEN))
    end = index[-1]["cut_end"] if index else 0.0
    return merge_windows([(a, min(b, end)) for a, b in windows])


def public(best: Listening, tries: Sequence[Listening], fixes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The check as the cut's JSON carries it."""
    if best.errors:
        status = "problems"
    elif not best.heard:
        status = "audio_only"
    else:
        status = "fixed" if any(t.errors for t in tries[:tries.index(best)]) else "passed"
    findings = sorted(best.findings, key=lambda f: (f.cut_time, f.piece))
    return {"status": status, "reason": best.reason, "renders": len(tries), "kept": best.render,
            "joins": max(0, len(best.pieces) - 1),
            "words": {"expected": best.expected, "heard": best.matched} if best.heard else None,
            "findings": [f.public() for f in findings],
            "fixes": [dict(f) for f in fixes if f["render"] < best.render],
            "edges": [{"piece": n, **(p.get("edges") or {})} for n, p in enumerate(best.pieces, 1)]}
