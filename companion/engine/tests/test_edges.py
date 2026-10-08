"""Where a cut goes: into the pause before a passage's first word and after its last one, with
a fade that fits in that pause - found on the audio's loudness, not taken from the transcript.

Envelopes here are built by hand (10 ms frames), except the last test, which measures one."""
import pytest

from companion.engine import edges
from companion.engine.render import Envelope, clips, envelope, probe_duration

from .audio import needs_ffmpeg

SPEECH, PAUSE, BREATH = -14.0, -70.0, -30.0


def env(start, *runs):
    """An envelope from (seconds, level) runs, starting at `start`."""
    levels = []
    for seconds, level in runs:
        levels += [level] * round(seconds / 0.01)
    return Envelope(start, 0.01, tuple(levels))


def test_a_start_goes_into_the_pause_before_the_first_word_keeping_a_short_lead():
    # previous words until 10.0, a 0.6 s pause, the first word at 10.6 (the transcript says 10.65)
    e = env(8.5, (1.5, SPEECH), (0.6, PAUSE), (1.5, SPEECH))
    cut = edges.start_cut(e, 10.65, earliest=9.9)
    assert cut.kind == "pause"
    assert cut.time == pytest.approx(10.6 - edges.LEAD, abs=0.011)
    assert cut.fade <= edges.MAX_FADE and cut.time + cut.fade <= 10.6       # the fade ends before the word


def test_an_end_keeps_a_short_tail_and_a_blip_inside_the_pause_does_not_end_it():
    # the last word ends at 20.0; a 20 ms click at 20.08; the next sentence at 21.0
    e = env(18.5, (1.5, SPEECH), (0.08, PAUSE), (0.02, BREATH), (0.9, PAUSE), (1.5, SPEECH))
    cut = edges.end_cut(e, 19.9, latest=21.1)
    assert cut.kind == "pause" and cut.time == pytest.approx(20.0 + edges.TAIL, abs=0.011)
    assert cut.time - cut.fade >= 20.0


def test_a_late_transcript_time_still_finds_the_pause_before_the_word():
    # the word really starts at 30.0; the transcript says 30.3
    e = env(28.5, (1.1, SPEECH), (0.4, PAUSE), (0.25, SPEECH), (0.08, PAUSE), (1.0, SPEECH))
    cut = edges.start_cut(e, 30.3, earliest=29.45)
    assert cut.time < 30.0 and cut.time >= 29.6


def test_run_together_speech_is_cut_at_the_quietest_moment_nearest_the_planned_time():
    # no pause at all: a dip to -27 dB between two words, and a deeper dip further away
    e = env(39.0, (0.9, SPEECH), (0.03, -27.0), (0.3, SPEECH), (0.03, -29.0), (0.74, SPEECH))
    cut = edges.end_cut(e, 39.93)
    assert cut.kind == "dip" and cut.time == pytest.approx(39.915, abs=0.02) and cut.fade == edges.MIN_FADE


def test_a_flat_signal_leaves_the_edge_where_the_transcript_put_it():
    e = env(0.0, (3.0, SPEECH))
    for cut in (edges.start_cut(e, 1.5), edges.end_cut(e, 1.5)):
        assert (cut.time, cut.kind) == (1.5, "kept")


def test_a_start_never_reaches_back_into_the_words_before_it():
    # the pause before the previous words is the only real one, but they must stay out
    e = env(8.5, (0.3, PAUSE), (1.2, SPEECH), (0.02, -40.0), (1.5, SPEECH))
    cut = edges.start_cut(e, 10.02, earliest=9.85)
    assert cut.time >= 9.85


def test_place_moves_both_edges_and_records_what_it_did():
    lines = [type("L", (), {"start": s, "end": e})() for s, e in ((0.0, 10.0), (10.6, 20.0), (21.0, 30.0))]
    start = env(9.1, (0.9, SPEECH), (0.6, PAUSE), (1.5, SPEECH))
    end = env(18.5, (1.5, SPEECH), (1.0, PAUSE), (1.5, SPEECH))
    piece = edges.place({"start": 10.6, "end": 20.0, "audio": "x"}, start, end, lines)
    assert piece["start"] == pytest.approx(10.4, abs=0.011) and piece["end"] == pytest.approx(20.3, abs=0.011)
    assert piece["edges"]["start"]["planned"] == 10.6 and piece["edges"]["end"]["kind"] == "pause"
    assert 0 < piece["fade_in"] <= edges.MAX_FADE and 0 < piece["fade_out"] <= edges.MAX_FADE


@needs_ffmpeg
def test_envelope_and_clips_read_exact_windows_in_one_decode(tmp_path):
    import subprocess
    src = tmp_path / "beeps.mp3"
    # 1 s of tone, 1 s of silence, 1 s of tone
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,1,2),0,0.5*sin(2*PI*440*t))':s=44100:d=3", "-c:a", "libmp3lame",
                    "-q:a", "3", str(src)], check=True)
    near, past = envelope(src, [(0.5, 2.5), (2.8, 9.0)])
    assert near.start == pytest.approx(0.5) and len(near.levels) == 200
    assert max(near.levels[60:140]) < -60 and min(near.levels[:40]) > -20      # the silence, and tone before it
    assert past.end <= 3.05                                                      # past the end is not a pause
    first, second = clips(src, [(0.0, 1.0), (1.5, 3.0)], tmp_path, name="w")
    # the mp3 encoder pads each file by up to a frame and a half (~0.1 s at 16 kHz)
    assert probe_duration(first) == pytest.approx(1.0, abs=0.12)
    assert probe_duration(second) == pytest.approx(1.5, abs=0.12)
    assert envelope(first, [(0.1, 0.9)])[0].levels and max(envelope(second, [(0.1, 0.4)])[0].levels) < -60
