"""Headless discovery/preparation and complete transcript/script coverage for a learner agent."""
import copy
import json

import pytest

from conftest import client_for, json_response, run
from podcast_mcp import logic
from podcast_mcp.client import PodcastError

EP = "11111111-1111-1111-1111-111111111111"


def test_queue_pages_episode_ids_in_playlist_order_and_allows_another_library_playlist():
    entries = [{"podcastEpisode": {"episode_id": f"ep{i}", "name": f"Episode {i}",
                                   "total_time": 60 * i, "status": True}} for i in [3, 1, 2]]
    client = client_for(lambda request: json_response(200, [
        {"id": "p1", "name": "Listen next", "items": entries},
        {"id": "p2", "name": "Study routing", "items": entries[:1]}]))
    first = run(logic.list_queue(client, limit=2))
    assert [e["episode_id"] for e in first["episodes"]] == ["ep3", "ep1"]
    assert first["next_cursor"] == 2 and first["episodes"][0]["duration_clock"] == "3:00"
    second = run(logic.list_queue(client, cursor=first["next_cursor"], limit=2))
    assert [e["episode_id"] for e in second["episodes"]] == ["ep2"] and second["next_cursor"] is None
    other = run(logic.list_queue(client, playlist_id=first["playlists"][1]["id"]))
    assert other["name"] == "Study routing" and len(other["episodes"]) == 1
    with pytest.raises(PodcastError, match="playlist no longer exists"):
        run(logic.list_queue(client, playlist_id="missing"))


def test_untimed_cursor_reads_every_character_exactly_once_including_the_last_page():
    text = "A book covers the basics. A podcast adds a new failure story. " * 50 + "Last word."
    client = client_for(lambda request: json_response(200, {
        "episode_id": EP, "timed": False, "digest": "a" * 32, "text": text}))
    pages, cursor = [], 0
    while True:
        page = run(logic.get_transcript(client, EP, max_chars=211, cursor=cursor))
        pages.append(page["text"])
        assert page["transcript_digest"] == "a" * 32
        if page["next_cursor"] is None:
            assert page["complete"]
            break
        assert page["next_cursor"] > cursor and not page["complete"]
        cursor = page["next_cursor"]
    assert "".join(pages) == text


def test_timed_cursor_covers_equal_timestamps_overlaps_and_long_gaps_without_duplication():
    segments = [{"id": f"s{i}", "start": at, "end": at + 60, "text": f"line {i}: " + "words " * 40}
                for i, at in enumerate([0, 0, 15, 900, 901, 3600])]
    client = client_for(lambda request: json_response(200, {
        "episode_id": EP, "timed": True, "digest": "b" * 32, "segments": segments}))
    seen, cursor = [], 0
    for _ in range(len(segments) + 1):
        page = run(logic.get_transcript(client, EP, cursor=cursor, max_chars=300))
        seen += [s["id"] for s in page["segments"]]
        assert page["window"] is None and page["total_segments"] == len(segments)
        if page["next_cursor"] is None:
            assert page["complete"]
            break
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]
    assert seen == [s["id"] for s in segments]


def test_empty_time_window_points_to_later_words_instead_of_claiming_completion():
    client = client_for(lambda request: json_response(200, {
        "episode_id": EP, "timed": True, "segments": [{"id": "late", "start": 1000,
        "end": 1010, "text": "After a long opening gap."}]}))
    page = run(logic.get_transcript(client, EP))
    assert page["segments"] == [] and page["next_cursor"] == 0
    assert page["next_start"] == 1000 and not page["complete"]


@pytest.mark.parametrize("kwargs", [{"cursor": -1}, {"cursor": 0, "start": 0}])
def test_invalid_transcript_cursor_is_refused_without_http(no_calls, kwargs):
    with pytest.raises(PodcastError, match="cursor"):
        run(logic.get_transcript(no_calls, EP, **kwargs))


def test_script_pages_sentences_in_a_long_passage_and_preserves_skip_evidence():
    lines = [{"start": i, "end": i + 1, "text": f"Sentence {i}."} for i in range(15)]
    data = {"episodes": [{"episode_id": EP, "title": "A podcast", "parts": [
        {"kind": "keep", "start": 0, "end": 15, "why": "New example", "lines": lines},
        {"kind": "cut", "reason": "skip", "why": "Already recalled from book chapter 2",
         "start": 15, "end": 16, "lines": [{"start": 15, "end": 16, "text": "Familiar fact."}]}]}]}
    client = client_for(lambda request: json_response(200, data))
    seen, cursor = [], 0
    while True:
        page = run(logic.get_plan_script(client, "plan1", cursor=cursor, limit=3))
        assert len(page["lines"]) <= 3
        seen += page["lines"]
        if page["next_cursor"] is None:
            assert page["complete"]
            break
        cursor = page["next_cursor"]
    assert [s["text"] for s in seen] == [s["text"] for s in lines] + ["Familiar fact."]
    assert seen[-1]["reason"] == "skip" and "book chapter 2" in seen[-1]["why"]
    assert seen[-1]["start_clock"] == "0:15"


