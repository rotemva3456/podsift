"""Keyword plans and model-picked ranges: exclusions never come back, spans stay inside the
episode, the budget holds, and planning never touches the network."""
import pytest

from companion.engine import select
from companion.engine.align import TimingCheck
from companion.engine.select import SelectionError, plan, select_ranges, spans_for, widen
from companion.engine.transcript import Segment

AUDIO = "https://example.invalid/episode.mp3"


def episode(segments, duration=60, **extra):
    return {"episode_id": "ep-1", "audio": AUDIO, "duration": duration, "n": 1,
            "title": "Synthetic teaching fixture", "segments": segments, **extra}


def times(spans):
    return [(span["start"], span["end"]) for span in spans]


# --- ported from the command-line tool's plan tests -----------------------------------------

def test_excluded_interval_is_never_reintroduced_by_merge_or_padding():
    ep = episode([
        {"start": 0, "end": 6, "text": "subnetting setup"},
        {"start": 10, "end": 14, "text": "ipv6 excluded material"},
        {"start": 20, "end": 26, "text": "subnetting example"},
    ])
    _terms, spans, _seconds = spans_for("subnetting", [ep], drop="ipv6")
    assert times(spans) == [(0.0, 10.0), (14.0, 32.0)]
    assert all(span["end"] <= 10 or span["start"] >= 14 for span in spans)


def test_padding_is_clamped_to_the_source_duration():
    ep = episode([{"start": 58, "end": 60, "text": "subnetting conclusion"}])
    _terms, spans, _seconds = spans_for("subnetting", [ep])
    assert times(spans) == [(52.0, 60.0)]


def test_changed_media_url_rejects_stale_timing():
    ep = episode([{"start": 1, "end": 8, "text": "subnetting"}],
                 media={"source_url": "https://example.invalid/old.mp3"})
    with pytest.raises(ValueError, match="different source URL"):
        spans_for("subnetting", [ep])
    result = plan("subnetting", [ep])
    assert result.status == "needs_timing" and "different source URL" in result.needs_timing[0]["reason"]


def test_plan_reports_missing_timing_without_the_network(offline):
    untimed = episode([], episode_id="ep-2")
    timed = episode([{"start": 1, "end": 8, "text": "subnetting basics"}])
    result = plan("subnetting", [untimed, timed])
    assert result.needs_timing == [{"episode_id": "ep-2", "reason": "This episode has no timed transcript yet."}]
    assert result.status == "ready" and times(result.spans) == [(0.0, 14.0)]
    assert plan("subnetting", [untimed]).status == "needs_timing"
    assert offline == []


# --- new guarantees ------------------------------------------------------------------------

def test_no_span_runs_past_the_episode_end():
    # the transcript claims more than the file holds: 70 s of text for a 45 s file
    ep = episode([{"start": 30, "end": 44, "text": "bgp route selection"},
                  {"start": 44, "end": 70, "text": "bgp weight and local preference"}], duration=45)
    second = episode([{"start": 0, "end": 5, "text": "bgp at the very start"}], duration=20,
                     episode_id="ep-2")
    for pad in (0.0, 6.0, 30.0):
        _terms, spans, _secs = spans_for("bgp", [ep, second], pad=pad)
        assert spans
        for span in spans:
            limit = 45 if span["episode_id"] == "ep-1" else 20
            assert 0 <= span["start"] < span["end"] <= limit


def test_exclude_intervals_such_as_ads_are_barriers():
    ep = episode([{"start": 0, "end": 10, "text": "ospf areas"},
                  {"start": 10, "end": 20, "text": "this episode is sponsored by acme"},
                  {"start": 20, "end": 30, "text": "ospf lsa types"}], exclude=[(10, 20)])
    _terms, spans, _secs = spans_for("ospf", [ep], pad=8)
    assert times(spans) == [(0.0, 10.0), (20.0, 38.0)]


def test_plan_budget_keeps_the_densest_spans_and_reports_the_rest():
    segs = [{"start": 0, "end": 30, "text": "vlan"},
            {"start": 100, "end": 110, "text": "vlan vlan vlan trunk vlan"},
            {"start": 200, "end": 260, "text": "vlan once in a long ramble"}]
    result = plan("vlan", [episode(segs, duration=300)], minutes=1, pad=0)
    assert times(result.spans) == [(0.0, 30.0), (100.0, 110.0)]
    assert result.kept_seconds == 40.0 and result.source_seconds == 300.0
    assert result.omitted == [{"episode_id": "ep-1", "start": 200.0, "end": 260.0,
                               "reason": "over the time budget"}]
    assert result.to_dict()["spans"][0]["segment_ids"] == ["seg-0001"]
    assert plan("nothing matches", [episode(segs)]).status == "empty"


def test_timing_mismatch_goes_to_needs_timing():
    ep = episode([{"start": 1, "end": 8, "text": "subnetting"}],
                 timing=TimingCheck("mismatch", "The transcript runs past the end."))
    result = plan("subnetting", [ep])
    assert result.status == "needs_timing"
    assert result.needs_timing[0]["reason"] == "The transcript runs past the end."


def test_spans_come_back_in_episode_order():
    first = episode([{"start": 5, "end": 9, "text": "dns"}], episode_id="a")
    second = episode([{"start": 1, "end": 3, "text": "dns"}], episode_id="b")
    _terms, spans, _secs = spans_for("dns", [first, second], pad=0)
    assert [s["episode_id"] for s in spans] == ["a", "b"]
    _terms, spans, _secs = spans_for("dns", [dict(first, order=2), dict(second, order=1)], pad=0)
    assert [s["episode_id"] for s in spans] == ["b", "a"]


