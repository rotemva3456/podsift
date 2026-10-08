from __future__ import annotations

import httpx
import pytest

from conftest import client_for, json_response, run
from podcast_mcp import logic
from podcast_mcp.client import PodcastError

EP = "11111111-1111-1111-1111-111111111111"


# --------------------------------------------------------------------------------- search_episodes


def test_search_episodes_caps_hits_and_adds_clocks():
    hits = [{"segment_id": f"s{i}", "start": 60.0 * i, "end": 60.0 * i + 5, "text": f"hit {i}"} for i in range(5)]
    payload = {"query": "bgp", "terms": ["bgp"], "library": True, "unmatched": 0,
               "episodes": [{"episode_id": EP, "id": "1", "podcast_id": "p1", "title": "Routing",
                             "duration": 1830, "matches": 5, "hits": hits}]}
    client = client_for(lambda request: json_response(200, payload))

    result = run(logic.search_episodes(client, "bgp"))

    episode = result["episodes"][0]
    assert episode["duration_clock"] == "30:30"
    assert len(episode["hits"]) == 3          # default hits_per_episode
    assert episode["more_hits"] == 2
    assert episode["hits"][0]["start_clock"] == "0:00"
    assert result["more_episodes"] == 0


def test_search_episodes_blank_query_raises_without_a_network_call(no_calls):
    with pytest.raises(PodcastError, match="Say what you want to find"):
        run(logic.search_episodes(no_calls, "   "))


def test_search_episodes_limit_caps_episode_count():
    episodes = [{"episode_id": str(i), "id": str(i), "podcast_id": "p", "title": f"E{i}",
                "duration": 60, "matches": 1, "hits": []} for i in range(10)]
    payload = {"query": "x", "terms": ["x"], "library": True, "unmatched": 0, "episodes": episodes}
    client = client_for(lambda request: json_response(200, payload))

    result = run(logic.search_episodes(client, "x", limit=2))

    assert len(result["episodes"]) == 2
    assert result["more_episodes"] == 8


# ----------------------------------------------------------------------------------- list_library


def test_list_library_lists_shows_with_no_podcast_id():
    shows = [{"id": f"p{i}", "name": f"Show {i}", "author": "Someone"} for i in range(3)]
    client = client_for(lambda request: json_response(200, shows))

    result = run(logic.list_library(client))

    assert result["kind"] == "shows"
    assert [s["podcast_id"] for s in result["shows"]] == ["p0", "p1", "p2"]
    assert result["more_shows"] == 0
    assert "podcast_id" in result["hint"]


def test_list_library_episodes_forwards_cursor_and_pages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cursor"] = request.url.params.get("last_podcast_episode")
        items = [{"podcastEpisode": {"episode_id": f"e{i}", "id": str(i), "podcast_id": "p1",
                                     "name": f"Ep {i}", "total_time": 600, "date_of_recording": f"2026-01-{i+1:02d}",
                                     "status": i % 2 == 0}}
                for i in range(3)]
        return json_response(200, items)

    client = client_for(handler)
    result = run(logic.list_library(client, podcast_id="p1", cursor="2025-12-31", limit=2))

    assert seen["path"] == "/api/v1/podcasts/p1/episodes"
    assert seen["cursor"] == "2025-12-31"
    assert result["kind"] == "episodes"
    assert len(result["episodes"]) == 2
    assert result["more"] == 1
    assert result["episodes"][0]["downloaded"] is True
    assert result["episodes"][0]["duration_clock"] == "10:00"
    assert result["next_cursor"] == "2026-01-02"        # the last SHOWN episode's date


# ---------------------------------------------------------------------------------- get_transcript


TIMED = {
    "episode_id": EP, "source": "feed", "origin": "feed", "timed": True, "text": "",
    "segments": [{"id": f"s{i}", "start": float(i * 30), "end": float(i * 30 + 30), "text": f"line {i} " * 5}
                for i in range(20)],
}


def test_get_transcript_default_window_is_first_five_minutes():
    client = client_for(lambda request: json_response(200, TIMED))

    result = run(logic.get_transcript(client, EP))

    assert result["timed"] is True
    assert result["window"]["start"] == 0.0
    assert result["window"]["end"] == 300.0
    assert result["window"]["end_clock"] == "5:00"
    assert all(seg["start"] < 300.0 for seg in result["segments"])
    assert result["next_start"] == 300.0                # segment 10 starts at 300


