"""The listen-back check on hand-made "heard" words: a clean cut passes; a clipped first word,
a leaked word of the part you cut, a piece from the wrong place and dead air at a join are
found where they are; speech-to-text wording noise inside a piece is not an error."""
import pytest

from companion.engine import verify
from companion.engine.render import Envelope
from companion.engine.verify import Heard, Piece

ONE = "so the router picks the route with the highest weight first then local preference decides"
TWO = "next the shortest as path wins and after that the lowest origin type is preferred by bgp"


def piece(number, text, cut_start, source_start=100.0, seconds=None):
    words = verify.tokens(text)
    seconds = seconds or len(words) * 0.4 + 0.4                 # a short lead and tail
    timed = [(w, source_start + seconds * k / len(words)) for k, w in enumerate(words)]
    return Piece(number, "ep", cut_start, cut_start + seconds, source_start, timed)


def said(text, start, step=0.4):
    return [Heard(w, start + k * step, start + k * step + 0.3) for k, w in enumerate(text.split())]


def kinds(result):
    return sorted((f.kind, f.piece, f.edge) for f in result.findings)


def test_tokens_compare_words_not_spelling():
    assert verify.tokens("We've got OK, um, twenty-one BGP-related RFCs.") == \
        ["weve", "got", "ok", "20", "1", "bgp", "related", "rfcs"]
    assert verify.tokens("Okay uh") == ["ok"]


def test_a_clean_cut_passes_and_counts_its_words():
    pieces = [piece(1, ONE, 0.0), piece(2, TWO, 6.4, source_start=300.0)]
    heard = said(ONE, 0.2) + said(TWO, 6.6)
    result = verify.check_words(pieces, heard, [(0.0, 13.6)])
    assert result.findings == [] and result.expected == result.matched == 32
    assert result.judged == {(1, "start"), (1, "end"), (2, "start"), (2, "end")}


def test_a_clipped_first_word_and_a_leaked_word_are_found_at_their_join():
    pieces = [piece(1, ONE, 0.0), piece(2, TWO, 6.4, source_start=300.0)]
    heard = said(ONE, 0.2) + said("okay " + TWO.split(" ", 2)[2], 6.6)     # "next the" lost, "okay" leaked in
    result = verify.check_words(pieces, heard, [(0.0, 13.6)])
    assert kinds(result) == [("clipped_start", 2, "start"), ("extra_start", 2, "start")]
    clipped = next(f for f in result.findings if f.kind == "clipped_start")
    assert clipped.words == ("next", "the") and clipped.cut_time == 6.4
    assert clipped.detail["source_time"] == 300.0 and "starts too late" in clipped.public()["message"]
    extra = next(f for f in result.findings if f.kind == "extra_start")
    assert extra.words == ("ok",)


def test_a_clipped_last_word_is_found():
    pieces = [piece(1, ONE, 0.0)]
    heard = said(ONE.rsplit(" ", 1)[0], 0.2)                                   # "decides" lost
    result = verify.check_words(pieces, heard, [(0.0, 7.0)])
    assert kinds(result) == [("clipped_end", 1, "end")] and result.findings[0].words == ("decides",)


def test_wording_noise_inside_a_piece_is_a_warning_and_a_piece_from_elsewhere_is_an_error():
    noisy = ONE.replace("highest weight", "hi his wait")
    result = verify.check_words([piece(1, ONE, 0.0)], said(noisy, 0.2), [(0.0, 7.0)])
    assert [f.severity for f in result.findings] == []                           # 13 of 15 words: fine
    wrong = verify.check_words([piece(1, ONE, 0.0)], said(TWO, 0.2), [(0.0, 7.0)])
    assert kinds(wrong) == [("wrong_audio", 1, None)] and wrong.findings[0].severity == "error"


def test_only_edges_inside_what_was_heard_are_judged():
    pieces = [piece(1, ONE, 0.0), piece(2, TWO, 6.4, source_start=300.0)]
    heard = [w for w in said(ONE, 0.2) + said(TWO, 6.6) if 0.3 <= w.start <= 12.2]
    result = verify.check_words(pieces, heard, [(0.3, 12.5)])
    assert result.judged == {(1, "end"), (2, "start")} and result.findings == []
    assert result.expected == 0                                                  # no piece was heard whole


def test_dead_air_at_a_join_and_a_join_without_a_pause():
    pieces = [piece(1, ONE, 0.0), piece(2, TWO, 6.4, source_start=300.0), piece(3, ONE, 12.8, source_start=500.0)]
    speech, quiet = -14.0, -70.0
    # 1.5 s of silence around the join at 6.4; none at all at 12.8
    before = Envelope(1.4, 0.01, tuple([speech] * 440 + [quiet] * 150 + [speech] * 410))
    tight = Envelope(7.8, 0.01, tuple([speech] * 1000))
    found = verify.check_joins(pieces, {2: before, 3: tight}, 19.2)
    assert [(f.kind, f.piece, f.severity) for f in found] == [("dead_air", 2, "error"), ("no_pause", 3, "warning")]
    assert found[0].seconds == pytest.approx(1.5, abs=0.02)
    assert found[0].detail["before"] == pytest.approx(0.6, abs=0.02) and found[0].detail["after"] == pytest.approx(0.9, abs=0.02)


def test_silence_at_the_very_end_is_dead_air_too():
    pieces = [piece(1, ONE, 0.0)]
    end = Envelope(1.4, 0.01, tuple([-14.0] * 300 + [-80.0] * 200))
    found = verify.check_joins(pieces, {0: end}, 6.4)
    assert [(f.kind, f.edge) for f in found] == [("dead_air", "end")] and found[0].seconds == pytest.approx(2.0, abs=0.02)


def test_a_line_with_no_words_expects_nothing_and_does_not_fail():
    assert verify.expected_words([(0.0, 4.0, "♪♪♪"), (4.0, 5.0, "...")], 0.0, 5.0) == []


def test_a_piece_that_holds_part_of_a_long_line_expects_only_the_words_it_holds():
    # one unpunctuated 30 s cue, cut at 10-20 s (no sentence edge within reach)
    line = (0.0, 30.0, " ".join(f"w{k}" for k in range(30)))
    words = verify.expected_words([line], 10.0, 20.0)
    assert [token for token, _t in words] == [f"w{k}" for k in range(10, 20)]
    cut = Piece(1, "ep", 0.0, 10.0, 10.0, words)
    heard = [Heard(f"w{k}", k - 10 + 0.1, k - 10 + 0.4) for k in range(10, 20)]
    assert verify.check_words([cut], heard, [(0.0, 10.0)]).findings == []


def test_words_leaked_past_a_join_are_found_by_their_order_not_their_garbled_times():
    # measured on N4N032: the service stretched "area." over the next words and started "It" before it
    one = piece(1, "forced through the backbone area", 0.0, seconds=3.0)
    two = piece(2, "and this is one of those esoteric things", 3.3, source_start=500.0, seconds=4.0)
    heard = [Heard("forced", 0.1, 0.4), Heard("through", 0.4, 0.6), Heard("the", 0.6, 0.7), Heard("backbone", 0.7, 1.0),
             Heard("area.", 1.0, 1.8), Heard("It", 0.8, 1.56), Heard("does", 1.56, 1.76)]
    heard += said("and this is one of those esoteric things", 3.5, step=0.45)
    result = verify.check_words([one, two], heard, [(0.0, 7.3)])
    assert kinds(result) == [("extra_end", 1, "end")] and result.findings[0].words == ("it", "does")
