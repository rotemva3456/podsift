"""GET /companion/search: library transcripts, returned under the PodFetch episode they belong to."""
import json
import os

import httpx
from fastapi.testclient import TestClient

from companion.routes.search import query_terms
from companion.server import create_app

FEED = "https://example.test/feed"
PODCAST = "01a0c877-0000-7000-8000-000000000001"


def segments(*texts, length=10):
    return [{"start": n * length, "end": (n + 1) * length, "text": text} for n, text in enumerate(texts)]


def episode(n, title="Episode", total_time=600):
    return {"id": f"internal-{n}", "episode_id": f"public-{n}", "podcast_id": PODCAST, "name": f"{title} {n}",
            "url": f"https://example.test/{n}.mp3", "total_time": total_time,
            "date_of_recording": f"2026-01-{n:02d}T00:00:00"}


def library(tmp_path, entries):
    """entries: key -> (segments | None, plain text | None, duration)."""
    show = tmp_path / "library" / "net-show"
    show.mkdir(parents=True)
    manifest = []
    for n, (key, (timed, text, duration)) in enumerate(entries.items(), start=1):
        manifest.append({"key": key, "n": n, "title": f"Library title {key}", "duration": duration,
                         "audio": f"https://example.test/{key}.mp3", "words_file": f"{key}.txt", "guid": key})
        if text is not None:
            (show / f"{key}.txt").write_text(text)
        if timed is not None:
            (show / f"{key}.timed.json").write_text(json.dumps({"duration": duration, "segments": timed}))
    (show / "_feed.json").write_text(json.dumps({"feed": FEED, "episodes": manifest}))
    return show.parent


def podfetch(episodes, calls, podcasts=None):
    def handle(request):
        calls.append(str(request.url))
        path = request.url.path
        if path == "/api/v1/podcasts":
            return httpx.Response(200, json=podcasts if podcasts is not None else
                                  [{"id": PODCAST, "name": "Net show", "rssfeed": FEED + "/"}])
        if path == f"/api/v1/podcasts/{PODCAST}/episodes":
            ordered = sorted(episodes, key=lambda e: e["date_of_recording"], reverse=True)
            cursor = request.url.params.get("last_podcast_episode")
            if cursor:
                ordered = [e for e in ordered if e["date_of_recording"] < cursor]
            return httpx.Response(200, json=[{"podcastEpisode": e, "podcastHistoryItem": None} for e in ordered[:75]])
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def start(tmp_path, entries, episodes, calls=None, podcasts=None):
    calls = [] if calls is None else calls
    root = library(tmp_path, entries)
    app = create_app("http://podfetch.test", tmp_path / "companion.db", root, podfetch(episodes, calls, podcasts))
    return TestClient(app), root


def test_hits_come_back_under_the_podfetch_episode_with_their_times(tmp_path):
    client, _ = start(tmp_path, {"1": (segments("Hello.", "The spanning tree protocol stops loops.",
                                                "Trees again: spanning trees.", "Nothing here."), None, 600)},
                      [episode(1)])
    result = client.get("/companion/search", params={"q": "Spanning Tree"}).json()
    assert result["library"] is True and result["terms"] == ["spanning", "tree"] and result["unmatched"] == 0
    [found] = result["episodes"]
    assert (found["episode_id"], found["id"], found["podcast_id"], found["title"], found["duration"]) == (
        "public-1", "internal-1", PODCAST, "Episode 1", 600)
    assert found["matches"] == 2
    assert [(h["segment_id"], h["start"], h["end"]) for h in found["hits"]] == [("seg-0002", 10, 20), ("seg-0003", 20, 30)]
    assert found["hits"][0]["text"] == "The spanning tree protocol stops loops."


def test_every_word_must_start_a_word_in_the_passage(tmp_path):
    client, _ = start(tmp_path, {"1": (segments("Panning for gold.", "A spanning ring.", "Spanning trees here."),
                                       None, 600)}, [episode(1)])
    starts = lambda q: [h["start"] for e in client.get("/companion/search", params={"q": q}).json()["episodes"]
                        for h in e["hits"]]
    assert starts("span tree") == [20]          # prefixes match, like PodFetch's search
    assert starts("panning") == [0]             # never the middle of "spanning"
    assert starts("spanning ring") == [10]
    assert starts("gold ring trees") == []      # no passage (or neighbouring pair) holds every word


def test_a_phrase_split_between_two_passages_is_one_hit(tmp_path):
    client, _ = start(tmp_path, {"1": (segments("First the spanning", "tree elects a root.", "Then a spanning tree."),
                                       None, 600)}, [episode(1)])
    [found] = client.get("/companion/search", params={"q": "spanning tree"}).json()["episodes"]
    assert [(h["start"], h["end"], h["text"]) for h in found["hits"]] == [
        (0, 20, "First the spanning tree elects a root."), (20, 30, "Then a spanning tree.")]


def test_episodes_not_in_podfetch_or_with_changed_audio_are_only_counted(tmp_path):
    entries = {str(n): (segments("The spanning tree."), None, 600) for n in (1, 2, 3)}
    client, _ = start(tmp_path, entries, [episode(1), episode(3, total_time=900)])
    result = client.get("/companion/search", params={"q": "spanning tree"}).json()
    assert [e["episode_id"] for e in result["episodes"]] == ["public-1"]
    assert result["unmatched"] == 2   # 2 is not in PodFetch; 3 is 50% longer, so its timing is stale


