"""Did the cut come out as planned? What speech-to-text hears in the rendered MP3, compared
with the plan's script, and the MP3's loudness at every join.

Each piece of a cut is one unbroken stretch of the source file, so no word inside a piece can
go missing that the source has. A cut goes wrong at its edges: a first or last word clipped,
a word of the part you cut leaking in, or seconds of silence where two pieces meet. So the
check looks hard at the edges - the first and last words of every piece, anything heard
between two pieces, the silence at every join - and counts the words inside a piece only to
catch a piece taken from the wrong place (a transcript whose timing doesn't fit the file).

Speech-to-text is not the transcript: it drops a word at a hard start, writes "ok" for
"okay", hears a number differently. Fillers (um, uh) are ignored on both sides, words are
compared after `tokens` normalises them, and every edge problem found here is a suspicion:
`companion.listen` checks it against the original audio before it re-cuts anything.

No I/O here: callers pass in what was heard, and envelopes of the rendered file.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .edges import QUIET, quiet_runs, speech_level
from .render import Envelope
from .transcript import hms, spread_words

FILLERS = frozenset("um umm uh uhm uhh er erm ah ahh hmm hm mm mhm".split())
_NUMBERS = {word: str(number) for number, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUMBERS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                 "eighty": "80", "ninety": "90", "hundred": "100", "okay": "ok", "alright": "allright"})

EDGE_SECONDS = 6.0     # the part of a piece that counts as its edge ...
EDGE_WORDS = 4         # ... and at least this many of its words
DEAD_AIR = 1.0         # more silence than this where two pieces meet is dead air
JOIN_WINDOW = 5.0      # envelope seconds each side of a join
WRONG_AUDIO = 0.5      # a piece whose words are heard less than this is from the wrong place ...
LOW_MATCH = 0.8        # ... and under this, the transcript and the audio differ in wording
MIN_WORDS = 10         # a piece needs this many words before its match rate means anything
SHOWN = 8              # words quoted in a finding


def tokens(text: str) -> list[str]:
    """Words for comparing, not for reading: lower case, "we've" -> "weve", number words up to
    twenty (and the tens) as digits, "okay" -> "ok", and no fillers."""
    text = re.sub(r"(?<=[a-z0-9])['’](?=[a-z])", "", (text or "").lower())
    return [word for word in (_NUMBERS.get(w, w) for w in re.findall(r"[a-z0-9]+", text)) if word not in FILLERS]


@dataclass(frozen=True)
class Heard:
    """One word speech-to-text heard; times in seconds of the file it heard."""
    text: str
    start: float
    end: float


@dataclass
class Piece:
    """One piece of a rendered cut, as the check sees it. `words` are the script's tokens for
    it with their approximate time in the SOURCE file (evenly spread over each line)."""
    number: int                              # 1-based, in play order
    episode_id: str
    cut_start: float
    cut_end: float
    source_start: float                      # where this piece's audio starts in the source
    words: list[tuple[str, float]]

    def at(self, source_time: float) -> float:
        """A source time as a time in the cut."""
        return self.cut_start + (source_time - self.source_start)

    def source(self, cut_time: float) -> float:
        return self.source_start + (cut_time - self.cut_start)


@dataclass
class Finding:
    """One thing the check found. kind:
    errors    clipped_start / clipped_end   the piece's first / last words aren't heard
              extra_start / extra_end       words of the part you cut are heard at its edge
              dead_air                      a long silence where two pieces meet (or at either end)
              wrong_audio                   the piece doesn't sound like its script at all
    warnings  no_pause        the join goes through sound: the source has no pause there
              low_match       inside a piece, wording differs between transcript and audio
              stt_missed / stt_extra / transcript_differs   an edge suspicion the original
                              audio cleared (set by companion.listen)
    `piece` is 1-based; a join's finding belongs to the piece after it (`edge` "start")."""
    kind: str
    severity: str
    piece: int
    edge: str | None
    cut_time: float
    words: tuple[str, ...] = ()
    seconds: float = 0.0
    heard: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[int, str | None, str]:
        """Which edge (or join) this is about, whatever its kind."""
        group = "audio" if self.kind in ("dead_air", "no_pause") else "words"
        return self.piece, self.edge, group

    def public(self) -> dict[str, Any]:
        out = {"kind": self.kind, "severity": self.severity, "piece": self.piece, "edge": self.edge,
               "cut_time": round(self.cut_time, 3), "words": list(self.words), "seconds": round(self.seconds, 2),
               "heard": self.heard, "message": message(self)}
        out.update({k: v for k, v in self.detail.items()
                    if k in ("source_time", "episode_id", "span_ids", "stuck", "confirmed")})
        return out


