"""Timing checks against the downloaded file: overruns, origins, and spot checks that find a
shared offset or flag ads inserted mid-episode."""
import pytest

from companion.engine.align import TimingCheck, check_timing, duration_agrees, spot_check

MEDIA = {"source_url": "https://example.invalid/ep.mp3", "sha256": "a" * 64, "byte_length": 10,
         "duration_seconds": 600.0}


def transcript(seconds=600, step=10, per=25):
    """A transcript whose every word is unique, so any window can be found exactly."""
    return [{"id": f"s{i}", "start": float(t), "end": float(t + step),
             "text": " ".join(f"w{i}x{k}" for k in range(per))}
            for i, t in enumerate(range(0, seconds, step))]


def heard_at(segments, source_start, seconds=15.0):
    """The words the transcript places in [source_start, +seconds), as a speech-to-text window."""
    words = []
    for seg in segments:
        per = len(seg["text"].split())
        for k, word in enumerate(seg["text"].split()):
            at = seg["start"] + (seg["end"] - seg["start"]) * k / per
            if source_start <= at < source_start + seconds:
                words.append(word)
    return " ".join(words)


def test_check_timing_flags_a_transcript_that_runs_past_the_file():
    long = transcript(700)                           # 700 s of transcript for a 600 s file
    for origin in ("generated", "library", "feed"):
        check = check_timing(long, origin, MEDIA)
        assert check == "mismatch" and check.status == "mismatch"
        assert "past the end" in check.reason
    # a second or two over the end is normal rounding, not a mismatch
    assert check_timing(transcript(600) + [{"start": 600, "end": 601.5, "text": "outro"}],
                        "generated", MEDIA) == "ok"


def test_check_timing_by_origin():
    segs = transcript()
    assert check_timing(segs, "generated", MEDIA) == "ok"
    assert check_timing(segs, "feed", MEDIA) == "unverified"
    assert check_timing(segs, "library", MEDIA, made_from=dict(MEDIA)) == "ok"
    other_file = dict(MEDIA, sha256="b" * 64)
    assert check_timing(segs, "library", MEDIA, made_from=other_file) == "unverified"
    shorter = dict(other_file, duration_seconds=570.0)             # 30 s of ads fewer
    check = check_timing(segs[:50], "library", MEDIA, made_from=shorter)
    assert check == "mismatch" and "09:30" in check.reason
    assert check_timing(segs, "library", MEDIA) == "unverified"    # no made_from identity
    assert check_timing([], "generated", MEDIA) == "unverified"
    assert check_timing(segs, "feed", None) == "unverified"        # no file to compare yet


def test_spot_check_finds_a_shared_30_second_offset():
    segs = transcript()
    heard = [(t + 30.0, heard_at(segs, t)) for t in (5.0, 300.0, 560.0)]   # 30 s of pre-roll added
    check = spot_check(segs, heard, duration=630.0)
    assert check == "ok"
    assert check.offset == pytest.approx(30.0, abs=1.0)
    assert check.segments[0].start == pytest.approx(30.0, abs=1.0)
    assert check.segments[-1].end <= 630.0
    assert "30 s later" in check.reason


def test_spot_check_flags_two_different_offsets():
    segs = transcript()
    heard = [(5.0, heard_at(segs, 5.0)), (330.0, heard_at(segs, 300.0)), (590.0, heard_at(segs, 560.0))]
    check = spot_check(segs, heard)
    assert check == "mismatch" and "ads were probably inserted" in check.reason


def test_spot_check_tolerates_speech_to_text_differences():
    segs = transcript()
    heard = []
    for t in (5.0, 300.0, 560.0):
        words = heard_at(segs, t).split()
        words[3] = "uh"                                  # a misheard word
        del words[10]                                    # a dropped word
        heard.append({"start": t, "text": " ".join(words)})
    check = spot_check(segs, heard)
    assert check == "ok" and check.offset == 0.0 and check.segments[0].start == 0.0


def test_spot_check_needs_enough_windows_it_can_find():
    segs = transcript()
    heard = [(5.0, heard_at(segs, 5.0)), (300.0, "music plays with no words anyone said"), (560.0, "")]
    check = spot_check(segs, heard)
    assert check == "unverified" and "Only 1 of 3" in check.reason
    assert spot_check([], heard) == "unverified"


def test_timing_check_is_a_plain_string_with_a_reason():
    check = TimingCheck("ok", "fine")
    assert check == "ok" and isinstance(check, str) and check.reason == "fine"
    with pytest.raises(ValueError):
        TimingCheck("maybe")


def test_duration_agrees_catches_a_stretched_timeline():
    assert duration_agrees(965.0, 960.0)
    assert not duration_agrees(1681.0, 965.0)            # what opus input did to Groq's timeline
