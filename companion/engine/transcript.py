"""One transcript type for every source.

A `Segment` is a run of speech with its `start` and `end` in seconds from the start of the
episode's audio file. It is built from PodFetch segments, `.timed.json` library files, WebVTT,
SRT or Podcasting 2.0 JSON. Every builder drops NaN, negative and reversed times; pass
`duration` to also clamp to the length of the file.

Transcripts are untrusted text. Nothing here executes or interprets them.
"""
from __future__ import annotations

import bisect
import html
import json
import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# A word-level source (one word per entry) is regrouped into phrases no longer than this.
GROUP_MAX_SECONDS = 15.0
GROUP_MAX_GAP = 1.5
# A cut script's line is one sentence; one that never ends (no punctuation) stops growing here.
LINE_MAX_SECONDS = 30.0
# When a source gives no end for the last segment, assume this speaking rate.
SECONDS_PER_WORD, TAIL_MAX = 0.4, 15.0


class TranscriptError(ValueError):
    """The transcript could not be read at all."""


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Segment:
    id: str
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    speaker: str | None = None

    def to_dict(self, words: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "start": self.start, "end": self.end, "text": self.text}
        if self.speaker:
            out["speaker"] = self.speaker
        if words and self.words:
            out["words"] = [{"text": w.text, "start": w.start, "end": w.end} for w in self.words]
        return out


def hms(seconds: float | None) -> str:
    """12:05, or 1:02:05 past an hour."""
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _num(value: Any) -> float | None:
    """A finite float, or None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _limit(duration: Any) -> float | None:
    number = _num(duration)
    return number if number is not None and number > 0 else None


def _pos_id(index: int) -> str:
    """Positional id, stable while the source file is unchanged (same scheme as the library)."""
    return f"seg-{index + 1:04d}"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _speaker(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _words(raw: Any) -> tuple[Word, ...]:
    out = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, Mapping):
            continue
        start, end = _num(item.get("start")), _num(item.get("end"))
        text = _text(item.get("word") if item.get("word") is not None else item.get("text"))
        if start is not None and end is not None and text:
            out.append(Word(text, start, end))
    return tuple(out)


def _clean_word(word: Word, limit: float | None) -> Word | None:
    start, end = _num(word.start), _num(word.end)
    if start is None or end is None or start < 0 or end < start or not word.text:
        return None
    if limit is not None:
        if start >= limit:
            return None
        end = min(end, limit)
    return Word(word.text, start, end)


def clean(segments: Iterable[Segment], duration: float | None = None) -> list[Segment]:
    """Drop NaN, negative, reversed or empty segments; clamp to `duration`; sort by start."""
    limit = _limit(duration)
    out = []
    for seg in segments:
        start, end = _num(seg.start), _num(seg.end)
        if start is None or end is None or start < 0 or end <= start:
            continue
        if limit is not None:
            if start >= limit:
                continue
            end = min(end, limit)
        text = _text(seg.text)
        if not text:
            continue
        words = tuple(w for w in (_clean_word(w, limit) for w in seg.words) if w)
        out.append(Segment(str(seg.id), start, end, text, words, seg.speaker))
    out.sort(key=lambda s: (s.start, s.end))
    return out


def as_segments(items: Iterable[Any] | None, duration: float | None = None) -> list[Segment]:
    """Segments from `Segment`s or plain dicts ({id?, start, end, text, words?, speaker?}).

    A dict without an id gets the positional id `seg-0001`, `seg-0002`, ..."""
    segs = []
    for index, item in enumerate(items or ()):
        if isinstance(item, Segment):
            segs.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        start, end = _num(item.get("start")), _num(item.get("end"))
        if start is None or end is None:
            continue
        raw_id = item.get("id")
        seg_id = str(raw_id) if raw_id not in (None, "") else _pos_id(index)
        segs.append(Segment(seg_id, start, end, _text(item.get("text")),
                            _words(item.get("words")), _speaker(item.get("speaker"))))
    return clean(segs, duration)


def plain_text(segments: Iterable[Segment]) -> str:
    return "\n".join(seg.text for seg in segments)


def _tail_end(start: float, text: str) -> float:
    """End of a final segment whose source gives none: a normal speaking rate, capped."""
    return start + min(TAIL_MAX, max(1.0, SECONDS_PER_WORD * len(text.split())))


def _fill_ends(rows: list[tuple[str, float, float | None, str, str | None]]) -> list[Segment]:
    """A missing end means the next segment's start."""
    segs = []
    for i, (seg_id, start, end, text, speaker) in enumerate(rows):
        if end is None:
            end = rows[i + 1][1] if i + 1 < len(rows) else _tail_end(start, text)
        segs.append(Segment(seg_id, start, end, text, (), speaker))
    return segs


