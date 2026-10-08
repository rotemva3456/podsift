"""Choose what to hear, in seconds of the original episode audio.

Keyword mode: `terms` -> `spans_for` -> `budget`, wrapped by `plan`, which returns a `Plan`.
AI mode: a model picks segment-id ranges and `select_ranges` turns them into times; unknown ids
fail, exclusions win, overlaps merge and the budget holds. A model never supplies times.
Either way `snap` then moves each edge onto a whole sentence, never across an exclusion.

No network, no transcription and no file reads happen here. Callers pass timed segments in; an
episode without them comes back in `Plan.needs_timing` instead of being fetched or transcribed.
An excluded interval (a skip match, or a sponsor read the caller passes in) never comes back,
not through merging, padding or widening.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .media import MediaIdentityError, verify_media
from .transcript import Segment, as_segments

STOP_TERMS = frozenset(
    "the a an and or of to in for on with is are was were be been it this that "
    "what how why when i you we they at as by from do does can will not no".split())

Interval = tuple[float, float]


class SelectionError(ValueError):
    """A model's pick referred to something that is not in the transcript."""


def terms(text: str | None) -> list[str]:
    """Search words of a query: lowercased, stop words dropped, `c++`, `802.1q` kept whole."""
    out = []
    for word in re.findall(r"[a-z0-9+#.-]{2,}", (text or "").lower()):
        word = word.strip(".-")
        if len(word) >= 2 and word not in STOP_TERMS:
            out.append(word)
    return out


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, with overlapping or touching intervals joined."""
    out: list[Interval] = []
    for start, end in sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a)):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def subtract(interval: Interval, barriers: Sequence[Interval]) -> list[Interval]:
    """What is left of `interval` after cutting every barrier out of it."""
    pieces = [interval]
    for bar_start, bar_end in barriers:
        remaining = []
        for start, end in pieces:
            if bar_end <= start or bar_start >= end:
                remaining.append((start, end))
                continue
            if start < bar_start:
                remaining.append((start, bar_start))
            if bar_end < end:
                remaining.append((bar_end, end))
        pieces = remaining
    return pieces


def _overlap(start: float, end: float, intervals: Sequence[Interval]) -> float:
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in intervals)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _duration(episode: Mapping[str, Any], segments: Sequence[Segment]) -> float:
    """The episode's length; the last segment's end when the episode does not say."""
    value = _finite(episode.get("duration"))
    if value is not None and value > 0:
        return value
    return max((s.end for s in segments), default=0.0)


def _intervals(raw: Any, duration: float) -> list[Interval]:
    out = []
    for item in raw or ():
        if isinstance(item, Mapping):
            item = (item.get("start"), item.get("end"))
        try:
            start, end = _finite(item[0]), _finite(item[1])
        except (TypeError, IndexError, KeyError):
            continue
        if start is None or end is None:
            continue
        start, end = max(0.0, min(duration, start)), max(0.0, min(duration, end))
        if end > start:
            out.append((start, end))
    return out


def _check_media(episode: Mapping[str, Any]) -> None:
    """Stale timing is refused: the transcript's media must name this episode's audio URL."""
    media = episode.get("media")
    verify_media(media if isinstance(media, Mapping) else None, str(episode.get("audio") or ""))


def _covering(segments: Sequence[Segment], start: float, end: float) -> list[str]:
    return [s.id for s in segments if s.start < end and s.end > start]


def _order_key(span: Mapping[str, Any]) -> tuple[Any, float]:
    return (span.get("order") or 0, float(span["start"]))


def spans_for(query: str, episodes: Sequence[Mapping[str, Any]], *, pad: float = 6.0,
              gap: float = 25.0, drop: str = "", floor: float = 0.004
              ) -> tuple[list[str], list[dict[str, Any]], float]:
    """Pick the segments that answer `query` and merge them into spans.

    Each episode is a mapping with `segments` (Segments or {id?, start, end, text} dicts; parse
    other formats with `transcript.parse` first) and optionally `episode_id`, `duration`,
    `audio`, `media`, `title`, `n`, `order` and `exclude` (intervals to keep out, such as
    sponsor reads). `drop` is a strict exclusion: a segment matching it stays out even when
    nearby matches would otherwise merge across it or pull it back in as padding. Every span is
    clamped to the episode's duration. Spans come back in episode order (`order` when given,
    else the input order; pass `order=n` to sort by episode number), then by start.

    Returns (terms, spans, timed_seconds). A span is a dict: episode_id, audio, start, end,
    score, n, title, text (the first matching segment), media, order and segment_ids.
    """
    want, skip = terms(query), terms(drop)
    spans: list[dict[str, Any]] = []
    timed_secs = 0.0
    for index, episode in enumerate(episodes):
        segments = as_segments(episode.get("segments"))
        duration = _duration(episode, segments)
        if not segments or duration <= 0:
            continue
        _check_media(episode)
        timed_secs += duration
        order = episode.get("order", index)
        keep, excluded = [], _intervals(episode.get("exclude"), duration)
        for seg in segments:
            seg_start = max(0.0, min(duration, seg.start))
            seg_end = max(0.0, min(duration, seg.end))
            if seg_end <= seg_start:
                continue
            low = seg.text.lower()
            score = sum(low.count(t) for t in want)
            if any(low.count(t) for t in skip):
                excluded.append((seg_start, seg_end))
                continue
            if score:
                keep.append((seg_start, seg_end, score, seg.text))
        barriers = merge_intervals(excluded)

        candidates = []
        for core_start, core_end, score, text in keep:
            pieces = subtract((max(0.0, core_start - pad), min(duration, core_end + pad)), barriers)
            core_length = core_end - core_start
            for start, end in pieces:
                overlap = max(0.0, min(end, core_end) - max(start, core_start))
                if overlap <= 0 or end <= start:
                    continue
                candidates.append({
                    "episode_id": episode.get("episode_id"), "audio": episode.get("audio"),
                    "start": start, "end": end, "score": score * overlap / core_length,
                    "n": episode.get("n"), "title": episode.get("title") or "", "text": text,
                    "media": episode.get("media") if isinstance(episode.get("media"), Mapping) else None,
                    "order": order,
                })

        mine: list[dict[str, Any]] = []
        cur = None
        for candidate in sorted(candidates, key=lambda item: item["start"]):
            crosses_exclusion = cur is not None and any(
                start < candidate["start"] and end > cur["end"] for start, end in barriers)
            if cur is not None and candidate["start"] - cur["end"] <= gap and not crosses_exclusion:
                cur["end"] = max(cur["end"], candidate["end"])
                cur["score"] += candidate["score"]
            else:
                cur = candidate
                mine.append(cur)
        for span in mine:
            span["segment_ids"] = _covering(segments, span["start"], span["end"])
        spans.extend(mine)
    # one stray mention inside a long span is not a span about the topic
    spans = [s for s in spans if s["score"] / max(s["end"] - s["start"], 1) >= floor]
    spans.sort(key=_order_key)
    return want, spans, timed_secs


def budget(spans: Sequence[dict[str, Any]], minutes: float | None) -> list[dict[str, Any]]:
    """Keep the densest spans that fit in `minutes` (all of them when minutes is 0 or None)."""
    if not minutes:
        return list(spans)
    ranked = sorted(spans, key=lambda s: -s.get("score", 1.0) / max(s["end"] - s["start"], 1))
    out, total = [], 0.0
    for span in ranked:
        length = span["end"] - span["start"]
        if total + length > minutes * 60:
            continue
        out.append(span)
        total += length
    out.sort(key=_order_key)
    return out


@dataclass
class Plan:
    status: str                                  # "ready" | "needs_timing" | "empty"
    spans: list[dict[str, Any]]
    kept_seconds: float
    source_seconds: float
    omitted: list[dict[str, Any]] = field(default_factory=list)       # {episode_id, start, end, reason}
    needs_timing: list[dict[str, Any]] = field(default_factory=list)  # {episode_id, reason}
    want: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan(want: str, episodes: Sequence[Mapping[str, Any]], *, skip: str = "",
         minutes: float | None = None, pad: float = 6.0, gap: float = 25.0,
         floor: float = 0.004) -> Plan:
    """A keyword plan over one or more episodes (see `spans_for` for the episode fields).

    An episode with no timed segments, stale media, or `timing` == "mismatch" (from
    `align.check_timing`) goes to `needs_timing` with a reason. Spans over the budget go to
    `omitted`. Status: "ready" when anything is kept, else "needs_timing" when an episode is
    waiting for timing, else "empty"."""
    ready, waiting = [], []
    for index, episode in enumerate(episodes):
        episode_id = episode.get("episode_id")
        timing = episode.get("timing")
        if timing is not None and str(timing) == "mismatch":
            waiting.append({"episode_id": episode_id, "reason": getattr(timing, "reason", "")
                            or "The transcript's timing does not match the audio file."})
            continue
        segments = as_segments(episode.get("segments"))
        if not segments:
            waiting.append({"episode_id": episode_id,
                            "reason": "This episode has no timed transcript yet."})
            continue
        try:
            _check_media(episode)
        except MediaIdentityError as exc:
            waiting.append({"episode_id": episode_id, "reason": str(exc)})
            continue
        ready.append({**episode, "segments": segments, "order": episode.get("order", index)})
    want_terms, found, source_secs = spans_for(want, ready, pad=pad, gap=gap, drop=skip, floor=floor)
    kept = budget(found, minutes)
    kept_ids = {id(span) for span in kept}
    omitted = [{"episode_id": s.get("episode_id"), "start": s["start"], "end": s["end"],
                "reason": "over the time budget"} for s in found if id(s) not in kept_ids]
    status = "ready" if kept else ("needs_timing" if waiting else "empty")
    return Plan(status, kept, round(sum(s["end"] - s["start"] for s in kept), 3),
                round(source_secs, 3), omitted, waiting, want_terms, terms(skip))


# ------------------------------------------------------------ model-picked ranges


def _range_ids(raw: Any) -> tuple[str, str, str]:
    if isinstance(raw, Mapping):
        first, last, why = raw.get("start_id"), raw.get("end_id", raw.get("start_id")), raw.get("why")
    elif isinstance(raw, (list, tuple)) and len(raw) in (2, 3):
        first, last, why = raw[0], raw[1], raw[2] if len(raw) == 3 else ""
    else:
        raise SelectionError("Each range needs a start_id and an end_id.")
    if first is None or last is None:
        raise SelectionError("Each range needs a start_id and an end_id.")
    return str(first), str(last), " ".join(str(why or "").split())


def _exclusions(exclude: Iterable[Any], segments: Sequence[Segment], index: Mapping[str, int],
                duration: float) -> list[Interval]:
    out: list[Interval] = []
    for item in exclude or ():
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            if str(item) not in index:
                raise SelectionError(f"Unknown segment id {str(item)!r} in the exclusions.")
            seg = segments[index[str(item)]]
            out.append((seg.start, seg.end))
        else:
            out.extend(_intervals([item], duration))
    return merge_intervals(out)


def select_ranges(segments: Iterable[Any], ranges: Iterable[Any], exclude: Iterable[Any] = (),
                  budget_seconds: float | None = None, *, duration: float | None = None,
                  min_seconds: float = 1.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn segment-id ranges a model picked into spans, for one episode.

    `ranges`: [{start_id, end_id, why?}] or (start_id, end_id[, why]), in the model's order of
    preference. `exclude`: segment ids and/or (start, end) intervals that must stay out.
    `budget_seconds`: the most listening time to keep (None: no limit).

    Unknown ids or a backwards range raise SelectionError. Exclusions are cut out of every
    range. Ranges are taken in order while they fit the budget (overlap with what is already
    kept costs nothing), then merged. Returns (spans, omitted): spans are dicts with start,
    end, segment_ids, why and text (the first segment's); omitted are {start, end, reason}.
    """
    segs = as_segments(segments)
    index: dict[str, int] = {}
    for i, seg in enumerate(segs):
        if seg.id in index:
            raise SelectionError(f"Segment id {seg.id!r} appears twice in the transcript.")
        index[seg.id] = i
    limit = _finite(duration)
    if limit is None or limit <= 0:
        limit = max((s.end for s in segs), default=0.0)
    barriers = _exclusions(exclude, segs, index, limit)

    picked = []
    for raw in ranges or ():
        first, last, why = _range_ids(raw)
        for seg_id in (first, last):
            if seg_id not in index:
                raise SelectionError(f"Unknown segment id {seg_id!r}.")
        lo, hi = index[first], index[last]
        if lo > hi:
            raise SelectionError(f"The range {first!r} to {last!r} runs backwards.")
        chunk = segs[lo:hi + 1]
        start = max(0.0, min(s.start for s in chunk))
        end = min(limit, max(s.end for s in chunk))
        for piece in subtract((start, end), barriers):
            picked.append((piece[0], piece[1], why))

    kept: list[tuple[float, float, str]] = []
    omitted: list[dict[str, Any]] = []
    union: list[Interval] = []
    total = 0.0
    for start, end, why in picked:
        if end - start < min_seconds:
            omitted.append({"start": start, "end": end, "reason": "too short once exclusions are removed"})
            continue
        added = (end - start) - _overlap(start, end, union)
        if budget_seconds is not None and total + added > budget_seconds + 1e-6:
            omitted.append({"start": start, "end": end, "reason": "over the time budget"})
            continue
        kept.append((start, end, why))
        union = merge_intervals(union + [(start, end)])
        total += added

    spans: list[dict[str, Any]] = []
    for start, end, why in sorted(kept):
        if spans and start <= spans[-1]["end"]:
            last = spans[-1]
            last["end"] = max(last["end"], end)
            if why and why not in last["why"].split(" / "):
                last["why"] = f"{last['why']} / {why}" if last["why"] else why
        else:
            spans.append({"start": start, "end": end, "why": why})
    for span in spans:
        span["segment_ids"] = _covering(segs, span["start"], span["end"])
        first_id = span["segment_ids"][0] if span["segment_ids"] else None
        span["text"] = segs[index[first_id]].text if first_id else ""
    return spans, omitted


