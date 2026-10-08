"""Native skip evidence, safe boundaries and preservation of real teaching."""
import pytest

from companion.engine.ads import ad_spans
from companion.engine.skipping import MAX_BLOCK, skip_spans


def cue(start, end, text, id="line"):
    return {"id": id, "start": start, "end": end, "text": text}


def test_unknown_sponsor_includes_the_read_and_stops_before_the_lesson():
    lines = [cue(0, 10, "Welcome to our podcast.", "hello"),
             cue(10, 20, "This episode is sponsored by a brand new company, Acme.", "ad-1"),
             cue(20, 30, "Their service helps your team collaborate.", "body"),
             cue(30, 40, "Visit acme.example for a free trial.", "offer"),
             cue(40, 45, "Thanks to our sponsor for supporting us.", "thanks"),
             cue(45, 70, "Now we explain how route selection works.", "lesson")]
    sponsor = [s for s in skip_spans(lines, duration=70) if s.category == "sponsor"]
    assert [(s.start, s.end) for s in sponsor] == [(10, 45)]
    assert sponsor[0].segment_ids == ("ad-1", "body", "offer", "thanks")
    assert "explicitly" in sponsor[0].reason
    assert ad_spans(lines) == [(10, 45)]


@pytest.mark.parametrize("text", [
    "Learn more about OSPF route selection.",
    "Opengear and Kentik expose APIs; this lesson compares the vendors.",
    "The open source project has a sponsor, but the protocol is independent.",
    "Use the code example to understand a recursive function.",
    "A free trial does not prove that this network tool works in production.",
    "Visit docs.example.com/networking to read the protocol reference.",
    "The packet uses a subscription to receive route updates.",
    "For example, a host might say this episode is sponsored by Acme.",
    "This episode is sponsored by Acme. Now we explain BGP route selection.",
])
def test_teaching_and_weak_promotional_hints_are_not_skipped(text):
    assert ad_spans([cue(100, 115, text)]) == []


def test_two_ads_never_bridge_a_return_to_teaching():
    lines = [cue(20, 25, "Sponsored by Acme.", "ad-1"),
             cue(25, 30, "Now we explain the actual protocol.", "lesson"),
             cue(30, 35, "Use promo code NET.", "ad-2")]
    assert ad_spans(lines) == [(20, 25), (30, 35)]


def test_two_ads_never_bridge_a_quoted_teaching_example():
    lines = [cue(20, 25, "Sponsored by Acme.", "ad-1"),
             cue(25, 30, "For example, a host can sell ad slots while discussing media economics.", "lesson"),
             cue(30, 35, "Use promo code NET.", "ad-2")]
    assert ad_spans(lines) == [(20, 25), (30, 35)]


@pytest.mark.parametrize("category,start,text", [
    ("sponsor", 100, "This podcast is sponsored by Acme."),
    ("selfpromo", 100, "Support our show by becoming a member."),
    ("interaction", 100, "Please subscribe to our podcast."),
    ("intro", 0, "You're listening to the Example Podcast."),
    ("outro", 290, "Thanks for listening, see you next week."),
    ("preview", 100, "In our next episode we visit the data center."),
    ("filler", 100, "[off-topic discussion]"),
    ("music_offtopic", 100, "[spoken interlude]"),
    ("intro", 0, "[intro music]"),
    ("outro", 290, "[outro music]"),
])
def test_category_choices_are_backed_by_explicit_source_cues(category, start, text):
    spans = skip_spans([cue(start, start + 10, text, "evidence")], duration=300)
    assert len(spans) == 1
    assert spans[0].category == category
    assert spans[0].segment_ids == ("evidence",)
    assert spans[0].reason


def test_outro_in_the_middle_and_lesson_preview_are_kept():
    assert skip_spans([cue(100, 110, "Thanks for listening to that example.")], duration=600) == []
    assert skip_spans([cue(100, 110, "In the next section we explain BGP.")], duration=600) == []


def test_invalid_or_oversized_timing_cannot_create_a_skip():
    text = "This episode is sponsored by Acme."
    assert skip_spans([cue(-1, 10, text), cue(10, float("nan"), text), cue(20, 10, text)]) == []
    assert skip_spans([cue(0, MAX_BLOCK + 1, text)]) == []
    assert skip_spans([cue(90, 110, text)], duration=100) == []


