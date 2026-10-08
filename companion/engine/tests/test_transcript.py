"""Every transcript format parses into the one Segment type, with bad times dropped."""
import json
import math

import pytest

from companion.engine import transcript as tr
from companion.engine.transcript import Segment, Word


def spans(segments):
    return [(s.id, s.start, s.end, s.text) for s in segments]


def test_podfetch_segments_fill_missing_ends_and_skip_untimed():
    payload = {"segments": [
        {"idx": 0, "startMs": 500, "endMs": 4200, "speaker": "Alice", "text": "Hello world"},
        {"idx": 1, "startMs": 4200, "endMs": None, "text": "no end, runs to the next start"},
        {"idx": 2, "startMs": None, "endMs": None, "text": "untimed line"},
        {"idx": 3, "startMs": 9000, "text": "last line has no end"},
    ]}
    segs = tr.from_podfetch(payload)
    assert spans(segs)[:2] == [("0", 0.5, 4.2, "Hello world"),
                               ("1", 4.2, 9.0, "no end, runs to the next start")]
    assert segs[0].speaker == "Alice"
    assert [s.id for s in segs] == ["0", "1", "3"]
    last = segs[-1]
    assert last.start == 9.0 and 9.0 < last.end <= 9.0 + tr.TAIL_MAX
    # clamped to the file when its duration is known
    assert tr.from_podfetch(payload, duration=10.0)[-1].end == 10.0


def test_podfetch_word_level_is_grouped_into_phrases_that_keep_their_words():
    words = "so today we look at bgp. the route selection process starts with weight.".split()
    payload = [{"idx": i, "startMs": i * 400, "endMs": i * 400 + 350, "text": w}
               for i, w in enumerate(words)]
    segs = tr.from_podfetch(payload)
    assert 1 < len(segs) < len(words)
    assert segs[0].id == "0" and segs[0].text.startswith("so today we look at bgp.")
    assert sum(len(s.words) for s in segs) == len(words)
    assert segs[1].start == 2.4 and segs[1].words[0] == Word("the", 2.4, 2.75)
    assert len(tr.from_podfetch(payload, group=False)) == len(words)


def test_timed_json_from_the_library_attaches_top_level_words():
    doc = {"duration": 30, "source": "groq", "segments": [
        {"start": 0, "end": 10, "text": "A subnet divides a network."},
        {"start": 10, "end": 20, "text": "  A prefix   defines its size. "},
        {"start": 25, "end": 40, "text": "past the end is clamped"},
        {"start": float("nan"), "end": 5, "text": "nan start"},
    ], "words": [{"word": "A", "start": 0.1, "end": 0.3}, {"word": "subnet", "start": 0.3, "end": 0.8},
                 {"word": "prefix", "start": 10.4, "end": 10.9}]}
    segs = tr.from_timed_json(json.dumps(doc))
    assert spans(segs) == [("seg-0001", 0.0, 10.0, "A subnet divides a network."),
                           ("seg-0002", 10.0, 20.0, "A prefix defines its size."),
                           ("seg-0003", 25.0, 30.0, "past the end is clamped")]
    assert [w.text for w in segs[0].words] == ["A", "subnet"]
    assert [w.text for w in segs[1].words] == ["prefix"]


VTT = (
    "﻿WEBVTT Kind: captions\r\nLanguage: en\r\n\r\n"
    "STYLE\r\n::cue { color: red }\r\n\r\n"
    "NOTE a comment\r\nthat spans two lines\r\n\r\n"
    "1\r\n00:00.500 --> 00:04.200 align:start position:0%\r\n<v.loud Alice>Hello &amp; <b>world</b></v>\r\n\r\n"
    "2\r\n00:00:04.200 --> 00:00:08.000\r\n<v Bob>Nice to meet you\r\nthat continues <00:00:06.000><c>here</c>\r\n"
    "00:08.000 --> 00:09.500\r\nno blank line before this cue\r\n\r\n"
    "4\r\n00:09.500 -> 00:10.000\r\nbroken arrow is not a cue\r\n\r\n"
    "5\r\n00:1x.000 --> 00:12.000\r\nbroken time is skipped, never 0:00\r\n\r\n"
    "6\r\n00:12.000 --> 00:11.000\r\nreversed is dropped\r\n"
)


def test_vtt_blocks_cue_ids_voices_and_broken_cues():
    segs = tr.parse_vtt(VTT)
    assert spans(segs) == [
        ("seg-0001", 0.5, 4.2, "Hello & world"),
        ("seg-0002", 4.2, 8.0, "Nice to meet you that continues here"),
        ("seg-0003", 8.0, 9.5, "no blank line before this cue"),
    ]
    assert [s.speaker for s in segs] == ["Alice", "Bob", None]


def test_srt_with_comma_decimals_and_tags():
    body = ("1\r\n00:00:00,500 --> 00:00:04,200\r\n<i>Hello world</i>\r\n\r\n"
            "2\r\n00:00:04,200 --> 00:00:08,000\r\n{\\an8}Nice to meet you\r\nsecond line\r\n")
    assert spans(tr.parse_srt(body)) == [("seg-0001", 0.5, 4.2, "Hello world"),
                                         ("seg-0002", 4.2, 8.0, "Nice to meet you second line")]