def looks_word_level(segments: Sequence[Segment]) -> bool:
    """True when most entries hold one or two words (Podcasting 2.0 JSON often does)."""
    if len(segments) < 8:
        return False
    return statistics.median(len(s.text.split()) for s in segments) <= 2


_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")


def group_words(segments: Sequence[Segment], max_seconds: float = GROUP_MAX_SECONDS,
                max_gap: float = GROUP_MAX_GAP) -> list[Segment]:
    """Join word-level entries into phrases. Each phrase keeps its words and takes the id of
    its first entry. A phrase ends at a speaker change, a pause, a sentence end (once it is 2 s
    long) or `max_seconds`."""
    groups: list[list[Segment]] = []
    for seg in segments:
        cur = groups[-1] if groups else None
        if cur is not None:
            first, last = cur[0], cur[-1]
            if not (seg.speaker != last.speaker or seg.start - last.end > max_gap
                    or seg.end - first.start > max_seconds
                    or (_SENTENCE_END.search(last.text) and last.end - first.start >= 2.0)):
                cur.append(seg)
                continue
        groups.append([seg])
    out = []
    for group in groups:
        words = tuple(w for s in group for w in (s.words or (Word(s.text, s.start, s.end),)))
        out.append(Segment(group[0].id, group[0].start, max(s.end for s in group),
                           " ".join(s.text for s in group), words, group[0].speaker))
    return out


def _maybe_group(segs: list[Segment], group: bool) -> list[Segment]:
    return group_words(segs) if group and looks_word_level(segs) else segs


def _own_words(seg: Segment) -> list[Word]:
    """The segment's words, without neighbours' words that `from_timed_json` attached to it
    because they overlap its edge."""
    return [w for w in seg.words if seg.start - 0.05 <= (w.start + w.end) / 2 <= seg.end + 0.05]


def _spelling(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))


def _split_sentences(seg: Segment) -> list[Segment]:
    """One segment cut where a word ends a sentence. Only with word times, and only when its
    words spell exactly its text: where speakers overlap, Whisper's word times overlap too, a
    neighbour's word lands in the segment and one of its own falls out (measured on N4N064 at
    68:23, "Thank you. We're going..."), and a split on those words would drop "We're"."""
    words = _own_words(seg)
    if (len(words) < 2 or not any(_SENTENCE_END.search(w.text) for w in words[:-1])
            or _spelling(" ".join(w.text for w in words)) != _spelling(seg.text)):
        return [seg]
    groups: list[list[Word]] = [[]]
    for word in words:
        groups[-1].append(word)
        if _SENTENCE_END.search(word.text):
            groups.append([])
    groups = [g for g in groups if g]
    out = []
    for k, group in enumerate(groups):
        start = seg.start if k == 0 else group[0].start
        end = seg.end if k == len(groups) - 1 else group[-1].end
        if end > start:
            out.append(Segment(seg.id if k == 0 else f"{seg.id}.{k + 1}", start, end,
                               " ".join(w.text for w in group), tuple(group), seg.speaker))
    return out or [seg]


def spread_words(start: float, end: float, text: str) -> list[tuple[str, float]]:
    """A line's words, each with the middle of an even share of the line's time. Where a cut
    falls inside a line (an edge that couldn't reach a sentence edge), the plan's script and the
    listen-back check both split the line with this, so they agree on every word."""
    words = text.split()
    if not words:
        return []
    step = (end - start) / len(words)
    return [(word, start + step * (k + 0.5)) for k, word in enumerate(words)]


def sentence_pieces(segments: Sequence[Segment], max_seconds: float = LINE_MAX_SECONDS,
                    max_gap: float = GROUP_MAX_GAP) -> list[tuple[Segment, int]]:
    """The segments cut where a word ends a sentence, each with the number of the sentence it
    belongs to (see `sentences`). A plan keeps these: a passage that must start mid-sentence (a
    skipped line right before it) then still shows exactly the words it keeps."""
    out: list[tuple[Segment, int]] = []
    number, first, line_end = -1, 0.0, 0.0
    last: Segment | None = None
    for piece in (p for seg in segments for p in _split_sentences(seg)):
        if (last is not None and not _SENTENCE_END.search(last.text) and piece.start - line_end <= max_gap
                and piece.speaker == last.speaker and piece.end - first <= max_seconds):
            line_end = max(line_end, piece.end)
        else:
            number, first, line_end = number + 1, piece.start, piece.end
        out.append((piece, number))
        last = piece
    return out