def test_discovery_and_follow_use_the_existing_public_directory_and_rss_api():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return json_response(200, {"results": [{"collectionName": "Networking",
                "artistName": "A host", "feedUrl": "https://example.test/feed.xml", "trackId": 42}] * 3})
        assert json.loads(request.content) == {"rssFeedUrl": "https://example.test/feed.xml"}
        return json_response(200, {"id": "show1", "name": "Networking"})

    client = client_for(handler)
    found = run(logic.discover_podcasts(client, "routing examples", limit=1))
    assert len(found["podcasts"]) == 1 and found["more_podcasts"] == 2
    assert calls[0].url.path == "/api/v1/podcasts/0/routing examples/search"
    followed = run(logic.follow_podcast(client, found["podcasts"][0]["feed_url"]))
    assert followed["result"]["id"] == "show1" and calls[-1].url.path == "/api/v1/podcasts/feed"


@pytest.mark.parametrize("url", ["platform:playlist:abc", "file:///tmp/podcast", "https://", "https://user:password@example.test/feed"])
def test_follow_refuses_non_feed_urls_and_embedded_credentials(no_calls, url):
    with pytest.raises(PodcastError, match="RSS feed"):
        run(logic.follow_podcast(no_calls, url))


def test_preparation_is_read_only_by_default_and_download_is_explicit():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return json_response(200, {"podcastEpisode": {"id": "internal1", "name": "Example", "status": False},
                                   "podcastHistoryItem": None})

    client = client_for(handler)
    first = run(logic.prepare_episode(client, EP))
    assert first["status"] == "not_downloaded" and first["title"] == "Example"
    assert calls == [("GET", f"/api/v1/episodes/{EP}")]
    assert run(logic.prepare_episode(client, EP, download=True))["status"] == "downloading"
    assert calls[-1] == ("PUT", f"/api/v1/podcasts/{EP}/episodes/download")


@pytest.mark.parametrize("status", ["pending", "running", "parsed"])
def test_preparation_reuses_generated_transcripts_instead_of_starting_another_job(status):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.url.path.endswith("/transcripts"):
            return json_response(200, [{"id": "t1", "source": "generated", "status": status}])
        return json_response(200, {"podcastEpisode": {"id": "internal1", "name": "Example", "status": True},
                                   "podcastHistoryItem": None})

    result = run(logic.prepare_episode(client_for(handler), EP, transcribe=True))
    assert result["downloaded"] and result["title"] == "Example"
    assert calls == ["GET", "GET"]
    assert result["status"] == ("downloaded" if status == "parsed" else "transcription_running")


def test_transcription_can_only_start_with_an_explicit_flag_after_downloading():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/transcripts"):
            return json_response(200, [])
        return json_response(200, {"podcastEpisode": {"id": "internal1", "name": "Example", "status": True},
                                   "podcastHistoryItem": None})

    client = client_for(handler)
    assert run(logic.prepare_episode(client, EP))["status"] == "downloaded"
    assert all(method == "GET" for method, _ in calls)
    assert run(logic.prepare_episode(client, EP, transcribe=True))["status"] == "transcription_running"
    assert calls[-1] == ("POST", f"/api/v1/podcasts/episodes/{EP}/transcribe")


def test_agent_plan_forwards_book_evidence_and_the_entire_selection_without_another_model():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return json_response(201, {"id": "p1", "mode": "agent", "status": "ready", "spans": [],
                                  "kept_seconds": 60, "source_seconds": 3600})

    selections = [{"episode_id": EP, "transcript_digest": "a" * 32,
                   "keep": [{"start_id": "new", "end_id": "new", "why": "New application"}],
                   "skip": [{"start_id": "old", "end_id": "old", "why": "Already recalled from a book"}]}]
    expected = copy.deepcopy(selections)
    result = run(logic.plan_from_segments(client_for(handler), "Learn applications", selections, minutes=5))
    assert len(seen) == 1 and seen[0]["mode"] == "agent" and seen[0]["selections"] == expected
    assert seen[0]["episode_ids"] == [EP] and result["source_seconds_clock"] == "1:00:00"