SNAP_MOVE = 8.0          # the most a snapped edge moves to reach a sentence edge
EDGE = 0.05              # an edge this close to a line's edge is already on it


def snap(start: float, end: float, lines: Sequence[Segment], exclude: Iterable[Interval] = (), *,
         max_move: float = SNAP_MOVE) -> tuple[float, float]:
    """Move a span's edges onto whole sentences (`lines` from `transcript.sentences`).

    An edge inside a sentence moves out to that sentence's start (or end), so the passage keeps
    the whole sentence, when that is at most `max_move` seconds away and crosses no excluded
    interval. Otherwise it moves in to the next sentence edge within `max_move`, leaving the
    part sentence out. When neither works (a sentence longer than 2 x `max_move`, or a skipped
    line right beside it) the edge stays where it is, mid-sentence. An edge in the gap between
    two sentences stays too. Returns (start, end); never an empty span."""
    barriers = merge_intervals(exclude)
    ordered = sorted(lines, key=lambda line: (line.start, line.end))

    def blocked(a: float, b: float) -> bool:
        return any(x < b and y > a for x, y in barriers)

    def inside(t: float) -> list[Segment]:
        return [line for line in ordered if line.start + EDGE < t < line.end - EDGE]

    new_start, new_end = start, end
    holding = inside(start)
    if holding:
        line = max(holding, key=lambda item: item.start)
        if start - line.start <= max_move and not blocked(line.start, start):
            new_start = line.start
        else:
            later = next((item for item in ordered if item.start >= line.end - EDGE), None)
            if later is not None and later.start - start <= max_move and later.start < end - EDGE:
                new_start = later.start
    holding = inside(end)
    if holding:
        line = min(holding, key=lambda item: item.end)
        if line.end - end <= max_move and not blocked(end, line.end):
            new_end = line.end
        else:
            earlier = next((item for item in reversed(ordered) if item.end <= line.start + EDGE), None)
            if earlier is not None and end - earlier.end <= max_move and earlier.end > new_start + EDGE:
                new_end = earlier.end
    if new_end - new_start <= EDGE:
        return start, end
    return new_start, new_end


def widen(spans: Sequence[Mapping[str, Any]], seconds: float, *, duration: float,
          exclude: Iterable[Interval] = ()) -> list[dict[str, Any]]:
    """Add `seconds` of context on both sides of every span of ONE episode, clamped to the
    episode and never into an excluded interval; spans that come to overlap are merged."""
    barriers = merge_intervals(exclude)
    widened: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda s: float(s["start"])):
        start, end = float(span["start"]), float(span["end"])
        for piece in subtract((max(0.0, start - seconds), min(duration, end + seconds)), barriers):
            if piece[0] < end and piece[1] > start:      # the piece that still holds the span
                widened.append({**span, "start": piece[0], "end": piece[1]})
    merged: list[dict[str, Any]] = []
    for span in widened:
        if merged and span["start"] <= merged[-1]["end"]:
            last = merged[-1]
            last["end"] = max(last["end"], span["end"])
            ids = list(last.get("segment_ids") or [])
            ids += [i for i in span.get("segment_ids") or [] if i not in ids]
            if "segment_ids" in last or ids:
                last["segment_ids"] = ids
        else:
            merged.append(dict(span))
    return merged
