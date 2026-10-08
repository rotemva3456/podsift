"""The 5 hand-labelled fixture episodes: load their transcript and their answer key.

A label never stores a literal segment id ("seg-0001"): it stores a 1-based INDEX into the
transcript's segment list, the same order a human reads the transcript in (these labels were written by reading a plain
numbered dump of each transcript). ``Fixture.segment_id``
resolves an index to the real ``Segment.id`` at load time, so a label stays correct even if a
transcript parser one day assigns ids differently, and ``load_fixtures`` raises immediately if a
label ever points past the end of its transcript (a typo, or a transcript that shrank).

No PodFetch, no network, no audio: transcripts come from JSON or SRT files already committed
under ``fixtures/`` or reused from ``demo/library/`` (see ``fixtures/ATTRIBUTION.md`` and
``demo/LICENSE-CONTENT.md`` for licence and attribution).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine import Segment, from_timed_json, parse_srt

# Fixtures reference transcripts by a path relative to the app folder.
ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FixtureError(ValueError):
    """A fixture file is malformed, or its label references a segment that doesn't exist."""


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str


@dataclass(frozen=True)
class KeyIdea:
    text: str
    indices: tuple[int, ...]


@dataclass(frozen=True)
class CutRequest:
    want: str
    skip: str
    minutes: float | None
    must_keep: tuple[int, ...]
    never_keep: tuple[int, ...]


@dataclass(frozen=True)
class Label:
    verdict: str
    verdict_reason: str
    chapters: tuple[Chapter, ...]
    key_ideas: tuple[KeyIdea, ...]
    cut_request: CutRequest


@dataclass
class Fixture:
    episode_id: str
    title: str
    show: str
    attribution: str
    segments: list[Segment] = field(repr=False)
    duration: float
    label: Label

    def segment_id(self, index: int) -> str:
        """The real ``Segment.id`` at a label's 1-based index."""
        if not 1 <= index <= len(self.segments):
            raise FixtureError(f"{self.episode_id}: label references segment {index}, but the "
                               f"transcript only has {len(self.segments)} segments")
        return self.segments[index - 1].id

    def segment_start(self, index: int) -> float:
        return self.segments[index - 1].start

    def chapter_targets(self) -> list[tuple[float, str]]:
        """[(start seconds, title)] for each required chapter, for matching a model's chapters."""
        return [(self.segment_start(c.index), c.title) for c in self.label.chapters]

    def must_keep_ids(self) -> set[str]:
        return {self.segment_id(i) for i in self.label.cut_request.must_keep}

    def never_keep_ids(self) -> set[str]:
        return {self.segment_id(i) for i in self.label.cut_request.never_keep}


def _load_transcript(spec: dict[str, Any]) -> list[Segment]:
    path = ROOT / spec["path"]
    fmt = spec["format"]
    if fmt == "timed_json":
        return from_timed_json(json.loads(path.read_text(encoding="utf-8")))
    if fmt == "srt":
        return parse_srt(path.read_text(encoding="utf-8"))
    raise FixtureError(f"Unknown fixture transcript format {fmt!r} ({path})")


def _label_from(raw: dict[str, Any]) -> Label:
    cut = raw["cut_request"]
    return Label(
        verdict=raw["verdict"],
        verdict_reason=raw.get("verdict_reason", ""),
        chapters=tuple(Chapter(int(c["segment_index"]), c["title"]) for c in raw["chapters"]),
        key_ideas=tuple(KeyIdea(k["text"], tuple(int(i) for i in k["segment_indices"])) for k in raw["key_ideas"]),
        cut_request=CutRequest(cut["want"], cut.get("skip") or "", cut.get("minutes"),
                               tuple(int(i) for i in cut["must_keep_segment_indices"]),
                               tuple(int(i) for i in cut["never_keep_segment_indices"])),
    )


def _highest_index(label_raw: dict[str, Any]) -> int:
    cut = label_raw["cut_request"]
    indices = ([c["segment_index"] for c in label_raw["chapters"]]
              + [i for k in label_raw["key_ideas"] for i in k["segment_indices"]]
              + list(cut["must_keep_segment_indices"]) + list(cut["never_keep_segment_indices"]))
    return max(indices) if indices else 0


def load_fixture(path: Path) -> Fixture:
    raw = json.loads(path.read_text(encoding="utf-8"))
    segments = _load_transcript(raw["transcript"])
    highest = _highest_index(raw["label"])
    if highest > len(segments):
        raise FixtureError(f"{path.name}: label references segment {highest}, but the transcript "
                           f"({raw['transcript']['path']}) only has {len(segments)} segments")
    if not segments:
        raise FixtureError(f"{path.name}: transcript has no segments")
    duration = max((s.end for s in segments), default=0.0)
    return Fixture(raw["episode_id"], raw["title"], raw["show"], raw["attribution"],
                   segments, duration, _label_from(raw["label"]))


def load_fixtures(episode_ids: list[str] | None = None) -> list[Fixture]:
    """Every fixture in ``fixtures/*.json``, sorted by file name, or only ``episode_ids``.

    Raises ``FixtureError`` (not a bare assertion) the moment a label is inconsistent with its
    transcript, so a broken fixture fails loudly instead of silently scoring wrong."""
    out = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        fixture = load_fixture(path)
        if episode_ids is not None and fixture.episode_id not in episode_ids:
            continue
        out.append(fixture)
    if episode_ids is not None:
        missing = set(episode_ids) - {f.episode_id for f in out}
        if missing:
            raise FixtureError(f"No fixture file for: {', '.join(sorted(missing))}")
    return out
