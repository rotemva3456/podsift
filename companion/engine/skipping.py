"""Podsift's own content-skip detector. No community database, model or network.

Only explicit transcript evidence seeds a skip. Vendor names, links and a mention
of sponsorship in a lesson are insufficient. Each result keeps its source line
IDs and a reason, so a caller can explain the decision. Times always come from
the transcript; this module never guesses audio timestamps from character counts.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .transcript import Segment, as_segments

MAX_BLOCK, MAX_GAP = 180.0, 25.0

DISCLOSURE = re.compile(
    r"\b(?:this|today's|our|the) (?:episode|show|podcast|video) (?:is |was )?"
    r"(?:sponsored|supported|brought to you) by\b"
    r"|(?:^|[.!?]\s+)sponsored by\b|\bwe(?:'re| are) sponsored by\b"
    r"|\bbrought to you by\b|\b(?:word|message|break) from (?:our|the) sponsor\b"
    r"|\bhear from (?:our|the|today's) sponsor\b"
    r"|\bour sponsor (?!(?:policy|contract|guideline|message|example)\b)"
    r"[\w'-]+(?: [\w'-]+){0,3} (?:is|offers|provides|makes)\b"
    r"|\b(?:thanks? to|thank you to) (?:our|today's) sponsors?\b"
    r"|\b(?:paid advertisement|advertising break|sponsor break)\b", re.I)
OFFER = re.compile(
    r"\buse (?:(?:the |our )?(?:promo|discount|coupon) code\b"
    r"|(?:the )?code [\w-]+ (?:at checkout|to save|for \d))"
    r"|\b(?:enter|apply) (?:the |our )?(?:promo|discount|coupon) code\b", re.I)
CALL_TO_ACTION = re.compile(
    r"\b(?:visit|head (?:on )?over to|go to|sign up"
    r"|start (?:a |your )?(?:free )?trial|learn more at|find out more at)\b", re.I)
COMMERCIAL = re.compile(
    r"\b(?:free trial|discount|checkout|\d+\s*(?:%|percent) off|save \$?\d+)\b", re.I)
DEMO_OFFER = re.compile(r"\bbook (?:a |your )?demo\b", re.I)
SELF_PROMO = re.compile(
    r"\b(?:support (?:our|this|the) (?:show|podcast|channel)|join our patreon"
    r"|become (?:a |our )?(?:patron|supporting member)|buy (?:our|my) (?:book|course|merch)"
    r"|(?:our|my) (?:merch|course|book) is (?:available|on sale))\b"
    r"|\bpatreon\.com/", re.I)
INTERACTION = re.compile(
    r"\b(?:please |don't forget to |remember to )(?:like (?:and subscribe|this (?:video|episode))"
    r"|subscribe to (?:our|the|this) (?:channel|show|podcast)|share this (?:episode|video)"
    r"|leave (?:us |a )?(?:review|rating))\b"
    r"|\bhit (?:the |that )?(?:like|subscribe) button\b"
    r"|\bgive (?:us|the show|this podcast) (?:a )?five[- ]star (?:review|rating)\b", re.I)
INTRO = re.compile(r"\b(?:welcome (?:back )?to (?:the |our )?(?:show|podcast)"
                   r"|you(?:'re| are) listening to)\b", re.I)
OUTRO = re.compile(r"\b(?:thanks? (?:you )?for (?:listening|watching)"
                   r"|until next time|see you (?:all )?next (?:time|week))\b", re.I)
PREVIEW = re.compile(r"^(?:next week\b|next time (?:on|we)\b|in (?:our|the) next episode\b"
                     r"|previously on\b|in (?:our|the) (?:last|previous) episode\b)", re.I)
FILLER = re.compile(r"^\[(?:silence|pause|off[- ]topic(?: discussion| banter)?|unintelligible)\]$", re.I)
MUSIC = re.compile(r"^(?:\[(?:intro |outro |instrumental )?music\]|[♪♫\s]+)$", re.I)
NON_MUSIC = re.compile(r"^\[(?:spoken interlude|non[- ]music|talking during music)\]$", re.I)
RETURN = re.compile(
    r"(?:^|[.!?]\s+)(?:back to\b|(?:now )?back (?:to|with) (?:the|our) (?:show|episode|discussion)\b"
    r"|(?:today|now|next|first) (?:we |let's )?(?:explain|look at|discuss|learn|study|explore)\b"
    r"|let's (?:get back|return|dive|continue|talk about|look at)\b"
    r"|(?:and )?now (?:for|on with) (?:the|our) (?:show|episode|discussion)\b)", re.I)
EXAMPLE = re.compile(r"\b(?:for example|an example of|if (?:a|the) (?:host|podcast)|quoted? phrase)\b", re.I)


@dataclass(frozen=True)
class SkipSpan:
    start: float
    end: float
    category: str
    reason: str
    segment_ids: tuple[str, ...]


def _signal(seg: Segment, duration: float | None) -> tuple[str, str] | None:
    text = " ".join(seg.text.replace("’", "'").split())
    # A cue mixing an ad and a return to the lesson needs finer timing. Keep it
    # rather than silently skipping both, even when it contains a promo code.
    if RETURN.search(text) or EXAMPLE.search(text):
        return None
    if DISCLOSURE.search(text):
        return "sponsor", "The speaker explicitly announces a sponsor or paid advertisement."
    if SELF_PROMO.search(text):
        return "selfpromo", "The speaker asks listeners to support or buy from the show."
    if OFFER.search(text):
        return "sponsor", "The speaker asks the listener to use a promotional or discount code."
    if CALL_TO_ACTION.search(text) and COMMERCIAL.search(text):
        return "sponsor", "A purchase or signup request is accompanied by a commercial offer."
    if INTERACTION.search(text):
        return "interaction", "The speaker explicitly asks for a like, subscription or review."
    if seg.end - seg.start <= 30 and seg.start < 90 and INTRO.search(text):
        return "intro", "An opening greeting identifies the show."
    if duration and seg.end - seg.start <= 30 and seg.start >= max(0, duration - 90) and OUTRO.search(text):
        return "outro", "A closing sign-off appears near the end of the episode."
    if seg.end - seg.start <= 30 and PREVIEW.search(text):
        return "preview", "The speaker explicitly introduces another episode or its recap."
    if FILLER.fullmatch(text):
        return "filler", "The transcript explicitly marks silence, an aside or unintelligible speech."
    if MUSIC.fullmatch(text):
        if seg.end <= 90:
            return "intro", "The transcript marks opening music without speech."
        if duration and seg.start >= max(0, duration - 90):
            return "outro", "The transcript marks closing music without speech."
    if NON_MUSIC.fullmatch(text):
        return "music_offtopic", "The transcript explicitly marks a spoken interlude in music."
    return None


def skip_spans(segments: Iterable[Any], *, duration: float | None = None) -> list[SkipSpan]:
    """Evidence-backed spans, including every existing skip category.

    Between two nearby sponsor cues, include the ad's intervening sentences.
    A disclosed read may also join its closing offer through contiguous cues,
    within MAX_BLOCK, without guessing an endpoint beyond the offer;
    never bridge an explicit return to the lesson or another category. Other
    categories only join adjacent cues. A single oversized cue is left alone:
    trimming it to an arbitrary timestamp could cut into the actual lesson.
    """
    segs = as_segments(segments)
    texts = [" ".join(seg.text.replace("’", "'").split()) for seg in segs]
    signals = [_signal(seg, duration) for seg in segs]
    spans: list[SkipSpan] = []
    previous = -1
    for index, (seg, signal) in enumerate(zip(segs, signals)):
        text = texts[index]
        current = spans[-1] if spans else None
        between = range(previous + 1, index)
        # A demo offer alone can be ordinary teaching. It only closes a read
        # already bracketed by an explicit disclosure and contiguous timing.
        closes_read = (not RETURN.search(text) and not EXAMPLE.search(text)
                       and (OFFER.search(text) or DEMO_OFFER.search(text)
                            or (CALL_TO_ACTION.search(text) and COMMERCIAL.search(text))))
        long_read = (current and current.category == "sponsor" and previous >= 0
                     and DISCLOSURE.search(texts[previous])
                     and closes_read and all(segs[i].start - segs[i - 1].end <= 2 for i in range(previous + 1, index + 1)))
        if (signal is None and long_read and seg.end - current.start <= MAX_BLOCK
                and all(not RETURN.search(texts[i]) and not EXAMPLE.search(texts[i])
                        and (signals[i] is None or signals[i][0] == "sponsor") for i in between)):
            signal = "sponsor", current.reason
        if signal is None or seg.end - seg.start > MAX_BLOCK or (duration and seg.end > duration):
            continue
        category, reason = signal
        start = 0.0 if index == 0 and category == "sponsor" and seg.start < 15 and seg.end <= MAX_BLOCK else seg.start
        gap = MAX_GAP if category in ("sponsor", "selfpromo") else 2.0
        bridge = all(not RETURN.search(texts[i]) and not EXAMPLE.search(texts[i])
                     and (signals[i] is None or signals[i][0] == category) for i in between)
        # Real host-read ads can be a minute long. A disclosure plus an explicit
        # closing offer brackets the body, but a transcript gap or return to the
        # lesson prevents the longer bridge. Two generic offers alone cannot do it.
        if (current and current.category == category and (seg.start - current.end <= gap or long_read)
                and seg.end - current.start <= MAX_BLOCK and bridge):
            spans[-1] = SkipSpan(current.start, seg.end, category, current.reason,
                                 (*current.segment_ids, *(segs[i].id for i in between), seg.id))
        else:
            spans.append(SkipSpan(start, seg.end, category, reason, (seg.id,)))
        previous = index
    return spans