def join_pieces(pieces: Sequence[tuple[Segment, int]]) -> list[Segment]:
    """Pieces with the same sentence number as one line: the first piece's id, all the words."""
    lines: list[Segment] = []
    numbers: list[int] = []
    for piece, number in pieces:
        if numbers and numbers[-1] == number:
            last = lines[-1]
            lines[-1] = Segment(last.id, last.start, max(last.end, piece.end), f"{last.text} {piece.text}",
                                last.words + piece.words, last.speaker)
        else:
            lines.append(piece)
            numbers.append(number)
    return lines


def sentences(segments: Sequence[Segment], max_seconds: float = LINE_MAX_SECONDS,
              max_gap: float = GROUP_MAX_GAP) -> list[Segment]:
    """The lines of a cut script: whole sentences, so a passage can start and end on one.

    A segment with word times is split where a word ends a sentence (. ? !); a segment that
    doesn't end one is joined with the next, until a sentence ends, a pause longer than
    `max_gap`, a speaker change, or `max_seconds`. Transcripts without punctuation therefore
    give lines of up to `max_seconds`, still on segment edges. Each line keeps its words and
    takes the id of the segment it starts in (".2", ".3" for the later parts of a split one)."""
    return join_pieces(sentence_pieces(segments, max_seconds, max_gap))


# ----------------------------------------------------------------------- sources


def from_podfetch(data: Mapping[str, Any] | Sequence[Any] | None, duration: float | None = None,
                  *, group: bool = True) -> list[Segment]:
    """PodFetch's transcript segments ({idx, startMs, endMs, speaker, text}). A segment with no
    start is untimed and skipped; one with no end runs to the next segment's start."""
    raw = data.get("segments") if isinstance(data, Mapping) else data
    rows = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        start_ms = _num(item.get("startMs"))
        if start_ms is None:
            continue
        end_ms = _num(item.get("endMs"))
        idx = item.get("idx")
        rows.append((str(idx) if idx is not None else _pos_id(len(rows)), start_ms / 1000.0,
                     None if end_ms is None else end_ms / 1000.0, _text(item.get("text")),
                     _speaker(item.get("speaker"))))
    return _maybe_group(clean(_fill_ends(rows), duration), group)


def _assign_words(words: tuple[Word, ...], start: float, end: float,
                  starts: list[float], longest: float) -> tuple[Word, ...]:
    lo = bisect.bisect_left(starts, start - longest)
    hi = bisect.bisect_left(starts, end)
    return tuple(w for w in words[lo:hi] if w.start < end and w.end > start)


def from_timed_json(doc: Mapping[str, Any] | str | bytes, duration: float | None = None) -> list[Segment]:
    """A library `.timed.json` ({duration, media, segments: [{start, end, text, words?}], words?}).

    Top-level words are attached to every segment they overlap. `duration` defaults to the
    file's own; pass the audio's real length to clamp to it instead."""
    if isinstance(doc, (str, bytes)):
        doc = _load_json(doc)
    if not isinstance(doc, Mapping):
        raise TranscriptError("A timed transcript must be a JSON object.")
    limit = duration if duration is not None else doc.get("duration")
    top = tuple(sorted(_words(doc.get("words")), key=lambda w: w.start))
    starts = [w.start for w in top]
    longest = max((w.end - w.start for w in top), default=0.0)
    segs = []
    for index, item in enumerate(doc.get("segments") or []):
        if not isinstance(item, Mapping):
            continue
        start, end = _num(item.get("start")), _num(item.get("end"))
        if start is None or end is None:
            continue
        words = _words(item.get("words"))
        if not words and top:
            words = _assign_words(top, start, end, starts, longest)
        segs.append(Segment(_pos_id(index), start, end, _text(item.get("text")), words,
                            _speaker(item.get("speaker"))))
    return clean(segs, limit)


_CUE_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)$")
_VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")
_TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")


def cue_seconds(text: str) -> float | None:
    """`01:02:03.456`, `02:03.456` or `02:03,456` in seconds; None when it is not a time."""
    match = _CUE_TIME.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))


def _cue_text(lines: list[str]) -> tuple[str, str | None]:
    raw = " ".join(lines)
    voice = _VOICE.search(raw)
    text = html.unescape(_TAG.sub(" ", raw)).replace("‎", "").replace("‏", "")
    return _text(text), (_speaker(voice.group(1)) if voice else None)


