"""Highlights and recap.

Heard status reuses brief.is_heard; the weekly view aggregates across shows; the "what you
learned" paragraph is validated (unknown segment numbers dropped, nothing left to cite fails) and
cached like a brief part, with one repair. PodFetch is an httpx.MockTransport and transcripts come
from a fake loader, so nothing here touches a network or a real AI provider (FakeLLM)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import brief as briefs
from companion import recap as recaps
from companion.deps import PodFetch, get_transcripts
from companion.engine import Segment, Word, as_segments
from companion.llm import FakeLLM, get_llm
from companion.server import create_app

SHOW_A, SHOW_B = "5e1f0000-0000-4000-8000-0000000000a1", "5e1f0000-0000-4000-8000-0000000000b2"
TEXTS = ["Subnetting splits a network into smaller pieces.", "A prefix length sets the subnet size.",
        "CIDR notation writes the prefix length after a slash.", "VLSM lets each subnet have a different size."]


def eid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


RECENT, OLD, UNHEARD, SILENT = eid(1), eid(2), eid(3), eid(4)


def since(days: float) -> str:
    """An ISO timestamp ``days`` ago, computed relative to the real clock so the test never
    depends on which day it happens to run (PodFetch's own history timestamps carry no offset)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def episode(episode_id: str, show: str, name: str, total: float = 200) -> dict:
    return {"id": f"internal-{episode_id}", "episode_id": episode_id, "podcast_id": show, "name": name,
            "url": f"https://example.test/{episode_id}.mp3", "total_time": total,
            "date_of_recording": "2026-09-01T00:00:00+00:00"}


def transcript(episode_id: str, texts: list[str], seconds: float = 10.0) -> dict:
    segments = [{"id": f"s{n}", "start": n * seconds, "end": (n + 1) * seconds, "text": text}
                for n, text in enumerate(texts)]
    return {"episode_id": episode_id, "source": "Publisher transcript", "timed": True,
            "text": "\n".join(texts), "segments": segments}


class Library:
    """Two shows: one episode heard this week, one heard long ago, one never heard."""

    def __init__(self):
        self.episodes = {RECENT: episode(RECENT, SHOW_A, "Heard this week"),
                         OLD: episode(OLD, SHOW_A, "Heard last month"),
                         UNHEARD: episode(UNHEARD, SHOW_B, "Never heard")}
        self.history = {RECENT: {"position": 190, "total": 200, "timestamp": since(2)},
                        OLD: {"position": 200, "total": 200, "timestamp": since(40)}}
        self.transcripts = {key: transcript(key, TEXTS) for key in (RECENT, OLD, UNHEARD)}
        self.requests: list[str] = []

    def podfetch(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        if path.startswith("/api/v1/episodes/"):
            found = self.episodes.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"podcastEpisode": found}) if found else httpx.Response(404)
        if path == "/api/v1/podcasts":
            return httpx.Response(200, json=[{"id": SHOW_A, "name": "Show A"}, {"id": SHOW_B, "name": "Show B"}])
        for show in (SHOW_A, SHOW_B):
            if path == f"/api/v1/podcasts/{show}/episodes":
                items = [e for e in self.episodes.values() if e["podcast_id"] == show]
                return httpx.Response(200, json=[{"podcastEpisode": e,
                                                  "podcastHistoryItem": self.history.get(e["episode_id"])}
                                                 for e in items])
        return httpx.Response(404)

    def load(self, episode_id, episode=None):
        found = self.transcripts.get(str(episode_id))
        return found or {"episode_id": str(episode_id), "source": None, "timed": False, "text": "", "segments": []}


@pytest.fixture
def lib():
    return Library()


@pytest.fixture
def app(tmp_path: Path, lib: Library):
    application = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(lib.podfetch))
    application.dependency_overrides[get_transcripts] = lambda: lib.load
    return application


def with_ai(app, *responses, model="fake-model") -> FakeLLM:
    fake = FakeLLM(responses)
    fake.model = model
    app.dependency_overrides[get_llm] = lambda: fake
    return fake


def seed_brief(app, lib: Library, episode_id: str) -> None:
    """A real, ready brief for ``episode_id`` through brief.py's own public API (brief.py owns making
    one), so recap's "key ideas" pass-through has something real to check against."""
    settings = app.state.settings
    podfetch = PodFetch(settings.podfetch_url, transport=settings.transport)
    db = briefs.database.connect(settings.database)
    try:
        fake = FakeLLM([{"summary": "Subnetting basics.", "verdict": "HEAR", "verdict_reason": "Best heard.",
                         "who_for": "Beginners.", "chapters": [{"title": "Subnetting", "start_segment_id": "1"}],
                         "key_ideas": [{"text": "A prefix length sets the subnet size.", "segment_ids": ["2"]}]}])
        fake.model = "fake-model"
        briefs.generate_brief(episode_id, "default", briefs.BriefContext(podfetch, lib.load, db, fake))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- the "what you learned" paragraph (pure)


def test_ref_parses_the_formats_a_model_might_write():
    assert recaps._ref(3) == "3"
    assert recaps._ref("3") == "3"
    assert recaps._ref("[3]") == "3"
    assert recaps._ref("segment 3") == "3"
    assert recaps._ref(3.5) == ""
    assert recaps._ref(True) == ""


