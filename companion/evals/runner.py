"""Runs the real brief and cut-plan code against the fixtures, and scores the answer.

Two seams, both already used by production, called directly with fixture data instead of a
request: ``companion.brief.ask`` (builds the prompt, validates the answer, repairs it once) for
briefs, and ``companion.cuts._ai_spans`` + ``companion.engine.select_ranges`` (turns the model's
segment-id ranges into spans, applying exclusions and the time budget) for cut plans. Neither
needs PodFetch: ``ask`` only needs segments and a facts string, and the ``ready`` dict
``_ai_spans`` expects is exactly what ``companion.cuts.make_plan`` builds for it, built here by
hand from the fixture instead. brief.py and cuts.py are not edited here; this only reads their
public and one underscore-prefixed function.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException

from .. import brief as briefs
from .. import cuts
from ..engine import ad_spans, merge_intervals
from ..llm import LLM, LLMError
from .fixtures import Fixture

# Why a brief or cut has no result, so a reader (and a rerun) can tell "the provider never gave a
# usable answer" apart from "it answered, and the answer was wrong":
#   "limits"   an LLMError (rate limit exhausted, daily cap, timeout, bad JSON shape, cut off by
#              max_tokens, ...) -- companion.llm/companion.providers.openai_compat raised before
#              our own validation ever saw real content. Infrastructure, not the model's answer.
#   "rejected" companion.brief.BriefProblem -- the model answered, but companion.brief.validate()
#              rejected it (bad segment ids, chapters out of order, ...), even after one repair.
#              Cuts have no equivalent exception: companion.cuts._ai_spans silently drops an
#              invalid range instead of failing the whole plan, so a cut can only be "limits" or...
#   "bug"      any other exception. Should never happen; if it does, it's this eval's own bug (or
#              a real one in cuts._ai_spans / brief.ask), not a model or a rate limit, and is
#              always worth a NEEDS line quoting the message.
FailureKind = Literal["limits", "rejected", "bug"]

# A model's chapter "counts" as matching a required one if its start is within this many seconds
# of the label's anchor segment. Every fixture's 3 anchors are at least 3 minutes apart (see
# fixtures/*.json), so this can't accidentally match two different required chapters to one.
CHAPTER_TOLERANCE_SECONDS = 60.0
# A rendered/kept plan is "within budget" if it doesn't overshoot by more than this fraction...
BUDGET_SLACK_FRACTION = 0.05
# ...plus this many flat seconds, so a request for a very short clip isn't failed by rounding.
BUDGET_SLACK_SECONDS = 2.0


class MeteredLLM:
    """Wraps a real ``LLM`` so every call it makes, brief or cut, is counted the same way:
    how many calls, how many characters in (system + user), and how many wall-clock seconds.
    Passes ``model`` / ``max_input_chars`` through so ``brief.model_name`` / ``max_input_chars``
    read the wrapped provider's real values. Never touches the network itself."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.calls = 0
        self.input_chars = 0
        self.seconds = 0.0

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "")

    @property
    def max_input_chars(self) -> int | None:
        return getattr(self.inner, "max_input_chars", None)

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any],
                      max_tokens: int = 1024) -> dict[str, Any]:
        self.calls += 1
        self.input_chars += len(system) + len(user)
        started = time.monotonic()
        try:
            return self.inner.complete_json(system=system, user=user, schema=schema, max_tokens=max_tokens)
        finally:
            self.seconds += time.monotonic() - started


@dataclass
class BriefOutcome:
    episode_id: str
    ok: bool = False
    error: str = ""
    failure_kind: FailureKind | None = None    # None when ok
    repaired: bool = False
    citation_problem_first_try: bool = False
    chapter_problem_first_try: bool = False
    verdict: str | None = None
    verdict_match: bool = False
    chapters_found: int = 0
    chapters_total: int = 0


@dataclass
class CutOutcome:
    episode_id: str
    ok: bool = False
    error: str = ""
    failure_kind: FailureKind | None = None    # None when ok
    forbidden_kept: int = 0
    must_keep_hit: int = 0
    must_keep_total: int = 0
    kept_seconds: float = 0.0
    budget_seconds: float | None = None
    budget_ok: bool = True