def message(finding: Finding) -> str:
    """Plain English for a finding (the UI words its own from `kind`)."""
    text = _message(finding)
    if finding.detail.get("stuck"):
        text += " There is no clean place to cut here: the words run together or the speakers overlap."
    return text


def _message(finding: Finding) -> str:
    words = " ".join(finding.words)
    where = hms(finding.cut_time)
    n = finding.piece
    return {
        "clipped_start": f"Passage {n} starts too late at {where}: “{words}” is cut off.",
        "clipped_end": f"Passage {n} ends too soon at {where}: “{words}” is cut off.",
        "extra_start": f"Passage {n} starts too early at {where}: it plays “{words}” from the part you cut.",
        "extra_end": f"Passage {n} runs on at {where}: it plays “{words}” from the part you cut.",
        "dead_air": f"{finding.seconds:.1f} s of silence at {where}.",
        "wrong_audio": f"At {where} passage {n} doesn't sound like its script ({finding.detail.get('heard_share', 0):.0%} "
                       "of its words heard): the transcript may not fit this audio file.",
        "no_pause": f"No pause in the recording where passage {n} starts ({where}); it's cut at the quietest moment.",
        "low_match": f"Passage {n}: speech-to-text heard {finding.detail.get('heard_share', 0):.0%} of the planned words; "
                     "the rest differ in wording, not in audio.",
        "stt_missed": f"Speech-to-text didn't hear “{words}” at {where}, but the original audio there is intact.",
        "stt_extra": f"Speech-to-text heard “{finding.heard}” at {where}, but the original has no such words there.",
        "transcript_differs": f"The transcript says “{words}” at {where}; speech-to-text doesn't hear it in the "
                              "original either, so the cut is right.",
    }.get(finding.kind, finding.kind)


@dataclass
class Result:
    """What one listen-back found."""
    findings: list[Finding]
    expected: int = 0            # script words in the pieces that were fully heard
    matched: int = 0             # ... and how many of them speech-to-text heard
    judged: set[tuple[int, str]] = field(default_factory=set)    # (piece, "start"|"end") edges it judged

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]


def expected_words(lines: Iterable[Any], start: float, end: float) -> list[tuple[str, float]]:
    """The script's tokens for a piece planned as [start, end] of the source, each timed at the
    middle of its word's even share of its line (`transcript.spread_words`): whole lines inside,
    and of a line the piece only partly holds, just the words the plan's script shows in it.
    `lines` are Segments or (start, end, text) rows."""
    out: list[tuple[str, float]] = []
    for line in lines:
        a, b, text = (line.start, line.end, line.text) if hasattr(line, "text") else line
        if b < start - 0.05 or a > end + 0.05:
            continue
        for word, middle in spread_words(a, b, text):
            if start - 0.05 <= middle <= end + 0.05:
                out += [(token, middle) for token in tokens(word)]
    return out


def _covered(covered: Sequence[tuple[float, float]], a: float, b: float) -> bool:
    return any(x <= a + 0.01 and b <= y + 0.01 for x, y in covered)


def _inside(covered: Sequence[tuple[float, float]], t: float) -> bool:
    return any(x <= t <= y for x, y in covered)


