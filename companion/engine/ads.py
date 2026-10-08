"""Sponsor reads and teaching density, from the transcript alone.

Sponsor detection is shared with the native player. Explicit promotional
evidence, rather than a vendor name or teaching vocabulary, identifies an ad.
"""
from __future__ import annotations

import collections
import re
from collections.abc import Iterable, Sequence
from typing import Any

from .transcript import Segment, as_segments
from .skipping import MAX_BLOCK as AD_MAX_BLOCK, MAX_GAP as AD_GAP, skip_spans


def ad_spans(segments: Iterable[Any]) -> list[tuple[float, float]]:
    """Paid sponsor reads as (start, end), from Podsift's native detector."""
    return [(span.start, span.end) for span in skip_spans(segments) if span.category == "sponsor"]


def ad_segment_ids(segments: Iterable[Any]) -> set[str]:
    """Ids of the segments that fall inside a sponsor read."""
    segs = as_segments(segments)
    ads = ad_spans(segs)
    return {s.id for s in segs if any(s.start < end and s.end > start for start, end in ads)}


def _tok(text: str | None) -> list[str]:
    return re.findall(r"[a-z][a-z0-9'+-]{2,}", (text or "").lower())


def domain_vocab(target_texts: Sequence[str], ref_texts: Sequence[str] | None = None,
                 min_ref: int = 8, lift: float = 1.2) -> set[str]:
    """Words a DENSE reference corpus leans on harder than the chatty target does.

    A conversational show and a scripted course share the same English filler, so the filler
    cancels out and what survives is the technical vocabulary. With no reference corpus the
    target is compared against itself, which still ranks jargon above filler, just less
    sharply."""
    ref, tgt = collections.Counter(), collections.Counter()
    for text in ref_texts or target_texts:
        ref.update(_tok(text))
    for text in target_texts:
        tgt.update(_tok(text))
    ref_total, tgt_total = max(sum(ref.values()), 1), max(sum(tgt.values()), 1)
    return {w for w, c in ref.items()
            if c >= min_ref and (c / ref_total) >= lift * (tgt.get(w, 0) / tgt_total)}


def density(segments: Iterable[Any], vocab: set[str], window: int = 3) -> list[float]:
    """Technical density per segment, smoothed - one quiet line inside a teaching run is a
    breath, not a topic change."""
    raw = []
    for seg in as_segments(segments):
        words = _tok(seg.text)
        raw.append(sum(1 for w in words if w in vocab) / len(words) if words else 0.0)
    out = []
    for i in range(len(raw)):
        lo, hi = max(0, i - window // 2), min(len(raw), i + window // 2 + 1)
        out.append(sum(raw[lo:hi]) / (hi - lo))
    return out


def teaching_spans(segments: Iterable[Any], vocab: set[str], keep: float = 0.70, pad: float = 2.0,
                   min_run: float = 12.0, window: int = 3,
                   duration: float | None = None) -> list[dict[str, float]]:
    """Keep the densest `keep` fraction of an episode as contiguous spans, sponsor reads out.
    Pass `duration` to keep the padding inside the file."""
    segs: list[Segment] = as_segments(segments)
    if not segs:
        return []
    ads = ad_spans(segs)
    in_ad = {i for i, s in enumerate(segs) if any(s.start < end and s.end > start for start, end in ads)}
    dens = density(segs, vocab, window=window)
    order = sorted((i for i in range(len(segs)) if i not in in_ad), key=lambda i: -dens[i])
    # budget the KEEP fraction of the real episode, not of episode+advertising, or stripping
    # 2 min of ads would silently hand those 2 min back as banter.
    allowance = keep * sum(s.end - s.start for i, s in enumerate(segs) if i not in in_ad)
    chosen, total = set(), 0.0
    for i in order:
        if total >= allowance:
            break
        chosen.add(i)
        total += segs[i].end - segs[i].start
    spans: list[dict[str, float]] = []
    cur: dict[str, float] | None = None
    for i, seg in enumerate(segs):
        if i not in chosen:
            cur = None
            continue
        if cur and seg.start - cur["end"] <= 3.0:
            cur["end"] = seg.end + pad
        else:
            cur = {"start": max(0.0, seg.start - pad), "end": seg.end + pad}
            spans.append(cur)
    # `pad` widens every span by a couple of seconds, which is enough to pull the first words
    # of a sponsor read back in. Clamp the padding, never the content.
    for start, end in ads:
        for span in spans:
            if span["start"] < end <= span["end"]:
                span["start"] = max(span["start"], end)
            if span["start"] <= start < span["end"]:
                span["end"] = min(span["end"], start)
    if duration:
        for span in spans:
            span["end"] = min(span["end"], float(duration))
    return [s for s in spans if s["end"] - s["start"] >= min_run]