def _parse_cues(body: str | bytes, duration: float | None) -> list[Segment]:
    """WebVTT and SRT share one reader. A timing line always opens a new cue, so a file
    without blank lines between cues still parses, and a cue number or NOTE line after a blank
    line is never glued onto the previous cue's text."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    lines = body.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[int, float | None, float | None, list[str]]] = []
    payload = False
    for line in lines:
        line = line.strip()
        if not line:
            payload = False
            continue
        if "-->" in line:
            left, _, right = line.partition("-->")
            fields = right.split()
            cues.append((len(cues), cue_seconds(left), cue_seconds(fields[0]) if fields else None, []))
            payload = True
        elif payload and cues:
            cues[-1][3].append(line)
    segs = []
    for number, start, end, text_lines in cues:
        if start is None or end is None:
            continue                                   # a broken timestamp is skipped, never 0:00
        text, speaker = _cue_text(text_lines)
        segs.append(Segment(_pos_id(number), start, end, text, (), speaker))
    return clean(segs, duration)


def parse_vtt(body: str | bytes, duration: float | None = None) -> list[Segment]:
    return _parse_cues(body, duration)


def parse_srt(body: str | bytes, duration: float | None = None) -> list[Segment]:
    return _parse_cues(body, duration)


def _load_json(body: str | bytes) -> Any:
    try:
        return json.loads(body)
    except (TypeError, ValueError) as exc:
        raise TranscriptError("The transcript is not valid JSON.") from exc


def parse_json(body: Mapping[str, Any] | str | bytes, duration: float | None = None,
               *, group: bool = True) -> list[Segment]:
    """Podcasting 2.0 JSON ({segments: [{speaker, startTime, endTime, body}]}). Word-level
    files are regrouped into phrases that keep their words."""
    doc = _load_json(body) if isinstance(body, (str, bytes)) else body
    if not isinstance(doc, Mapping):
        raise TranscriptError("A JSON transcript must be an object with segments.")
    rows = []
    for index, item in enumerate(doc.get("segments") or []):
        if not isinstance(item, Mapping):
            continue
        start = _num(item.get("startTime"))
        if start is None:
            continue
        rows.append((_pos_id(index), start, _num(item.get("endTime")), _text(item.get("body")),
                     _speaker(item.get("speaker"))))
    return _maybe_group(clean(_fill_ends(rows), duration), group)


def sniff(body: str | bytes | Mapping[str, Any] | Sequence[Any], content_type: str | None = None) -> str:
    """The format of a transcript: vtt, srt, json (Podcasting 2.0), podfetch, timed or text."""
    if isinstance(body, Mapping) or (isinstance(body, Sequence) and not isinstance(body, (str, bytes))):
        return _json_kind(body)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    head = text.lstrip("﻿ \t\r\n")
    kind = (content_type or "").split(";")[0].strip().lower()
    if head.startswith("WEBVTT") or kind in ("text/vtt", "application/vtt"):
        return "vtt"
    if head[:1] and head[:1] in "{[":
        try:
            return _json_kind(json.loads(head))
        except ValueError:
            return "text"
    if "-->" in head[:2000] or kind in ("application/srt", "text/srt", "application/x-subrip"):
        return "srt"
    return "text"


def _json_kind(doc: Any) -> str:
    items = doc.get("segments") if isinstance(doc, Mapping) else doc
    first = next((item for item in items or () if isinstance(item, Mapping)), {})
    if "startMs" in first or "idx" in first:
        return "podfetch"
    if "startTime" in first or "body" in first:
        return "json"
    return "timed"


def parse(body: str | bytes | Mapping[str, Any] | Sequence[Any], fmt: str | None = None, *,
          duration: float | None = None, content_type: str | None = None,
          group: bool = True) -> list[Segment]:
    """Any supported transcript to segments. Plain text has no timing and gives []."""
    fmt = fmt or sniff(body, content_type)
    if fmt == "vtt":
        return parse_vtt(body, duration)  # type: ignore[arg-type]
    if fmt == "srt":
        return parse_srt(body, duration)  # type: ignore[arg-type]
    doc = _load_json(body) if isinstance(body, (str, bytes)) and fmt != "text" else body
    if fmt == "podfetch":
        return from_podfetch(doc, duration, group=group)  # type: ignore[arg-type]
    if fmt == "json":
        return parse_json(doc, duration, group=group)  # type: ignore[arg-type]
    if fmt == "timed":
        return from_timed_json(doc, duration)  # type: ignore[arg-type]
    if fmt == "text":
        return []
    raise TranscriptError(f"Unknown transcript format: {fmt}")
