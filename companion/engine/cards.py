"""Flashcards: SM-2 scheduling, and the filter that keeps show trivia out of the deck.

A card carries the episode and the second it came from, so a card you keep missing can be
played again in the hosts' own words, not just re-read.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from typing import Any

CARD_RE = re.compile(r"^Q:\s*(.+?)\s*$\n+^A:\s*(.+?)\s*$", re.M)
GRADES = {"1": 0, "again": 0, "2": 1, "hard": 1, "3": 2, "good": 2, "4": 3, "easy": 3}
DAY = 86400
_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})\]")

# A card about the podcast itself is not a card about the subject. These leak in from the intro
# and outro of every episode and are the fastest way to make a deck feel like a waste of time.
# The broad words are pinned to their show-meta phrasing, so "credit rating" or "subscribe to a
# multicast group" still make good cards.
META_RE = re.compile(
    r"\b(co-?hosts?|podcast is called|name of the (podcast|show)|linkedin|packet ?pushers|"
    r"apple podcasts|subscribe to (the|this|our) (show|podcast|channel|newsletter)|"
    r"(leave|give|write) (us )?an? (rating|review)|ratings? and reviews?|"
    r"join (us |our |the )?(on |in )?(slack|discord)|(slack|discord) (channel|community|group|server)|"
    r"which episode number|this episode number)\b", re.I)


def grade_value(grade: int | str) -> int:
    """0 again, 1 hard, 2 good, 3 easy - from an int or any key of GRADES."""
    if isinstance(grade, int) and not isinstance(grade, bool) and 0 <= grade <= 3:
        return grade
    value = GRADES.get(str(grade).strip().lower())
    if value is None:
        raise ValueError(f"Unknown grade {grade!r}: use again, hard, good or easy.")
    return value


def schedule(card_state: Mapping[str, Any], grade: int | str, now: float | None = None) -> dict[str, Any]:
    """SM-2, trimmed to what a podcast needs. Returns the card's next interval (days), ease,
    reps, lapses and due (unix time). "Again" brings the card back in 10 minutes."""
    grade = grade_value(grade)
    now = time.time() if now is None else now
    ease = float(card_state.get("ease", 2.5))
    reps = int(card_state.get("reps", 0))
    lapses = int(card_state.get("lapses", 0))
    if grade == 0:
        return {"interval": 0, "ease": max(1.3, ease - 0.2), "reps": 0,
                "lapses": lapses + 1, "due": now + 600}
    ease = max(1.3, ease + (0.1 if grade == 3 else 0.0 if grade == 2 else -0.15))
    interval = 1 if reps == 0 else 4 if reps == 1 else round(float(card_state.get("interval", 1)) * ease)
    if grade == 1:
        interval = max(1, round(interval * 0.6))
    return {"interval": interval, "ease": ease, "reps": reps + 1,
            "lapses": lapses, "due": now + interval * DAY}


def is_meta(question: str, answer: str = "") -> bool:
    """True for a card about the show itself (hosts, ratings, where to subscribe)."""
    return bool(META_RE.search(question or "") or META_RE.search(answer or ""))


def parse_cards(text: str, prefix: str = "") -> list[dict[str, Any]]:
    """Q:/A: cards from Markdown, each with the [mm:ss] its answer cites (`at`, seconds or
    None). Show-trivia cards are dropped. Ids are stable: `prefix` + a hash of the question."""
    out = []
    for question, answer in CARD_RE.findall(text or ""):
        if is_meta(question, answer):
            continue
        stamp = _STAMP.search(answer)
        at = int(stamp.group(1)) * 60 + int(stamp.group(2)) if stamp else None
        out.append({"id": prefix + hashlib.sha1(question.encode()).hexdigest()[:8],
                    "question": question, "answer": re.sub(r"\s*\[\d{1,3}:\d{2}\]\s*$", "", answer),
                    "at": at})
    return out