def test_learned_lines_prefers_what_was_actually_heard():
    segments = as_segments(transcript("x", TEXTS)["segments"])  # 4 segments: 0-10, 10-20, 20-30, 30-40
    _, up_to_15s = recaps._learned_lines(segments, 15)
    assert list(up_to_15s) == ["1", "2"]
    _, everything = recaps._learned_lines(segments, None)
    assert list(everything) == ["1", "2", "3", "4"]


def test_validate_learned_drops_unknown_ids_dedupes_and_trims_whitespace():
    segments = as_segments(transcript("x", TEXTS)["segments"])
    _, by_number = recaps._learned_lines(segments, None)
    made = recaps._validate_learned({"text": "  A   summary.  ", "segment_ids": ["2", "2", "99", "[1]"]}, by_number)
    assert made["text"] == "A summary."
    assert [c["segment_id"] for c in made["citations"]] == ["s1", "s0"]
    assert made["citations"][0] == {"segment_id": "s1", "start": 10.0, "end": 20.0}


@pytest.mark.parametrize("raw", [{"text": "Something.", "segment_ids": ["99"]}, {"text": "", "segment_ids": ["1"]},
                                 {"text": "ok", "segment_ids": []}, "not a mapping"])
def test_validate_learned_needs_a_real_citation_and_a_paragraph(raw):
    segments = as_segments(transcript("x", TEXTS)["segments"])
    _, by_number = recaps._learned_lines(segments, None)
    with pytest.raises(recaps.RecapProblem):
        recaps._validate_learned(raw, by_number)


# ---------------------------------------------------------------- a highlight's own quote


def test_quote_for_a_middle_highlight_keeps_roughly_a_third_with_ellipses_on_both_sides():
    # A 90 s segment made of 90 identical one-second "words"; a 30 s highlight in its middle
    # should keep about a third of them, cut (not whole-word-broken) on both sides.
    text = " ".join(["xx"] * 90)
    segment = Segment("s1", 0.0, 90.0, text)
    quote = recaps.quote_for([segment], 30.0, 60.0)
    assert quote.startswith("…") and quote.endswith("…")
    kept = quote.strip("…").split()
    assert kept == ["xx"] * 30  # exactly a third of 90, snapped to whole words either side


def test_quote_for_a_range_covering_whole_segments_quotes_them_whole():
    a = Segment("s1", 0.0, 10.0, "First segment text.")
    b = Segment("s2", 10.0, 20.0, "Second segment text.")
    quote = recaps.quote_for([a, b], 0.0, 20.0)
    assert quote == "First segment text. Second segment text."
    assert "…" not in quote


def test_quote_for_uses_word_timings_over_the_character_approximation():
    words = (Word("one", 0.0, 1.0), Word("two", 1.0, 2.0), Word("three", 2.0, 3.0), Word("four", 3.0, 4.0))
    segment = Segment("s1", 0.0, 4.0, "one two three four", words)
    assert recaps.quote_for([segment], 1.0, 3.0) == "…two three…"
    assert recaps.quote_for([segment], 0.0, 4.0) == "one two three four"  # the whole range: no ellipsis


def test_quote_for_neighbouring_highlights_over_one_long_segment_dont_repeat_it_whole():
    # A past bug: two saves 30 s apart on the same long segment used to both quote the
    # entire paragraph. Now each keeps only its own share.
    text = " ".join(["xx"] * 90)
    segment = Segment("s1", 0.0, 90.0, text)
    first = recaps.quote_for([segment], 0.0, 30.0)
    second = recaps.quote_for([segment], 30.0, 60.0)
    assert first != second
    assert len(first.split("…")[0].split()) < 90 and len(second.strip("…").split()) < 90


def test_week_since_is_seven_days_before_now():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert recaps.week_since(now) == now - timedelta(days=7)


# ---------------------------------------------------------------- heard status and the deterministic recap


def test_heard_status_reuses_is_heard_and_falls_back_to_the_episode_duration(app, lib):
    client = TestClient(app)
    old = client.get(f"/companion/episodes/{OLD}/recap").json()
    assert old["heard"] == {"heard": True, "position": 200.0, "total": 200.0, "percent": 100,
                            "at": old["heard"]["at"]}
    unheard = client.get(f"/companion/episodes/{UNHEARD}/recap").json()
    assert unheard["heard"] == {"heard": False, "position": None, "total": 200.0, "percent": None, "at": None}


def test_recap_without_ai_still_works_and_says_so(app, lib):
    client = TestClient(app)
    result = client.get(f"/companion/episodes/{RECENT}/recap").json()
    assert result["learned"]["status"] == "no_ai"
    assert result["key_ideas"] == []  # no brief was ever made


def test_recap_of_an_episode_with_no_transcript_reports_that_state(app, lib):
    lib.episodes[SILENT] = episode(SILENT, SHOW_A, "No transcript yet")
    client = TestClient(app)
    result = client.get(f"/companion/episodes/{SILENT}/recap").json()
    assert result["learned"]["status"] == "no_transcript"