def test_podcasting2_json_phrases_and_word_level():
    phrases = {"version": "1.0.0", "segments": [
        {"speaker": "Alice", "startTime": 0.5, "endTime": 4.2, "body": "Hello world"},
        {"speaker": "Bob", "startTime": 4.2, "body": "Nice to meet you"},
        {"speaker": "Bob", "startTime": 9.0, "endTime": 11.0, "body": "Bye"},
    ]}
    segs = tr.parse_json(json.dumps(phrases))
    assert spans(segs) == [("seg-0001", 0.5, 4.2, "Hello world"), ("seg-0002", 4.2, 9.0, "Nice to meet you"),
                           ("seg-0003", 9.0, 11.0, "Bye")]
    words = {"segments": [{"speaker": "Vader", "startTime": 0.5 + i * 0.3, "endTime": 0.75 + i * 0.3,
                           "body": w} for i, w in enumerate("I am your father . Search your feelings".split())]}
    grouped = tr.parse_json(words)
    assert len(grouped) < 8 and grouped[0].speaker == "Vader"
    assert " ".join(s.text for s in grouped) == "I am your father . Search your feelings"
    assert sum(len(s.words) for s in grouped) == 8


def test_clean_drops_nan_negative_and_reversed_and_clamps_to_duration():
    raw = [Segment("a", 0.0, 2.0, "ok"), Segment("b", math.nan, 3.0, "nan"),
           Segment("c", -1.0, 3.0, "negative"), Segment("d", 5.0, 4.0, "reversed"),
           Segment("e", 6.0, 6.0, "empty"), Segment("f", 8.0, 12.0, "clamped"),
           Segment("g", 11.0, 13.0, "after the end"), Segment("h", 3.0, 4.0, "   ")]
    assert spans(tr.clean(raw, duration=10.0)) == [("a", 0.0, 2.0, "ok"), ("f", 8.0, 10.0, "clamped")]


def test_parse_sniffs_every_format():
    podfetch = json.dumps({"segments": [{"idx": 0, "startMs": 0, "endMs": 1000, "text": "pf"}]})
    p20 = json.dumps({"segments": [{"startTime": 0, "endTime": 1, "body": "p20"}]})
    timed = json.dumps({"segments": [{"start": 0, "end": 1, "text": "timed"}]})
    srt = "1\n00:00:00,000 --> 00:00:01,000\nsrt\n"
    for body, fmt, text in ((VTT, "vtt", "Hello & world"), (srt, "srt", "srt"), (podfetch, "podfetch", "pf"),
                            (p20, "json", "p20"), (timed, "timed", "timed")):
        assert tr.sniff(body) == fmt
        assert tr.parse(body)[0].text == text
    assert tr.parse("just words, no timing") == []
    assert tr.sniff(b"", "text/vtt") == "vtt"
    with pytest.raises(tr.TranscriptError):
        tr.parse("{not json", "json")


def test_as_segments_accepts_dicts_and_segments():
    segs = tr.as_segments([{"start": 1, "end": 2, "text": "one"}, Segment("x", 3, 4, "two"),
                           {"id": "keep", "start": 5, "end": 6, "text": "three"}, "junk",
                           {"start": "bad", "end": 2, "text": "dropped"}])
    assert [s.id for s in segs] == ["seg-0001", "x", "keep"]
    assert segs[0].to_dict() == {"id": "seg-0001", "start": 1.0, "end": 2.0, "text": "one"}
    assert tr.hms(59) == "00:59" and tr.hms(3725) == "1:02:05"


def test_sentences_split_at_a_word_that_ends_one_and_join_segments_that_do_not():
    words = tuple(Word(w, 10 + i * 0.5, 10.4 + i * 0.5) for i, w in enumerate("It's fully baked. It's done, right?".split()))
    segs = [Segment("s1", 0.0, 4.0, "Well, together, Holly and I explore networking"),
            Segment("s2", 4.1, 7.0, "with an eye to the past."),
            Segment("s3", 10.0, 13.0, "It's fully baked. It's done, right?", words),
            Segment("s4", 16.0, 17.0, "No pause before this one")]
    lines = tr.sentences(segs)
    assert [(l.id, l.start, l.end, l.text) for l in lines] == [
        ("s1", 0.0, 7.0, "Well, together, Holly and I explore networking with an eye to the past."),
        ("s3", 10.0, 11.4, "It's fully baked."),
        ("s3.2", 11.5, 13.0, "It's done, right?"),
        ("s4", 16.0, 17.0, "No pause before this one")]
    assert [n for _piece, n in tr.sentence_pieces(segs)] == [0, 0, 1, 2, 3]


def test_a_sentence_stops_growing_at_a_pause_a_new_speaker_or_its_length_limit():
    segs = [Segment("a", 0.0, 2.0, "no punctuation here"), Segment("b", 4.0, 6.0, "after a long pause"),
            Segment("c", 6.1, 8.0, "another voice", speaker="Guest"), Segment("d", 8.1, 40.0, "long ramble"),
            Segment("e", 40.1, 42.0, "keeps going")]
    assert [l.id for l in tr.sentences(segs)] == ["a", "b", "c", "d", "e"]
