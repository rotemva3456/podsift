"""The scoring logic in runner.py, against small hand-built fixtures and FakeLLM so every case
is deterministic and fast (no network). The real 5 fixtures are exercised for
shape only in test_fixtures.py; running a real model against them is scripts/eval.py's job."""
from __future__ import annotations

from companion.engine import Segment
from companion.llm import FakeLLM, LLMError

from .fixtures import Chapter, CutRequest, Fixture, KeyIdea, Label
from .runner import MeteredLLM, evaluate, run_brief, run_cut

# 6 segments, each 20s (> the 15s block size), 5s gaps: `cuts.blocks()` can't merge any two of
# them (a block's own span would already exceed 15s), so every block id equals its segment id.
# That makes AI-mode ranges ("start_id"/"end_id") predictable in a test.
SEGMENTS = [
    Segment("a", 0, 20, "Intro chit chat, off topic."),
    Segment("b", 25, 45, "The core explanation the listener wants, part one."),
    Segment("c", 50, 70, "The core explanation the listener wants, part two."),
    Segment("d", 75, 95, "A sponsor read, an ad the listener wants to skip."),
    Segment("e", 100, 120, "More off-topic chatter that must never be kept."),
    Segment("f", 125, 145, "A closing thought that repeats the core topic."),
]


def _fixture(episode_id: str = "fx1") -> Fixture:
    label = Label(
        verdict="HEAR", verdict_reason="test",
        chapters=(Chapter(1, "Intro"), Chapter(3, "Core, part two"), Chapter(5, "Off-topic chatter")),
        key_ideas=(KeyIdea("The core idea.", (2, 3)),),
        cut_request=CutRequest(want="the core explanation", skip="off-topic chatter and the sponsor read",
                               minutes=0.75, must_keep=(2, 3), never_keep=(5,)),
    )
    return Fixture(episode_id, "Test episode", "Test show", "test attribution", list(SEGMENTS), 145.0, label)


def _brief_answer(verdict: str = "HEAR", chapter_ids: tuple[str, str, str] = ("1", "3", "5")) -> dict:
    return {"summary": "A test episode.", "verdict": verdict, "verdict_reason": "Because it's a test.",
            "who_for": "Test readers.",
            "chapters": [{"title": "Intro", "start_segment_id": chapter_ids[0]},
                        {"title": "Core, part two", "start_segment_id": chapter_ids[1]},
                        {"title": "Off-topic chatter", "start_segment_id": chapter_ids[2]}],
            "key_ideas": [{"text": "The core idea.", "segment_ids": ["2", "3"]}]}


def _cut_answer(*ranges: tuple[str, str, int]) -> dict:
    return {"ranges": [{"start_id": start, "end_id": end, "why": "on topic", "relevance": rel}
                       for start, end, rel in ranges]}


# ---------------------------------------------------------------- brief


def test_run_brief_clean_pass_matches_verdict_and_all_chapters():
    fixture = _fixture()
    llm = MeteredLLM(FakeLLM([_brief_answer()]))
    out = run_brief(llm, fixture)
    assert out.ok and not out.error
    assert out.repaired is False
    assert out.verdict == "HEAR" and out.verdict_match
    assert out.chapters_found == 3
    assert out.citation_problem_first_try is False
    assert llm.calls == 1


def test_run_brief_repairs_a_bad_chapter_reference():
    fixture = _fixture()
    bad = _brief_answer(chapter_ids=("1", "999", "5"))  # segment 999 doesn't exist
    out = run_brief(MeteredLLM(FakeLLM([bad, _brief_answer()])), fixture)
    assert out.ok
    assert out.repaired is True
    assert out.chapter_problem_first_try is True
    assert out.citation_problem_first_try is False
    assert out.chapters_found == 3, "the repaired (second) answer is what gets scored"


def test_run_brief_flags_a_bad_citation_on_the_first_try():
    fixture = _fixture()
    bad = _brief_answer()
    bad["key_ideas"] = [{"text": "The core idea.", "segment_ids": ["999"]}]
    out = run_brief(MeteredLLM(FakeLLM([bad, _brief_answer()])), fixture)
    assert out.ok and out.repaired
    assert out.citation_problem_first_try is True


def test_run_brief_records_a_verdict_disagreement():
    fixture = _fixture()  # label says HEAR
    out = run_brief(MeteredLLM(FakeLLM([_brief_answer(verdict="SKIP")])), fixture)
    assert out.ok
    assert out.verdict == "SKIP"
    assert out.verdict_match is False


def test_run_brief_still_broken_after_one_repair_is_not_ok():
    fixture = _fixture()
    bad = _brief_answer(chapter_ids=("1", "999", "5"))
    out = run_brief(MeteredLLM(FakeLLM([bad, bad])), fixture)
    assert out.ok is False
    assert out.error
    assert out.failure_kind == "rejected", "the model answered; validate() turned it down"


def test_run_brief_provider_failure_is_not_ok():
    fixture = _fixture()
    out = run_brief(MeteredLLM(FakeLLM([LLMError("The key was rejected.")])), fixture)
    assert out.ok is False
    assert "rejected" in out.error
    assert out.failure_kind == "limits", "an LLMError means the provider never gave a real answer"


def test_run_brief_unexpected_exception_is_a_bug():
    fixture = _fixture()
    out = run_brief(MeteredLLM(FakeLLM([ValueError("boom")])), fixture)
    assert out.ok is False
    assert out.failure_kind == "bug"
    assert "boom" in out.error


# ---------------------------------------------------------------- cut