def _facts_text(fixture: Fixture) -> str:
    """A plausible facts block with no PodFetch: real duration and sponsor seconds (measured from
    the fixture's own segments, same as production), no listening history (a fresh listener)."""
    episode = {"name": fixture.title}
    facts: dict[str, Any] = {
        "duration": fixture.duration,
        "ads_seconds": round(sum(end - start for start, end in ad_spans(fixture.segments))) or None,
        "percent_new": None, "new_concepts": [], "heard_count": None,
        "publisher_chapters": [], "topics": [],
    }
    return briefs.facts_text(episode, fixture.show, facts, fixture.segments)


def run_brief(llm: MeteredLLM, fixture: Fixture) -> BriefOutcome:
    """Score one fixture's brief: does it validate, does it need a repair, does the verdict
    agree with the label, and how many of the label's 3 required chapters show up."""
    out = BriefOutcome(fixture.episode_id, chapters_total=len(fixture.label.chapters))
    try:
        budget = briefs.max_input_chars(llm)
        text = _facts_text(fixture)
        brief_part, stats = briefs.ask(llm, fixture.segments, text, budget)
    except LLMError as exc:
        out.error, out.failure_kind = str(exc), "limits"
        return out
    except briefs.BriefProblem as exc:
        out.error, out.failure_kind = str(exc), "rejected"
        return out
    except Exception as exc:  # a bug here must not silently look like "no data for this model"
        out.error, out.failure_kind = f"{type(exc).__name__}: {exc}", "bug"
        return out
    out.ok = True
    out.repaired = bool(stats.get("repaired"))
    if out.repaired:
        problems = " | ".join(stats.get("first_problems") or [])
        out.citation_problem_first_try = "cite" in problems
        out.chapter_problem_first_try = "chapter" in problems
    out.verdict = brief_part.get("verdict")
    out.verdict_match = out.verdict == fixture.label.verdict
    model_chapters = brief_part.get("chapters") or []
    for target_start, _title in fixture.chapter_targets():
        if any(abs(float(c.get("start", -1e9)) - target_start) <= CHAPTER_TOLERANCE_SECONDS for c in model_chapters):
            out.chapters_found += 1
    return out


def _ready_entry(fixture: Fixture) -> dict[str, Any]:
    """The one field of ``companion.cuts.make_plan``'s ``ready`` list ``_ai_spans`` needs, built
    from the fixture instead of a loaded episode. Ad reads are excluded the same way a real plan
    excludes them (``skip_ads``); the fixture's own skip words are left for the model to judge,
    not mechanically forced out, since that is exactly what this eval measures."""
    ads = [(start, min(end, fixture.duration)) for start, end in ad_spans(fixture.segments)]
    return {"episode_id": fixture.episode_id, "title": fixture.title, "audio": "",
            "duration": fixture.duration, "segments": fixture.segments, "order": 0,
            "ads": ads, "exclude": merge_intervals(ads)}


def run_cut(llm: MeteredLLM, fixture: Fixture) -> CutOutcome:
    """Score one fixture's AI-mode cut plan: did any never-keep segment survive into a kept span
    (must be 0), how many of the must-keep segments were kept, and did the plan hold its budget."""
    request = fixture.label.cut_request
    budget_seconds = request.minutes * 60 if request.minutes else None
    out = CutOutcome(fixture.episode_id, budget_seconds=budget_seconds,
                     must_keep_total=len(request.must_keep))
    body = cuts.PlanRequest(want=request.want, skip=request.skip or None,
                            minutes=request.minutes, skip_ads=True, mode="ai")
    ready = [_ready_entry(fixture)]
    try:
        spans, _omitted, _source_seconds = cuts._ai_spans(body, ready, llm, budget_seconds, llm.max_input_chars)
    except HTTPException as exc:
        # cuts._ai_spans raises HTTPException(502, ...) only by wrapping an LLMError (see its own
        # `except LLMError: raise HTTPException(502, ...)`), so this is always the "limits" kind:
        # a bad AI-mode answer (invalid ids) is dropped per-range inside _ai_spans, never raised.
        out.error, out.failure_kind = str(exc.detail), "limits"
        return out
    except Exception as exc:
        out.error, out.failure_kind = f"{type(exc).__name__}: {exc}", "bug"
        return out
    out.ok = True
    kept_ids: set[str] = set()
    for span in spans:
        kept_ids.update(span.get("segment_ids") or ())
    out.forbidden_kept = len(kept_ids & fixture.never_keep_ids())
    out.must_keep_hit = len(kept_ids & fixture.must_keep_ids())
    out.kept_seconds = sum(span["end"] - span["start"] for span in spans)
    out.budget_ok = (budget_seconds is None
                     or out.kept_seconds <= budget_seconds * (1 + BUDGET_SLACK_FRACTION) + BUDGET_SLACK_SECONDS)
    return out


