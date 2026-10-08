"""Sponsor reads are found by name and kept out of teaching cuts."""
from companion.engine.ads import AD_MAX_BLOCK, ad_segment_ids, ad_spans, domain_vocab, teaching_spans


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


EPISODE = [
    seg(3, 10, "This episode is brought to you by Kentik."),
    seg(10, 20, "Head over to kentik.com/packet for a free trial."),
    seg(20, 60, "Today we explain bgp route selection, weight, local preference and as path length."),
    seg(60, 100, "The bgp best path algorithm compares attributes in a fixed order, then med."),
    seg(100, 115, "Let's take a quick break for a word from our sponsor."),
    seg(115, 130, "Use promo code PACKET at checkout."),
    seg(130, 170, "Back to bgp: communities tag prefixes so policy can match them later."),
    seg(170, 200, "Anyway, how was your weekend, did you watch the game?"),
]


def test_ad_spans_find_pre_roll_and_mid_roll():
    assert ad_spans(EPISODE) == [(0.0, 20.0), (100.0, 130.0)]    # pre-roll starts at 0:00
    assert ad_segment_ids(EPISODE) == {"seg-0001", "seg-0002", "seg-0005", "seg-0006"}


def test_an_ad_block_is_capped_and_needs_fresh_hits_to_grow():
    long_read = [seg(i * 20.0, i * 20.0 + 20, "use promo code SAVE") for i in range(20)]
    blocks = ad_spans(long_read)
    assert all(end - start <= AD_MAX_BLOCK for start, end in blocks) and len(blocks) > 1
    spread = [seg(30, 40, "sponsored by acme"), seg(40, 90, "real teaching"), seg(90, 95, "sponsored by acme")]
    assert ad_spans(spread) == [(30.0, 40.0), (90.0, 95.0)]       # a 50 s gap does not join them


def test_teaching_spans_keep_the_dense_part_and_never_the_ads():
    vocab = domain_vocab([s["text"] for s in EPISODE], None, min_ref=1)
    spans = teaching_spans(EPISODE, vocab, keep=0.5, pad=2.0, min_run=5.0, duration=195)
    assert spans
    for span in spans:
        assert span["end"] <= 195
        for start, end in ad_spans(EPISODE):
            assert span["end"] <= start or span["start"] >= end, (span, (start, end))
    kept = sum(span["end"] - span["start"] for span in spans)
    assert kept < 200 - 45                                         # the ads and some chat are gone
