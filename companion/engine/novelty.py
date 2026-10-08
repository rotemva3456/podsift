"""What is new to you: key concepts of an episode that the episodes you heard did not cover.

Concepts are 1-3 word phrases scored by tf-idf over the show's own episodes. A phrase must be
said at least twice in an episode to count, and a phrase found in nearly every episode is the
host's verbal tic, not a concept, so the document-frequency band throws it away.
"""
from __future__ import annotations

import collections
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

STOP = set("""the a an and or of to in for on with is are was were be been being it this that
these those what how why when where who which i you we they he she at as by from do does did
can could will would should may might must not no nor so if then than there here all any both
each few more most other some such only own same too very just now also into out up down over
under again further once about after before during while because until against between through
your our their its his her them us me my me's one two three got get gets getting go goes going
say says said like really actually basically thing things stuff lot lots kind sort well yeah ok
okay right sure gonna wanna want need know think see look talk talking said say saying let lets
make makes made take takes took come comes came give gives gave put puts use uses used using
different something anything everything nothing someone everyone people time times way ways
first second next last new old good bad big small long short high low
important interesting nice cool great pretty much many quite still even ever never
today yesterday tomorrow week month year day days guy guys folks everybody anybody
episode podcast show listeners listener welcome thanks thank please maybe probably
mean means meaning example examples case cases point points question questions answer
part parts side sides end ends start starts back front top bottom around across along
zoom chat call calls talked told asked saying mentioned remember forget guess suppose
seems seemed looks looked comes going gone done doing able possible sure certain""".split())

# Below this many documents the tic filter cannot tell a verbal tic from a shared concept.
MIN_DOCS_FOR_TIC_FILTER = 5


def phrases(text: str | None, nmax: int = 3) -> list[str]:
    """1-3 gram phrases, stop words stripped at the edges. No dependencies, ~instant."""
    words = [w.strip(".-'") for w in re.findall(r"[a-z0-9][a-z0-9'+#.-]*", (text or "").lower())]
    words = [w for w in words if w]
    out = []
    for n in range(1, nmax + 1):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if gram[0] in STOP or gram[-1] in STOP:
                continue
            if any(len(w) < 2 for w in gram):
                continue
            if all(w.replace(".", "").isdigit() for w in gram):
                continue                       # "15" is a timestamp, not a concept
            out.append(" ".join(gram))
    return out


def _contains(a: str, b: str) -> bool:
    """One phrase inside the other, on word boundaries ("vlan" in "vlan tag", not "ip" in "ship")."""
    return f" {a} " in f" {b} " or f" {b} " in f" {a} "


def concepts(texts: Sequence[str], per_doc: int = 45, min_df: int = 2) -> list[list[str]]:
    """Key concepts per document by tf-idf over these documents, best first.

    A concept is said at least twice in its document and appears in at least `min_df`
    documents; a phrase in more than 40% of the documents is a tic and is dropped (only once
    there are MIN_DOCS_FOR_TIC_FILTER documents). "vlan" and "vlan tag" count as one idea."""
    counts = [collections.Counter(phrases(text)) for text in texts]
    df: collections.Counter[str] = collections.Counter()
    for tf in counts:
        df.update(tf.keys())
    total = max(len(texts), 1)
    ceiling = max(2, int(total * 0.4)) if total >= MIN_DOCS_FOR_TIC_FILTER else total
    keep = {p for p, n in df.items() if min_df <= n <= max(min_df, ceiling)}
    out = []
    for tf in counts:
        scored = sorted(((c * (1.0 + math.log(total / df[p])), p)
                         for p, c in tf.items() if p in keep and c >= 2), reverse=True)
        picked: list[str] = []
        for _score, phrase in scored:
            if any(_contains(phrase, other) for other in picked):
                continue
            picked.append(phrase)
            if len(picked) >= per_doc:
                break
        out.append(picked)
    return out


def _text(item: Any) -> str:
    """A transcript as text: a string, or segments (objects or dicts) joined."""
    if isinstance(item, str):
        return item
    parts = []
    for seg in item or ():
        parts.append(str(seg.get("text") or "") if isinstance(seg, dict) else str(getattr(seg, "text", "")))
    return "\n".join(parts)


def percent_new(segments: Any, heard_segment_lists: Iterable[Any], *, corpus: Iterable[Any] = (),
                per_ep: int = 45, min_df: int = 1) -> tuple[float | None, list[str]]:
    """(fraction of this episode's key concepts that no heard episode taught, those concepts).

    Each argument is a transcript: segments or plain text. `corpus` adds other episodes of the
    same show (unheard ones too) so rare and common phrases are told apart better.
    The fraction is None when the episode has no concepts to measure. With no heard episodes,
    everything is new (1.0).

    `min_df=1` lets a concept be unique to this episode, which is exactly what "new" means when
    the comparison set is only what you heard. `min_df=2` with the whole show as `corpus`
    reproduces the "next" ranking of the command-line tool."""
    heard = [_text(item) for item in heard_segment_lists]
    docs = [_text(segments), *heard, *(_text(item) for item in corpus)]
    per_doc = concepts(docs, per_doc=per_ep, min_df=min_df)
    mine = per_doc[0]
    if not mine:
        return None, []
    learned = {c for picked in per_doc[1:1 + len(heard)] for c in picked}
    fresh = [c for c in mine if c not in learned]
    return len(fresh) / len(mine), fresh