@dataclass
class ModelReport:
    """Every fixture's outcome for one model, plus the aggregate row `scripts/eval.py` prints."""
    provider: str
    model: str
    briefs: list[BriefOutcome] = field(default_factory=list)
    cuts: list[CutOutcome] = field(default_factory=list)
    seconds: float = 0.0
    input_chars: int = 0

    def row(self) -> dict[str, Any]:
        n = len(self.briefs)
        ok_briefs = [b for b in self.briefs if b.ok]
        ok_cuts = [c for c in self.cuts if c.ok]

        def failed(outcomes, kind: FailureKind) -> int:
            return sum(1 for o in outcomes if not o.ok and o.failure_kind == kind)

        return {
            "provider": self.provider, "model": self.model, "fixtures": n,
            "valid_json_first_try": sum(1 for b in ok_briefs if not b.repaired),
            "repaired": sum(1 for b in ok_briefs if b.repaired),
            "brief_failed": sum(1 for b in self.briefs if not b.ok),
            "brief_limits": failed(self.briefs, "limits"),
            "brief_rejected": failed(self.briefs, "rejected"),
            "brief_bug": failed(self.briefs, "bug"),
            "valid_citations_first_try": sum(1 for b in ok_briefs if not b.citation_problem_first_try),
            "chapters_found": sum(b.chapters_found for b in ok_briefs),
            "chapters_expected": sum(b.chapters_total for b in ok_briefs),
            "verdict_agreement": sum(1 for b in ok_briefs if b.verdict_match),
            "cut_failed": sum(1 for c in self.cuts if not c.ok),
            "cut_limits": failed(self.cuts, "limits"),
            "cut_bug": failed(self.cuts, "bug"),
            "forbidden_segments_kept": sum(c.forbidden_kept for c in ok_cuts),
            "must_keep_hit": sum(c.must_keep_hit for c in ok_cuts),
            "must_keep_expected": sum(c.must_keep_total for c in ok_cuts),
            "budget_kept": sum(1 for c in ok_cuts if c.budget_ok),
            "seconds": round(self.seconds, 1),
            "input_tokens": round(self.input_chars / 4),
            "not_recommended": any(c.forbidden_kept for c in ok_cuts),
        }

    def failures(self) -> list[dict[str, Any]]:
        """One entry per failed brief or cut, in fixture order, so a run is diagnosable: which
        fixture, brief or cut, why (`failure_kind`), and the exact message."""
        out = []
        for kind, outcomes in (("brief", self.briefs), ("cut", self.cuts)):
            for outcome in outcomes:
                if not outcome.ok:
                    out.append({"provider": self.provider, "model": self.model, "call": kind,
                               "episode_id": outcome.episode_id, "failure_kind": outcome.failure_kind,
                               "error": outcome.error})
        return out


def evaluate(provider: str, model: str, llm: LLM, fixtures: list[Fixture]) -> ModelReport:
    """Run both the brief and the AI-mode cut plan for every fixture, through one metered LLM
    (so ``seconds`` / ``input_tokens`` cover the whole run, not just one half of it)."""
    metered = MeteredLLM(llm)
    report = ModelReport(provider, model)
    for fixture in fixtures:
        report.briefs.append(run_brief(metered, fixture))
        report.cuts.append(run_cut(metered, fixture))
    report.seconds = metered.seconds
    report.input_chars = metered.input_chars
    return report