def test_no_words_or_no_library_means_no_podfetch_lookups(tmp_path):
    calls = []
    lookups = lambda: [c for c in calls if "/api/v1/podcasts" in c]   # a login check may still ask who the caller is
    client, _ = start(tmp_path, {"1": (segments("Words."), None, 600)}, [episode(1)], calls)
    for q in ["", "   ", "?", "a"]:
        assert client.get("/companion/search", params={"q": q}).json()["episodes"] == []
    assert lookups() == []
    bare = TestClient(create_app("http://podfetch.test", tmp_path / "bare.db", None, podfetch([], calls)))
    assert bare.get("/companion/search", params={"q": "words"}).json() == {
        "query": "words", "terms": ["words"], "library": False, "unmatched": 0, "episodes": []}
    assert lookups() == []


def test_query_words():
    assert query_terms("What is a Spanning-Tree?") == ["spanning", "tree"]
    assert query_terms("802.1Q trunk, c++") == ["802.1q", "trunk", "c++"]
    assert query_terms("what is it") == ["what", "is", "it"]   # only stop words: keep them
    assert query_terms("über café") == ["über", "café"]
    assert query_terms("tree tree") == ["tree"]


def test_long_queries_are_refused_with_a_message(tmp_path):
    client, _ = start(tmp_path, {"1": (segments("Words."), None, 600)}, [episode(1)])
    response = client.get("/companion/search", params={"q": "x" * 201})
    assert response.status_code == 422 and response.json()["detail"] == "Search for 200 characters or fewer."


def test_best_episodes_first_and_hits_are_capped(tmp_path):
    many = segments(*["A subnet mask." for _ in range(14)])
    entries = {"1": (segments("One subnet."), None, 600), "2": (many, None, 600), "3": (segments("Subnet."), None, 600)}
    client, _ = start(tmp_path, entries, [episode(1), episode(2), episode(3, title="Subnets explained")])
    result = client.get("/companion/search", params={"q": "subnet"}).json()
    assert [(e["episode_id"], e["matches"], len(e["hits"])) for e in result["episodes"]] == [
        ("public-3", 1, 1),      # the title names it
        ("public-2", 14, 10),    # then the most matches; at most 10 hits each
        ("public-1", 1, 1)]


def test_untimed_transcripts_are_searchable_without_times(tmp_path):
    client, _ = start(tmp_path, {"1": (None, "Intro words. The spanning tree protocol is old. Bye.", 600)}, [episode(1)])
    [found] = client.get("/companion/search", params={"q": "spanning tree"}).json()["episodes"]
    assert [(h["segment_id"], h["start"], h["end"]) for h in found["hits"]] == [(None, None, None)]
    assert "The spanning tree protocol is old." in found["hits"][0]["text"]


def test_a_text_transcript_that_is_really_srt_keeps_its_times(tmp_path):
    srt = "1\n00:00:01,000 --> 00:00:04,000\nWelcome to the show.\n\n2\n00:01:05,500 --> 00:01:09,000\nThe spanning tree protocol.\n"
    client, _ = start(tmp_path, {"1": (None, srt, 600)}, [episode(1)])
    [found] = client.get("/companion/search", params={"q": "spanning tree"}).json()["episodes"]
    assert [(h["start"], h["end"], h["text"]) for h in found["hits"]] == [(65.5, 69.0, "The spanning tree protocol.")]


def test_pages_through_podfetch_and_reuses_the_episode_list(tmp_path):
    calls = []
    episodes = [episode(n) for n in range(1, 81)]            # more than one page of 75
    client, _ = start(tmp_path, {"1": (segments("The spanning tree."), None, 600)}, episodes, calls)
    assert client.get("/companion/search", params={"q": "spanning"}).json()["episodes"][0]["episode_id"] == "public-1"
    listed = [c for c in calls if "/episodes" in c]
    assert len(listed) == 2 and "last_podcast_episode=2026-01-06" in listed[1]
    client.get("/companion/search", params={"q": "tree"})
    assert len([c for c in calls if "/episodes" in c]) == 2   # the show's episode list is kept for a while


def test_a_changed_transcript_file_is_read_again(tmp_path):
    client, root = start(tmp_path, {"1": (segments("Old words."), None, 600)}, [episode(1)])
    assert client.get("/companion/search", params={"q": "vlan"}).json()["episodes"] == []
    timed = root / "net-show" / "1.timed.json"
    timed.write_text(json.dumps({"duration": 600, "segments": segments("A VLAN tag.")}))
    os.utime(timed, ns=(timed.stat().st_atime_ns, timed.stat().st_mtime_ns + 10**9))
    assert client.get("/companion/search", params={"q": "vlan"}).json()["episodes"][0]["hits"][0]["text"] == "A VLAN tag."


def test_shows_with_another_feed_are_not_listed(tmp_path):
    calls = []
    other = [{"id": PODCAST, "name": "Other", "rssfeed": "https://other.test/feed"}]
    client, _ = start(tmp_path, {"1": (segments("The spanning tree."), None, 600)}, [episode(1)], calls, other)
    result = client.get("/companion/search", params={"q": "spanning"}).json()
    assert result["episodes"] == [] and result["unmatched"] == 1
    assert not [c for c in calls if "/episodes" in c]