def _edge_count(piece: Piece, head: bool) -> int:
    if not piece.words:
        return 0
    if head:
        limit = piece.words[0][1] + EDGE_SECONDS
        count = sum(1 for _token, t in piece.words if t <= limit)
    else:
        limit = piece.words[-1][1] - EDGE_SECONDS
        count = sum(1 for _token, t in piece.words if t >= limit)
    return min(len(piece.words), max(EDGE_WORDS, count))


def check_words(pieces: Sequence[Piece], heard: Sequence[Heard],
                covered: Sequence[tuple[float, float]]) -> Result:
    """Compare what was heard (times in the cut) with each piece's script, within the `covered`
    stretches of the cut that speech-to-text listened to. Edges outside them are not judged.

    `heard` must be in the order speech-to-text gave it. Its ORDER is what it gets right; its
    word times are not: where speakers overlap it can end a word after the next one has started
    ("area." 2043.40-2044.18, then "It" from 2043.20, measured on N4N032). So nothing here
    re-sorts by time, and words between two pieces are found by their place in what was heard."""
    heard = list(heard)
    said = [tokens(w.text) for w in heard]
    flat: list[tuple[str, int]] = [(token, k) for k, parts in enumerate(said) for token in parts
                                   if _inside(covered, (heard[k].start + heard[k].end) / 2)]
    # ONE alignment of the whole script with everything heard, in order: a word heard at the end
    # of one piece can never also stand in for the same word at the start of the next
    script = [(token, piece.number, i) for piece in pieces for i, (token, t) in enumerate(piece.words)
              if _inside(covered, piece.at(t))]
    matcher = difflib.SequenceMatcher(None, [token for token, _n, _i in script], [token for token, _k in flat],
                                      autojunk=False)
    matches: dict[int, list[tuple[int, int]]] = {piece.number: [] for piece in pieces}
    claimed: set[int] = set()
    for block in matcher.get_matching_blocks():
        for j in range(block.size):
            _token, number, index = script[block.a + j]
            word = flat[block.b + j][1]
            matches[number].append((index, word))
            claimed.add(word)
    listened = {k for _token, k in flat}
    loose = [k for k in range(len(heard)) if k in listened and k not in claimed and said[k]]

    def between(low: int, high: int, keep: Any) -> list[int]:
        """Unclaimed words heard after position `low` and before `high`, on this piece's side."""
        middle = [k for k in loose if low < k < high]
        return [k for k in middle if keep((heard[k].start + heard[k].end) / 2)]

    findings: list[Finding] = []
    judged: set[tuple[int, str]] = set()
    expected = matched = 0
    end_of_cut = max((p.cut_end for p in pieces), default=0.0)
    for n, piece in enumerate(pieces):
        pairs = matches[piece.number]
        if not piece.words:
            continue
        whole = _covered(covered, piece.cut_start, piece.cut_end)
        if whole:
            expected += len(piece.words)
            matched += len({e for e, _k in pairs})
        if whole and len(piece.words) >= MIN_WORDS:
            share = len({e for e, _k in pairs}) / len(piece.words)
            if share < WRONG_AUDIO:
                findings.append(Finding("wrong_audio", "error", piece.number, None, piece.cut_start,
                                        detail={"heard_share": round(share, 2)}))
                continue                       # its edges can't be judged against a wrong script
            if share < LOW_MATCH:
                findings.append(Finding("low_match", "warning", piece.number, None, piece.cut_start,
                                        detail={"heard_share": round(share, 2)}))
        before = matches[pieces[n - 1].number] if n > 0 else []
        after = matches[pieces[n + 1].number] if n + 1 < len(pieces) else []
        for head in (True, False):
            edge_time = piece.cut_start if head else piece.cut_end
            span = (edge_time - 2.0, edge_time + EDGE_SECONDS) if head else (edge_time - EDGE_SECONDS, edge_time + 2.0)
            if not whole and not _covered(covered, max(0.0, span[0]), min(end_of_cut, span[1])):
                continue
            judged.add((piece.number, "start" if head else "end"))
            extra: list[int] = []
            if pairs and head:
                low = max(k for _e, k in before) if before else -1
                extra = between(low, min(k for _e, k in pairs), lambda t, j=edge_time: t >= j - 0.1)
            elif pairs:
                high = min(k for _e, k in after) if after else len(heard)
                extra = between(max(k for _e, k in pairs), high, lambda t, j=edge_time: t <= j + 0.1)
            findings += _edge(piece, head, pairs, heard, said, extra)
    return Result(findings, expected, matched, judged)