def test_recap_shows_the_cached_briefs_key_ideas_without_generating_one(app, lib):
    seed_brief(app, lib, RECENT)
    client = TestClient(app)
    result = client.get(f"/companion/episodes/{RECENT}/recap").json()
    assert result["key_ideas"] == [{"text": "A prefix length sets the subnet size.",
                                    "citations": [{"segment_id": "s1", "start": 10.0, "end": 20.0}]}]


def test_a_saved_highlight_appears_in_its_episode_recap_and_in_the_week(app, lib):
    client = TestClient(app)
    # [10, 40) covers segments 2-4 whole (each is exactly 10 s); the client's quote is a
    # deliberately wrong placeholder, since the server computes the real one from the transcript.
    note = {"id": str(uuid4()), "episode_id": RECENT, "position": 10, "text": "Subnetting splits a network.",
           "kind": "highlight", "start": 10, "end": 40, "quote": "placeholder"}
    assert client.post("/companion/notes", json=note).status_code == 201
    expected_quote = " ".join(TEXTS[1:4])
    recap = client.get(f"/companion/episodes/{RECENT}/recap").json()
    assert len(recap["highlights"]) == 1 and recap["highlights"][0]["quote"] == expected_quote
    assert recap["notes"] == []
    week = client.get("/companion/recap/week").json()
    assert [h["episode_id"] for h in week["highlights"]] == [RECENT]
    assert week["highlights"][0]["quote"] == expected_quote


# ---------------------------------------------------------------- the week


def test_week_recap_includes_only_episodes_heard_in_the_last_seven_days(app, lib):
    client = TestClient(app)
    week = client.get("/companion/recap/week").json()
    assert [e["episode_id"] for e in week["episodes"]] == [RECENT]
    assert week["episodes"][0]["heard_seconds"] == 190
    assert week["minutes"] == round(190 / 60)


def test_week_recap_pulls_in_each_episodes_cached_key_ideas(app, lib):
    seed_brief(app, lib, RECENT)
    client = TestClient(app)
    week = client.get("/companion/recap/week").json()
    assert week["key_ideas"] == [{"text": "A prefix length sets the subnet size.",
                                  "citations": [{"segment_id": "s1", "start": 10.0, "end": 20.0}],
                                  "episode_id": RECENT, "title": "Heard this week"}]


# ---------------------------------------------------------------- making the "what you learned" paragraph


def test_make_learned_needs_ai_first(app, lib):
    client = TestClient(app)
    response = client.post(f"/companion/episodes/{RECENT}/recap/learned")
    assert response.status_code == 409


def test_make_learned_needs_a_transcript(app, lib):
    with_ai(app)
    lib.episodes[SILENT] = episode(SILENT, SHOW_A, "No transcript yet")
    client = TestClient(app)
    assert client.post(f"/companion/episodes/{SILENT}/recap/learned").status_code == 409


def test_make_learned_validates_citations_against_the_real_transcript(app, lib):
    with_ai(app, {"text": "You heard how subnetting and prefix length work together.",
                 "segment_ids": ["1", "2", "99"]})
    client = TestClient(app)
    made = client.post(f"/companion/episodes/{RECENT}/recap/learned").json()
    assert made["status"] == "ready"
    assert [c["segment_id"] for c in made["citations"]] == ["s0", "s1"]
    assert (made["citations"][0]["start"], made["citations"][1]["start"]) == (0.0, 10.0)
    # cached: GET shows the same answer without any more FakeLLM responses queued
    again = client.get(f"/companion/episodes/{RECENT}/recap").json()
    assert again["learned"]["status"] == "ready" and again["learned"]["text"] == made["text"]


def test_make_learned_repairs_once_then_succeeds(app, lib):
    with_ai(app, {"text": "", "segment_ids": ["1"]}, {"text": "Fixed answer.", "segment_ids": ["1"]})
    client = TestClient(app)
    made = client.post(f"/companion/episodes/{RECENT}/recap/learned").json()
    assert made["status"] == "ready" and made["text"] == "Fixed answer."


def test_make_learned_repairs_once_then_fails_and_caches_the_failure(app, lib):
    with_ai(app, {"text": "ok", "segment_ids": ["99"]}, {"text": "still ok", "segment_ids": ["100"]})
    client = TestClient(app)
    made = client.post(f"/companion/episodes/{RECENT}/recap/learned").json()
    assert made["status"] == "failed"
    assert made["error"] and "cited no segment" in made["error"]
    again = client.get(f"/companion/episodes/{RECENT}/recap").json()
    assert again["learned"]["status"] == "failed"


def test_regenerate_makes_a_fresh_call_even_when_cached(app, lib):
    with_ai(app, {"text": "First.", "segment_ids": ["1"]}, {"text": "Second.", "segment_ids": ["2"]})
    client = TestClient(app)
    first = client.post(f"/companion/episodes/{RECENT}/recap/learned").json()
    assert first["text"] == "First."
    second = client.post(f"/companion/episodes/{RECENT}/recap/learned", json={"regenerate": True}).json()
    assert second["text"] == "Second."