def test_terms_drop_stop_words_and_edge_punctuation():
    assert select.terms("What is BGP? The c++ and 802.1q, -tcp.") == ["bgp", "c++", "802.1q", "tcp"]


def test_planning_makes_zero_network_calls(offline):
    eps = [episode([{"start": i * 10, "end": i * 10 + 8, "text": f"part{i} dns resolver"}
                    for i in range(6)], episode_id=f"ep-{n}") for n in range(3)]
    result = plan("dns resolver", eps, skip="part3", minutes=1)
    spans, _omitted = select_ranges(eps[0]["segments"], [("seg-0001", "seg-0003")], ["seg-0002"], 20)
    assert result.spans and spans
    assert offline == []


# --- model-picked ranges -------------------------------------------------------------------

SEGS = [{"id": str(i), "start": i * 10.0, "end": i * 10.0 + 10, "text": f"line {i}"} for i in range(10)]


def test_select_ranges_unknown_or_backwards_ids_fail():
    with pytest.raises(SelectionError, match="Unknown segment id '42'"):
        select_ranges(SEGS, [{"start_id": "1", "end_id": "42"}])
    with pytest.raises(SelectionError, match="runs backwards"):
        select_ranges(SEGS, [("5", "2")])
    with pytest.raises(SelectionError, match="exclusions"):
        select_ranges(SEGS, [("1", "2")], exclude=["nope"])
    with pytest.raises(SelectionError):
        select_ranges(SEGS, [{"why": "no ids"}])


def test_select_ranges_exclusions_win_and_overlaps_merge():
    ranges = [{"start_id": "1", "end_id": "4", "why": "the setup"},
              {"start_id": "3", "end_id": "6", "why": "the example"},
              {"start_id": 8, "end_id": 8}]
    spans, omitted = select_ranges(SEGS, ranges, exclude=["5", (82, 85)])
    assert times(spans) == [(10.0, 50.0), (60.0, 70.0), (80.0, 82.0), (85.0, 90.0)]
    assert spans[0]["why"] == "the setup / the example"
    assert spans[0]["segment_ids"] == ["1", "2", "3", "4"] and spans[0]["text"] == "line 1"
    assert omitted == []
    for span in spans:                                   # nothing inside an exclusion survives
        assert span["end"] <= 50 or span["start"] >= 60
        assert span["end"] <= 82 or span["start"] >= 85


def test_select_ranges_budget_holds_in_the_models_order():
    ranges = [("6", "7", "best"), ("0", "3", "too long now"), ("7", "8", "overlaps what is kept")]
    spans, omitted = select_ranges(SEGS, ranges, budget_seconds=30)
    assert times(spans) == [(60.0, 90.0)]
    assert sum(s["end"] - s["start"] for s in spans) <= 30
    assert omitted == [{"start": 0.0, "end": 40.0, "reason": "over the time budget"}]


def test_select_ranges_clamps_to_the_duration_and_drops_slivers():
    spans, omitted = select_ranges(SEGS, [("8", "9")], exclude=[(80.5, 89.6)], duration=95)
    assert times(spans) == [(89.6, 95.0)]
    assert omitted == [{"start": 80.0, "end": 80.5, "reason": "too short once exclusions are removed"}]


def test_widen_adds_context_without_crossing_exclusions_or_the_end():
    spans = [{"start": 10.0, "end": 20.0, "segment_ids": ["1"]},
             {"start": 30.0, "end": 40.0, "segment_ids": ["3"]},
             {"start": 80.0, "end": 95.0, "segment_ids": ["8"]}]
    out = widen(spans, 6, duration=100, exclude=[(24, 27)])
    assert times(out) == [(4.0, 24.0), (27.0, 46.0), (74.0, 100.0)]
    merged = widen(spans, 6, duration=100)
    assert times(merged) == [(4.0, 46.0), (74.0, 100.0)]
    assert merged[0]["segment_ids"] == ["1", "3"]


# --- snapping a span onto whole sentences ------------------------------------------------------

LINES = [Segment(str(i), start, end, f"line {i}.") for i, (start, end) in
         enumerate([(0.0, 8.0), (8.0, 16.0), (16.0, 30.0), (30.5, 34.0), (34.0, 60.0)])]


def test_snap_keeps_whole_sentences_within_eight_seconds():
    assert select.snap(10.0, 25.0, LINES) == (8.0, 30.0)        # both edges grow to their sentence
    assert select.snap(10.0, 20.0, LINES) == (8.0, 16.0)        # 10 s to the sentence's end: drop the part
    assert select.snap(8.0, 16.0, LINES) == (8.0, 16.0)         # already on edges
    assert select.snap(30.2, 32.0, LINES) == (30.2, 34.0)       # a start in the gap between sentences stays


def test_snap_never_crosses_an_exclusion_and_then_drops_the_part_sentence():
    # the sentence before 10.0 holds a skipped stretch: the start moves in to the next sentence
    assert select.snap(10.0, 25.0, LINES, exclude=[(8.5, 9.0)]) == (16.0, 30.0)
    # an end that can't grow into a skipped stretch, nor shrink within 8 s, stays mid-sentence
    assert select.snap(10.0, 28.0, LINES, exclude=[(29.0, 30.0)]) == (8.0, 28.0)
    assert select.snap(10.0, 45.0, LINES) == (8.0, 45.0)        # a 26 s sentence: 15 s out, 11 s in


def test_snap_never_returns_an_empty_span():
    assert select.snap(20.0, 22.0, LINES, exclude=[(16.0, 19.0), (25.0, 26.0)]) == (20.0, 22.0)