def test_run_cut_keeps_wanted_segments_and_respects_budget():
    fixture = _fixture()  # minutes=0.75 -> 45s budget; b and c are 20s each = 40s
    answer = _cut_answer(("b", "b", 3), ("c", "c", 3), ("f", "f", 2))  # f should be dropped: over budget
    out = run_cut(MeteredLLM(FakeLLM([answer])), fixture)
    assert out.ok and not out.error
    assert out.forbidden_kept == 0
    assert out.must_keep_hit == 2 and out.must_keep_total == 2
    assert out.kept_seconds == 40.0
    assert out.budget_ok is True


def test_run_cut_catches_a_forbidden_segment_kept():
    fixture = _fixture()
    answer = _cut_answer(("b", "b", 3), ("e", "e", 3))  # e is the never-keep segment
    out = run_cut(MeteredLLM(FakeLLM([answer])), fixture)
    assert out.ok
    assert out.forbidden_kept == 1, "the label's never-keep segment must be counted, not silently dropped"


def test_run_cut_over_budget_is_flagged():
    tight = _fixture()  # Label/CutRequest are frozen, so build a new one with a tighter budget
    tight.label = Label(tight.label.verdict, tight.label.verdict_reason, tight.label.chapters,
                        tight.label.key_ideas,
                        CutRequest(tight.label.cut_request.want, tight.label.cut_request.skip,
                                  minutes=0.3,  # 18s: even one 20s block overshoots
                                  must_keep=tight.label.cut_request.must_keep,
                                  never_keep=tight.label.cut_request.never_keep))
    answer = _cut_answer(("b", "b", 3))
    out = run_cut(MeteredLLM(FakeLLM([answer])), tight)
    assert out.ok
    assert out.kept_seconds == 0.0, "a single pick already over budget is dropped whole, not sliced"
    assert out.budget_ok is True, "0s kept against an 18s budget is trivially within budget"


def test_run_cut_provider_failure_is_not_ok():
    fixture = _fixture()
    out = run_cut(MeteredLLM(FakeLLM([LLMError("timed out")])), fixture)
    assert out.ok is False
    assert out.error
    assert out.failure_kind == "limits", "cuts._ai_spans only raises by wrapping an LLMError"


def test_run_cut_unexpected_exception_is_a_bug():
    fixture = _fixture()
    out = run_cut(MeteredLLM(FakeLLM([KeyError("segments")])), fixture)
    assert out.ok is False
    assert out.failure_kind == "bug"


# ---------------------------------------------------------------- MeteredLLM + evaluate()


def test_metered_llm_counts_calls_chars_and_forwards_settings():
    inner = FakeLLM([{"ranges": []}])
    inner.max_input_chars = 12_345
    inner.model = "test/model"
    llm = MeteredLLM(inner)
    assert llm.model == "test/model"
    assert llm.max_input_chars == 12_345
    llm.complete_json(system="sys", user="user", schema={}, max_tokens=10)
    assert llm.calls == 1
    assert llm.input_chars == len("sys") + len("user")
    assert llm.seconds >= 0.0


def test_evaluate_aggregates_two_fixtures_into_one_row():
    fixture_a, fixture_b = _fixture("fx-a"), _fixture("fx-b")
    bad_chapter = _brief_answer(chapter_ids=("1", "999", "5"))
    responses = [
        _brief_answer(),                                    # fx-a brief: clean
        _cut_answer(("b", "b", 3), ("c", "c", 3)),         # fx-a cut: clean, both must-keep hit
        bad_chapter, _brief_answer(),                        # fx-b brief: one repair
        _cut_answer(("e", "e", 3)),                        # fx-b cut: keeps the forbidden segment
    ]
    llm = FakeLLM(responses)
    report = evaluate("groq", "test/model", llm, [fixture_a, fixture_b])
    row = report.row()
    assert row["fixtures"] == 2
    assert row["valid_json_first_try"] == 1        # fx-a only
    assert row["repaired"] == 1                     # fx-b
    assert row["brief_failed"] == 0
    assert row["brief_limits"] == row["brief_rejected"] == row["brief_bug"] == 0
    assert row["verdict_agreement"] == 2            # both said HEAR, matching the label
    assert row["forbidden_segments_kept"] == 1      # fx-b's cut kept "e"
    assert row["not_recommended"] is True
    assert row["must_keep_hit"] == 2 and row["must_keep_expected"] == 4
    assert row["cut_failed"] == 0
    assert row["cut_limits"] == row["cut_bug"] == 0
    assert row["seconds"] >= 0.0
    assert row["input_tokens"] > 0
    assert report.failures() == []                 # nothing failed: nothing to diagnose


def test_evaluate_failures_lists_every_failure_with_its_kind_and_message():
    fixture_a, fixture_b = _fixture("fx-a"), _fixture("fx-b")
    responses = [
        LLMError("You have used up this provider's daily limit."),  # fx-a brief: limits
        _cut_answer(("b", "b", 3)),                                # fx-a cut: ok
        _brief_answer(),                                           # fx-b brief: ok
        LLMError("timed out"),                                     # fx-b cut: limits
    ]
    report = evaluate("groq", "test/model", FakeLLM(responses), [fixture_a, fixture_b])
    row = report.row()
    assert row["brief_failed"] == 1 and row["brief_limits"] == 1
    assert row["cut_failed"] == 1 and row["cut_limits"] == 1

    failures = report.failures()
    assert len(failures) == 2
    brief_failure = next(f for f in failures if f["call"] == "brief")
    cut_failure = next(f for f in failures if f["call"] == "cut")
    assert brief_failure == {"provider": "groq", "model": "test/model", "call": "brief",
                             "episode_id": "fx-a", "failure_kind": "limits",
                             "error": "You have used up this provider's daily limit."}
    assert cut_failure["episode_id"] == "fx-b" and cut_failure["failure_kind"] == "limits"
    assert cut_failure["error"] == "timed out"
