"""Flashcards: generation and validation, SM-2 review, "replay what I forget" as a cut plan,
and the Anki/Obsidian exports.

PodFetch is an httpx.MockTransport and the AI is a FakeLLM, so nothing here touches a network or a
real provider."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import cards_store, cuts
from companion.engine import as_segments
from companion.engine.align import TimingCheck
from companion.llm import FakeLLM, LLMError, get_llm
from companion.server import create_app

EP = "00000000-0000-4000-8000-000000000001"          # public episode_id
PID = "internal-1"                                    # PodFetch's own id for the same episode
EP2 = "00000000-0000-4000-8000-000000000002"
PID2 = "internal-2"

# 5 segments, 20 s each: three teaching passages, one sponsor read, one recap.
LINES = [
    "Spanning tree protocol stops network loops by blocking a redundant port.",
    "The root bridge election picks the bridge with the lowest bridge id as the root.",
    "BPDU guard shuts down a port that receives an unexpected BPDU from an edge device.",
    "This episode is brought to you by our sponsor, visit example dot com for a discount.",
    "In summary, spanning tree and BPDU guard keep a switched network loop free.",
]
TIMES = [(20.0 * i, 20.0 * i + 20.0) for i in range(len(LINES))]


class FakePodFetch:
    """Just enough of PodFetch's API for cards_store and cuts.load_episodes: one episode lookup,
    its preferred transcript, and graceful 404s for everything a brief's facts touch (chapters,
    history, other episodes of the show) so read_brief never raises."""

    def __init__(self):
        self.episodes: dict[str, dict] = {}
        self.native: dict[str, dict] = {}
        self.requests: list[str] = []

    def add(self, episode_id, podfetch_id, name="An episode", total_time=100.0, *,
            lines=LINES, times=TIMES, source="generated"):
        self.episodes[episode_id] = {"id": podfetch_id, "episode_id": episode_id, "podcast_id": "show-1",
                                     "name": name, "url": f"https://example.test/{episode_id}.mp3",
                                     "total_time": total_time}
        self.native[podfetch_id] = {"id": "t1", "source": source, "segments": [
            {"idx": i + 1, "startMs": int(a * 1000), "endMs": int(b * 1000), "text": text}
            for i, ((a, b), text) in enumerate(zip(times, lines, strict=True))]}
        return self.episodes[episode_id]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        parts = path.strip("/").split("/")
        if request.method == "GET" and parts[:3] == ["api", "v1", "episodes"] and parts[3] in self.episodes:
            return httpx.Response(200, json={"podcastEpisode": self.episodes[parts[3]]})
        by_pid = {e["id"]: e for e in self.episodes.values()}
        if parts[:4] == ["api", "v1", "podcasts", "episodes"] and parts[4] in by_pid:
            pid = parts[4]
            if parts[5:] == ["transcripts"]:
                return httpx.Response(200, json=[])
            if parts[5:] == ["transcript"] and pid in self.native:
                return httpx.Response(200, json=self.native[pid])
        return httpx.Response(404)


@pytest.fixture
def world(tmp_path):
    fake = FakePodFetch()
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(fake))
    return fake, app, TestClient(app)


def with_ai(app, *responses, model="fake-model") -> FakeLLM:
    fake = FakeLLM(responses)
    fake.model = model
    app.dependency_overrides[get_llm] = lambda: fake
    return fake


def good_cards(ids=("1", "2", "3")) -> dict:
    all_cards = {
        "1": {"question": "What does spanning tree protocol stop?", "answer": "It stops network loops by blocking a redundant port.", "segment_ids": ["1"]},
        "2": {"question": "How is the root bridge chosen?", "answer": "The election picks the bridge with the lowest bridge id.", "segment_ids": ["2"]},
        "3": {"question": "What does BPDU guard do?", "answer": "It shuts down a port that receives an unexpected BPDU.", "segment_ids": ["3"]},
        "5": {"question": "What is spanning tree's job overall?", "answer": "Keeping the network loop free with BPDU guard.", "segment_ids": ["5"]},
    }
    return {"cards": [all_cards[i] for i in ids]}


def meta_card() -> dict:
    return {"question": "Who are the co-hosts of this show?",
            "answer": "Please subscribe to our podcast and leave a rating.", "segment_ids": ["4"]}


def segments_of(texts=LINES):
    return as_segments([{"id": str(n), "start": 20.0 * (n - 1), "end": 20.0 * n, "text": text}
                        for n, text in enumerate(texts, 1)])


# ---------------------------------------------------------------- validation


def test_validate_keeps_good_cards_and_drops_a_bad_citation():
    raw = {"cards": [good_cards(["1"])["cards"][0],
                     {"question": "Invented", "answer": "Cites nothing real.", "segment_ids": ["99"]},
                     {"question": "", "answer": "No question.", "segment_ids": ["2"]}]}
    cards, problems = cards_store.validate_cards(raw, segments_of())
    assert [c["question"] for c in cards] == ["What does spanning tree protocol stop?"]
    assert cards[0]["citations"] == [{"segment_id": "1", "start": 0.0, "end": 20.0}]
    assert any("99" in p for p in problems)
    assert any("missing a question" in p for p in problems)


def test_validate_accepts_segment_ids_in_any_written_form():
    raw = {"cards": [{"question": "Q", "answer": "A", "segment_ids": ["[2]", " segment 3 ", 1]}]}
    cards, _problems = cards_store.validate_cards(raw, segments_of())
    assert {c["segment_id"] for c in cards[0]["citations"]} == {"1", "2", "3"}


def test_validate_caps_cards_and_citations():
    many = {"cards": [{"question": f"Q{n}", "answer": f"A{n}", "segment_ids": ["1", "2", "3", "4", "5"]}
                      for n in range(20)]}
    cards, _problems = cards_store.validate_cards(many, segments_of())
    assert len(cards) == cards_store.CARDS_MAX
    assert len(cards[0]["citations"]) == cards_store.CITATIONS_MAX


def test_drop_meta_removes_show_trivia_only():
    cards = [{"question": "What does BPDU guard do?", "answer": "Shuts an edge port.", "citations": []},
            {**meta_card(), "citations": []}]
    kept = cards_store._drop_meta(cards)
    assert [c["question"] for c in kept] == ["What does BPDU guard do?"]


# ---------------------------------------------------------------- ask() (validate + one repair)


def test_ask_returns_validated_non_meta_cards():
    llm = FakeLLM([{"cards": [*good_cards(["1", "2", "3"])["cards"], meta_card()]}])
    cards, stats = cards_store.ask(llm, segments_of(), [], budget=60_000)
    assert {c["question"] for c in cards} == {c["question"] for c in good_cards(["1", "2", "3"])["cards"]}
    assert stats["repaired"] is False


def test_ask_repairs_once_when_the_first_answer_has_nothing_usable():
    llm = FakeLLM([{"cards": [meta_card()]}, good_cards(["1", "2"])])
    cards, stats = cards_store.ask(llm, segments_of(), [], budget=60_000)
    assert len(cards) == 2
    assert stats["repaired"] is True
    assert len(llm.calls) == 2


def test_ask_raises_when_nothing_is_usable_even_after_a_repair():
    llm = FakeLLM([{"cards": [meta_card()]}, {"cards": [meta_card()]}])
    with pytest.raises(cards_store.CardProblem):
        cards_store.ask(llm, segments_of(), [], budget=60_000)


def test_ask_reports_a_transcript_too_long_for_the_budget():
    llm = FakeLLM([])
    with pytest.raises(cards_store.CardProblem, match="too long"):
        cards_store.ask(llm, [], [], budget=1500)          # no segments at all: nothing can be offered


def test_ask_spreads_across_the_whole_episode_with_no_key_ideas_yet():
    llm = FakeLLM([good_cards(["1"])])
    long_segments = segments_of(["word " * 400] * 20)      # 20 segments, over budget once clipped to lines
    cards_store.ask(llm, long_segments, [], budget=6000)
    user = llm.calls[0]["user"]
    assert "[1]" in user
    assert "[20]" in user                                  # reaches the end too, not only a leading prefix
    assert "excerpts spread across the episode" in user


def test_ask_reaches_a_key_ideas_citation_near_the_end_of_a_long_episode():
    texts = [f"Filler passage number {n} about routine scheduled maintenance, nothing else notable here." for n in range(300)]
    texts[194] = "The BGP route reflector cluster id must be unique within each cluster to avoid routing loops."
    long_segments = segments_of(texts)                     # 300 segments; the interesting one is #195
    idea = {"text": "Route reflector cluster ids must be unique.",
           "citations": [{"segment_id": "195", "start": 0.0, "end": 0.0}]}
    llm = FakeLLM([{"cards": [{"question": "Why must a route reflector cluster id be unique?",
                              "answer": "So reflectors in different clusters don't create a routing loop.",
                              "segment_ids": ["195"]}]}])
    cards, stats = cards_store.ask(llm, long_segments, [idea], budget=4000)
    assert stats["truncated"] is True
    assert "cluster id must be unique" in llm.calls[0]["user"]     # segment 195's own text, not just the idea's
    assert cards and cards[0]["citations"][0]["segment_id"] == "195"


def test_ask_folds_in_the_brief_key_ideas_as_evidence():
    llm = FakeLLM([good_cards(["1"])])
    ideas = [{"text": "BPDU guard protects edge ports.", "citations": [{"segment_id": "3", "start": 40.0, "end": 60.0}]}]
    cards_store.ask(llm, segments_of(), ideas, budget=60_000)
    assert "BPDU guard protects edge ports." in llm.calls[0]["user"]
    assert "[3]" in llm.calls[0]["user"]


# ---------------------------------------------------------------- generation (HTTP)


def test_generate_needs_ai_and_a_transcript(world):
    fake, app, client = world
    fake.add(EP, PID)
    assert client.post(f"/companion/episodes/{EP}/cards").status_code == 409
    with_ai(app)
    fake.add(EP2, PID2, lines=[], times=[])
    response = client.post(f"/companion/episodes/{EP2}/cards")
    assert response.status_code == 409
    assert "transcript" in response.json()["detail"]


def test_generate_makes_cards_once_then_reuses_them(world):
    fake, app, client = world
    fake.add(EP, PID)
    llm = with_ai(app, good_cards(["1", "2", "3"]))
    made = client.post(f"/companion/episodes/{EP}/cards")
    assert made.status_code == 200, made.text
    cards = made.json()["cards"]
    assert len(cards) == 3
    assert all(c["episode_id"] == EP and c["due"] > 0 and c["ease"] == 2.5 for c in cards)
    assert cards[0]["citations"] == [{"segment_id": "1", "start": 0.0, "end": 20.0}]

    again = client.post(f"/companion/episodes/{EP}/cards")
    assert again.status_code == 200
    assert [c["id"] for c in again.json()["cards"]] == [c["id"] for c in cards]
    assert len(llm.calls) == 1                      # the second call never asked the AI again

    got = client.get(f"/companion/episodes/{EP}/cards")
    assert got.status_code == 200
    assert got.json()["ai_ready"] is True
    assert [c["id"] for c in got.json()["cards"]] == [c["id"] for c in cards]


def test_generate_regenerate_replaces_the_cards(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1"]))
    first = client.post(f"/companion/episodes/{EP}/cards").json()["cards"]
    app.dependency_overrides[get_llm] = lambda: FakeLLM([good_cards(["2", "3"])])
    second = client.post(f"/companion/episodes/{EP}/cards", json={"regenerate": True}).json()["cards"]
    assert {c["id"] for c in first}.isdisjoint({c["id"] for c in second})
    assert len(second) == 2


def test_generate_reports_a_failed_ai_answer_as_502(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, LLMError("The key was rejected."))
    response = client.post(f"/companion/episodes/{EP}/cards")
    assert response.status_code == 502
    assert response.json()["detail"] == "The key was rejected."


def test_get_cards_reports_no_transcript_before_any_generation(world):
    fake, app, client = world
    fake.add(EP, PID, lines=[], times=[])
    response = client.get(f"/companion/episodes/{EP}/cards")
    assert response.json() == {"episode_id": EP, "cards": [], "ai_ready": False, "has_transcript": False}


# ---------------------------------------------------------------- SM-2 review


def test_due_then_grade_reschedules_and_drops_off_the_due_list(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1", "2"]))
    cards = client.post(f"/companion/episodes/{EP}/cards").json()["cards"]
    due = client.get("/companion/review/due").json()
    assert due["count"] == 2
    ids = {c["id"] for c in due["cards"]}
    assert ids == {c["id"] for c in cards}

    graded = client.post(f"/companion/review/{cards[0]['id']}/grade", json={"grade": "good"})
    assert graded.status_code == 200
    body = graded.json()
    assert body["interval"] == 1 and body["reps"] == 1 and body["lapses"] == 0

    due_after = client.get("/companion/review/due").json()
    assert due_after["count"] == 1
    assert due_after["cards"][0]["id"] == cards[1]["id"]


def test_again_increases_lapses_and_comes_back_in_ten_minutes(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1"]))
    card = client.post(f"/companion/episodes/{EP}/cards").json()["cards"][0]
    for _ in range(2):
        graded = client.post(f"/companion/review/{card['id']}/grade", json={"grade": "again"}).json()
    assert graded["lapses"] == 2
    assert graded["interval"] == 0
    assert client.get("/companion/review/due").json()["count"] == 0     # due in 600s, not now


def test_grade_rejects_an_unknown_card_or_grade(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1"]))
    card = client.post(f"/companion/episodes/{EP}/cards").json()["cards"][0]
    assert client.post("/companion/review/does-not-exist/grade", json={"grade": "good"}).status_code == 404
    assert client.post(f"/companion/review/{card['id']}/grade", json={"grade": "excellent"}).status_code == 422


# ---------------------------------------------------------------- "replay what I forget"


def _fail_twice(client, card_id):
    for _ in range(2):
        client.post(f"/companion/review/{card_id}/grade", json={"grade": "again"})


def test_replay_needs_two_lapses_and_builds_only_the_cited_spans(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1", "2", "3"]))
    cards = client.post(f"/companion/episodes/{EP}/cards").json()["cards"]

    assert client.post("/companion/review/replay").status_code == 409     # nobody has lapsed yet
    client.post(f"/companion/review/{cards[0]['id']}/grade", json={"grade": "again"})   # lapses=1: not enough
    assert client.post("/companion/review/replay").status_code == 409

    _fail_twice(client, cards[1]["id"])       # segment 2, [20, 40)
    replay = client.post("/companion/review/replay")
    assert replay.status_code == 200, replay.text
    plan = replay.json()
    assert plan["status"] == "ready"
    assert [(s["start"], s["end"]) for s in plan["spans"]] == [(20.0, 40.0)]
    assert plan["spans"][0]["episode_id"] == EP
    assert "Card:" in plan["spans"][0]["why"]
    assert plan["kept_seconds"] == 20.0

    reloaded = client.get(f"/companion/plans/{plan['id']}")               # the cuts route reads it back
    assert reloaded.status_code == 200
    assert reloaded.json()["spans"] == plan["spans"]


def test_replay_spans_never_include_a_card_answered_again_only_once(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1", "2"]))
    cards = client.post(f"/companion/episodes/{EP}/cards").json()["cards"]
    _fail_twice(client, cards[0]["id"])                                    # segment 1: [0, 20)
    client.post(f"/companion/review/{cards[1]['id']}/grade", json={"grade": "again"})   # segment 2: only 1 lapse
    plan = client.post("/companion/review/replay").json()
    assert [(s["start"], s["end"]) for s in plan["spans"]] == [(0.0, 20.0)]


def test_replay_plan_reports_an_episode_whose_transcript_does_not_match_its_audio():
    ep = cuts.PlanEpisode(episode_id=EP, title="An episode", audio="https://example.test/1.mp3", duration=100.0, order=0,
                          timing=_timing(["ok text"], [(0.0, 20.0)], "feed"), check=TimingCheck("mismatch", "runs past the end"))
    candidates = [{"episode_id": EP, "question": "Q", "citations": [{"segment_id": "1", "start": 0.0, "end": 20.0}]}]
    record = cards_store.replay_plan(candidates, [ep])
    assert record["status"] == "needs_timing"
    assert record["spans"] == []
    assert "runs past the end" in record["needs_timing"][0]["reason"]


def test_replay_plan_shifts_spans_by_the_episode_offset():
    ep = cuts.PlanEpisode(episode_id=EP, title="An episode", audio="https://example.test/1.mp3",
                          duration=100.0, order=0)
    ep.timing = _timing(["a", "b"], [(0.0, 20.0), (20.0, 40.0)], "feed")
    ok = TimingCheck("ok", "matches", 5.0)
    ep.check, ep.offset = ok, 5.0
    candidates = [{"episode_id": EP, "question": "Q", "citations": [{"segment_id": "1", "start": 0.0, "end": 20.0}]}]
    record = cards_store.replay_plan(candidates, [ep])
    assert record["status"] == "ready"
    assert (record["spans"][0]["start"], record["spans"][0]["end"]) == (5.0, 25.0)
    assert record["episodes"][0]["offset"] == 5.0


def _timing(texts, times, origin):
    from companion.transcripts import Timing
    segs = as_segments([{"id": str(n), "start": a, "end": b, "text": t}
                        for n, (t, (a, b)) in enumerate(zip(texts, times, strict=True), 1)])
    return Timing({"episode_id": "x", "source": None, "origin": origin, "timed": True, "text": "", "segments": []},
                  segs, origin)


# ---------------------------------------------------------------- exports


def test_anki_tsv_is_front_back_tag_per_line(world):
    fake, app, client = world
    fake.add(EP, PID, name="An <b>Episode</b>: Loops!")
    with_ai(app, good_cards(["1", "2"]))
    client.post(f"/companion/episodes/{EP}/cards")
    response = client.get("/companion/review/export.tsv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/tab-separated-values")
    lines = response.text.strip("\n").split("\n")
    assert len(lines) == 2
    front, back, tag = lines[0].split("\t")
    assert front == "What does spanning tree protocol stop?"
    assert tag == "An_Episode_Loops"          # HTML tags stripped, punctuation folded to underscores
    assert "\t" not in back


def test_obsidian_markdown_groups_by_episode(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1", "2"]))
    client.post(f"/companion/episodes/{EP}/cards")
    response = client.get("/companion/review/export.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert text.startswith("# Podcast flashcards")
    assert "## An episode" in text
    assert "### What does spanning tree protocol stop?" in text
    assert "*Heard at 00:00*" in text


def test_exports_are_empty_but_valid_with_no_cards(world):
    _fake, _app, client = world
    assert client.get("/companion/review/export.tsv").text == ""
    assert client.get("/companion/review/export.md").text == "# Podcast flashcards\n"


# ---------------------------------------------------------------- per-user isolation


def test_cards_and_due_are_scoped_to_the_current_user(world):
    fake, app, client = world
    fake.add(EP, PID)
    with_ai(app, good_cards(["1"]))
    from companion.deps import current_user
    app.dependency_overrides[current_user] = lambda: "alice"
    client.post(f"/companion/episodes/{EP}/cards")
    app.dependency_overrides[current_user] = lambda: "bob"
    assert client.get(f"/companion/episodes/{EP}/cards").json()["cards"] == []
    assert client.get("/companion/review/due").json() == {"count": 0, "cards": []}