def test_only_an_actual_opening_ad_can_expand_to_zero():
    lines = [cue(0, 8, "Important corrections to the previous lesson.", "correction"),
             cue(8, 14, "This episode is sponsored by Acme.", "ad")]
    assert ad_spans(lines) == [(8, 14)]


def test_repeated_ad_cues_are_bounded_without_guessed_timestamps():
    lines = [cue(i * 20, (i + 1) * 20, "Use promo code SAVE.", str(i)) for i in range(25)]
    spans = skip_spans(lines)
    assert len(spans) > 1
    assert all(span.end - span.start <= MAX_BLOCK for span in spans)
    assert sum(span.end - span.start for span in spans) == 500


def test_late_first_cue_cannot_expand_a_maximum_size_read_past_the_cap():
    span = skip_spans([cue(10, 190, "This episode is sponsored by Acme.")])[0]
    assert (span.start, span.end) == (10, 190)


@pytest.mark.parametrize("text", [
    "Our sponsor Atlas is a solid option for your team.",
    "Let's take a moment to hear from our sponsor, Atlas.",
])
def test_explicit_host_read_cues_do_not_need_a_brand_list(text):
    spans = skip_spans([cue(100, 110, text)])
    assert [(span.start, span.end, span.category) for span in spans] == [(100, 110, "sponsor")]


def test_a_contiguous_long_read_joins_its_closing_offer_but_not_the_following_lesson():
    lines = [cue(100, 104, "Let's hear from our sponsor, Atlas.", "open"),
             cue(104, 120, "Their team can manage your infrastructure.", "body-1"),
             cue(120, 138, "The service includes hardware, software and support.", "body-2"),
             cue(138, 158, "They can help you migrate and replace older equipment.", "body-3"),
             cue(158, 164, "Visit atlas.example to book a demo.", "close"),
             cue(165, 190, "BGP address families identify different kinds of reachability.", "lesson")]
    assert ad_spans(lines) == [(100, 164)]
    assert skip_spans(lines)[0].segment_ids == ("open", "body-1", "body-2", "body-3", "close")


@pytest.mark.parametrize("middle", [
    "Now we explain address families in BGP.",
    "Now    we\nexplain address families in BGP.",
    "For example, sponsorship contracts can fund independent media.",
])
def test_long_read_cannot_bridge_explicit_teaching(middle):
    lines = [cue(100, 105, "Let's hear from our sponsor, Atlas."),
             cue(105, 160, middle), cue(160, 170, "Visit atlas.example and use promo code SAVE.")]
    assert ad_spans(lines) == [(100, 105), (160, 170)]


def test_missing_cues_cannot_turn_a_long_gap_into_an_ad_body():
    lines = [cue(100, 105, "Let's hear from our sponsor, Atlas."),
             cue(140, 155, "Their service helps teams."), cue(155, 160, "Visit atlas.example and use promo code SAVE.")]
    assert ad_spans(lines) == [(100, 105), (155, 160)]


@pytest.mark.parametrize("text", [
    "Our sponsor policy is to refuse paid product endorsements.",
    "Our sponsor contract is an example for this lesson.",
    "Go to the lab to launch a demo topology.",
    "For example, an advertiser might tell you to book a demo.",
    "We book a demo to examine how the routing interface works.",
    "Visit the lab website to book a demo.",
])
def test_sponsorship_lessons_and_ordinary_demos_remain_content(text):
    assert ad_spans([cue(100, 110, text)]) == []


def test_demo_close_cannot_extend_an_ad_past_a_return_to_the_lesson():
    lines = [cue(100, 105, "Let's hear from our sponsor, Atlas."),
             cue(105, 160, "Now we explain address families in BGP."),
             cue(160, 170, "Visit the lab website to book a demo.")]
    assert ad_spans(lines) == [(100, 105)]


def test_a_demo_offer_can_close_a_disclosed_read_with_curly_apostrophes():
    lines = [cue(100, 105, "Let's hear from today’s sponsor, Atlas."),
             cue(105, 145, "Their service helps your team manage infrastructure."),
             cue(145, 150, "Atlas dot example to book a demo.")]
    assert ad_spans(lines) == [(100, 150)]