def test_get_transcript_window_picks_overlapping_segments_only():
    client = client_for(lambda request: json_response(200, TIMED))

    result = run(logic.get_transcript(client, EP, start=100, end=125))

    starts = [seg["start"] for seg in result["segments"]]
    assert starts == [90.0, 120.0]                      # segments covering [90,120) and [120,150)


def test_get_transcript_char_budget_sets_next_start():
    client = client_for(lambda request: json_response(200, TIMED))

    result = run(logic.get_transcript(client, EP, start=0, end=600, max_chars=250))

    assert len(result["segments"]) < 20
    assert result["next_start"] is not None
    assert result["next_start_clock"] == logic.clock(result["next_start"])


def test_get_transcript_untimed_ignores_window_and_caps_text():
    untimed = {"episode_id": EP, "source": "library", "origin": "library", "timed": False,
              "text": "word " * 2000, "segments": []}
    client = client_for(lambda request: json_response(200, untimed))

    result = run(logic.get_transcript(client, EP, start=10, end=20, max_chars=100))

    assert result["timed"] is False
    assert len(result["text"]) == 200            # max_chars has a 200-char floor (see clamp minimum)
    assert result["more_text"] > 0
    assert "ignored" in result["note"]


# --------------------------------------------------------------------------------------- get_brief


def test_get_brief_uses_get_when_not_generating():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return json_response(200, {"episode_id": EP, "status": "ready", "summary": "x", "verdict": "HEAR",
                                   "chapters": [], "key_ideas": [], "duration": 754, "ads_seconds": 30})

    client = client_for(handler)
    result = run(logic.get_brief(client, EP, generate=False))

    assert seen["method"] == "GET"
    assert result["duration_clock"] == "12:34"
    assert result["ads_seconds_clock"] == "0:30"


def test_get_brief_uses_post_when_generating():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return json_response(202, {"episode_id": EP, "status": "generating"})

    client = client_for(handler)
    result = run(logic.get_brief(client, EP, generate=True))

    assert seen["method"] == "POST"
    assert result["status"] == "generating"


def test_get_brief_caps_key_ideas_and_citations():
    citations = [{"segment_id": f"c{i}", "start": float(i), "end": float(i + 1)} for i in range(5)]
    ideas = [{"text": f"idea {i}", "citations": citations} for i in range(12)]
    client = client_for(lambda request: json_response(200, {"episode_id": EP, "status": "ready",
                                                             "chapters": [], "key_ideas": ideas}))

    result = run(logic.get_brief(client, EP))

    assert len(result["key_ideas"]) == 8
    assert result["more_key_ideas"] == 4
    assert len(result["key_ideas"][0]["citations"]) == 3
    assert result["key_ideas"][0]["more_citations"] == 2
    assert result["key_ideas"][0]["citations"][0]["start_clock"] == "0:00"


def test_get_brief_connect_ai_error_passes_through_the_server_sentence():
    client = client_for(lambda request: json_response(409, {"detail": "Connect AI in Settings → AI to make a brief."}))
    with pytest.raises(PodcastError, match="Connect AI in Settings"):
        run(logic.get_brief(client, EP, generate=True))


# ---------------------------------------------------------------------------------------- plan_cut


def test_plan_cut_requires_a_want(no_calls):
    with pytest.raises(PodcastError, match="Say what you want to hear"):
        run(logic.plan_cut(no_calls, "   ", episode_ids=[EP]))


def test_plan_cut_requires_exactly_one_source(no_calls):
    with pytest.raises(PodcastError, match="not both, not neither"):
        run(logic.plan_cut(no_calls, "bgp"))
    with pytest.raises(PodcastError, match="not both, not neither"):
        run(logic.plan_cut(no_calls, "bgp", episode_ids=[EP], use_queue=True))


def test_plan_cut_sends_episode_ids_and_formats_spans():
    seen = {}
    long_text = "x" * 600

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return json_response(201, {
            "id": "plan1", "status": "ready", "mode": "keyword", "want": "bgp", "skip": None,
            "minutes": 10, "spans": [{"id": "s1", "episode_id": EP, "start": 30.0, "end": 90.0,
                                      "text": long_text, "why": "on topic", "enabled": True}],
            "kept_seconds": 60.0, "source_seconds": 1800.0, "omitted": [], "needs_timing": [],
            "created_at": "now", "episodes": [],
        })

    client = client_for(handler)
    result = run(logic.plan_cut(client, "bgp", episode_ids=[EP]))

    assert seen["body"]["episode_ids"] == [EP]
    assert "source" not in seen["body"]
    assert result["kept_seconds_clock"] == "1:00"
    assert result["spans"][0]["start_clock"] == "0:30"
    assert result["spans"][0]["text"].endswith("…")
    assert len(result["spans"][0]["text"]) == 501
    assert "render_cut" in result["next"]