def _edge(piece: Piece, head: bool, pairs: list[tuple[int, int]], heard: Sequence[Heard],
          said: Sequence[list[str]], extra: list[int]) -> list[Finding]:
    count = _edge_count(piece, head)
    edge_time = piece.cut_start if head else piece.cut_end
    edge_indices = range(count) if head else range(len(piece.words) - count, len(piece.words))
    in_edge = [(e, k) for e, k in pairs if e in edge_indices]
    out: list[Finding] = []
    if head:
        first = min(in_edge)[0] if in_edge else count
        missing = [piece.words[i][0] for i in range(first)]
    else:
        last = max(in_edge)[0] if in_edge else len(piece.words) - count - 1
        missing = [piece.words[i][0] for i in range(last + 1, len(piece.words))]
    context = " ".join(w.text for w in heard if abs((w.start if head else w.end) - edge_time) <= 3.0)[:160]
    source_time = piece.source(edge_time)
    detail = {"source_time": round(source_time, 3), "episode_id": piece.episode_id}
    if missing:
        shown = tuple(missing[:SHOWN]) if head else tuple(missing[-SHOWN:])
        out.append(Finding("clipped_start" if head else "clipped_end", "error", piece.number,
                           "start" if head else "end", edge_time, shown, heard=context,
                           detail={**detail, "missing": len(missing)}))
    if extra:
        words = tuple(t for k in extra for t in said[k])
        out.append(Finding("extra_start" if head else "extra_end", "error", piece.number,
                           "start" if head else "end", edge_time, words[:SHOWN], heard=context,
                           detail={**detail, "extra_times": [(heard[k].start, heard[k].end) for k in extra]}))
    return out


def check_joins(pieces: Sequence[Piece], envelopes: Mapping[int, Envelope], duration: float) -> list[Finding]:
    """Silence where pieces meet, from the rendered file's loudness: `envelopes[n]` covers the
    join before piece n (n >= 2), `envelopes[1]` the cut's start and `envelopes[0]` its end."""
    out: list[Finding] = []
    for piece in pieces:
        env = envelopes.get(piece.number)
        if env is None or not env.levels:
            continue
        join = piece.cut_start
        threshold = speech_level(env.levels) - QUIET
        runs = quiet_runs(env, env.start, env.end, threshold)
        here = next((r for r in runs if r[0] - 0.05 <= join <= r[1] + 0.05), None)
        if here is None:
            if piece.number > 1:
                out.append(Finding("no_pause", "warning", piece.number, "start", join,
                                   detail={"source_time": round(piece.source_start, 3), "episode_id": piece.episode_id}))
            continue
        before, after = max(0.0, join - here[0]), max(0.0, here[1] - join)
        if before + after > DEAD_AIR:
            out.append(Finding("dead_air", "error", piece.number, "start", join, seconds=before + after,
                               detail={"before": round(before, 3), "after": round(after, 3),
                                       "source_time": round(piece.source_start, 3), "episode_id": piece.episode_id}))
    last = pieces[-1] if pieces else None
    env = envelopes.get(0)
    if last is not None and env is not None and env.levels:
        runs = quiet_runs(env, env.start, env.end, speech_level(env.levels) - QUIET)
        tail = next((r for r in runs if r[1] >= min(duration, env.end) - 0.05), None)
        if tail is not None and tail[1] - tail[0] > DEAD_AIR:
            out.append(Finding("dead_air", "error", last.number, "end", tail[0], seconds=tail[1] - tail[0],
                               detail={"before": round(tail[1] - tail[0], 3), "after": 0.0,
                                       "source_time": round(last.source(tail[0]), 3), "episode_id": last.episode_id}))
    return out