def test_plan_cut_use_queue_sends_source_not_episode_ids():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return json_response(201, {"id": "plan2", "status": "empty", "mode": "keyword", "want": "bgp",
                                   "spans": [], "kept_seconds": 0, "source_seconds": 0, "omitted": [],
                                   "needs_timing": [], "created_at": "now", "episodes": []})

    client = client_for(handler)
    result = run(logic.plan_cut(client, "bgp", use_queue=True))

    assert seen["body"]["source"] == "queue"
    assert "episode_ids" not in seen["body"]
    assert "nothing ready" in result["next"]


# -------------------------------------------------------------------------------------- list_plans


def test_list_plans_maps_filter_and_adds_human_source_time_context():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return json_response(200, [{
            "id": "plan1", "status": "ready", "kept_seconds": 60, "source_seconds": 600,
            "spans": [{"episode_id": EP, "start": 30, "end": 90, "text": "source", "enabled": False}],
            "episodes": [{"episode_id": EP, "title": "Routing", "duration": 600}],
            "omitted": [{"episode_id": EP, "start": 90, "end": 120, "reason": "budget"}],
            "needs_timing": [],
        }])

    result = run(logic.list_plans(client_for(handler), EP, limit=75))

    assert seen == {"path": "/companion/plans", "params": {"limit": "50", "episode_id": EP}}
    assert result["count"] == 1
    plan = result["plans"][0]
    assert plan["episodes"][0]["duration_clock"] == "10:00"
    assert plan["spans"][0]["start_clock"] == "0:30" and plan["spans"][0]["enabled"] is False
    assert plan["omitted"][0]["end_clock"] == "2:00"


# -------------------------------------------------------------------------------------- render_cut


def test_render_cut_returns_job_id_and_a_next_hint():
    client = client_for(lambda request: json_response(202, {"job_id": "job1"}))
    result = run(logic.render_cut(client, "plan1"))
    assert result["job_id"] == "job1"
    assert "job_status" in result["next"]


def test_render_cut_surfaces_the_no_spans_enabled_message():
    client = client_for(lambda request: json_response(422, {"detail": "Turn on at least one passage before exporting."}))
    with pytest.raises(PodcastError, match="Turn on at least one passage"):
        run(logic.render_cut(client, "plan1"))


# -------------------------------------------------------------------------------------- job_status


def test_job_status_done_adds_percent_and_next_hint():
    client = client_for(lambda request: json_response(200, {"id": "job1", "status": "done", "progress": 1.0,
                                                             "error": None, "cut_id": "cut1", "plan_id": "plan1",
                                                             "stage": "done", "detail": None}))
    result = run(logic.job_status(client, "job1"))
    assert result["progress_percent"] == 100
    assert "cut_link" in result["next"]


def test_job_status_running_has_no_next_hint():
    client = client_for(lambda request: json_response(200, {"id": "job1", "status": "running", "progress": 0.4,
                                                             "error": None, "cut_id": None, "plan_id": "plan1",
                                                             "stage": "render", "detail": None}))
    result = run(logic.job_status(client, "job1"))
    assert result["progress_percent"] == 40
    assert "next" not in result


# ---------------------------------------------------------------------------------------- cut_link


def test_cut_link_builds_url_and_caps_index():
    index = [{"cut_start": float(i * 10), "cut_end": float(i * 10 + 10), "episode_id": EP,
             "source_start": float(i * 30), "source_end": float(i * 30 + 10), "title": "seg"} for i in range(20)]
    client = client_for(lambda request: json_response(200, {"id": "cut1", "plan_id": "plan1", "duration": 754.0,
                                                             "size_bytes": 3_500_000, "index": index, "title": "My cut",
                                                             "timing": [], "label": None, "created_at": "now"}))

    result = run(logic.cut_link(client, "cut1"))

    assert result["url"] == "http://companion.test/companion/cuts/cut1.mp3"
    assert result["duration_clock"] == "12:34"
    assert result["size_mb"] == 3.5
    assert len(result["index"]) == 15
    assert result["more_index"] == 5
    assert result["index"][0]["cut_start_clock"] == "0:00"
