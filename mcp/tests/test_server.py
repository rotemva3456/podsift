"""Exercises the real MCP surface (tool registration, argument schemas, error wrapping) — not just
the plain functions in logic.py, which test_logic.py already covers against fake companion replies.
"""
from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import client_for, json_response, run
from podcast_mcp import server

EXPECTED_TOOLS = {"search_episodes", "list_library", "get_transcript", "get_brief",
                  "plan_cut", "render_cut", "job_status", "cut_link", "discover_podcasts",
                  "follow_podcast", "prepare_episode", "plan_from_segments", "get_plan_script", "list_queue",
                  "list_plans"}


@pytest.fixture(autouse=True)
def reset_client():
    """Every tool reaches the shared client through get_client(); each test picks its own fake."""
    server._client = None
    yield
    server._client = None


def test_every_packet_tool_is_registered_with_a_description():
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    assert EXPECTED_TOOLS <= tools.keys()
    for name in EXPECTED_TOOLS:
        assert tools[name].description.strip(), f"{name} has no description for the agent to read"


def test_get_client_reads_podcast_url_and_auth_from_env(monkeypatch):
    monkeypatch.setenv("PODCAST_URL", "http://127.0.0.1:18114")
    monkeypatch.setenv("PODCAST_AUTH", "Bearer abc")
    first = server.get_client()
    second = server.get_client()
    assert first is second                      # built once, reused
    assert first.base_url == "http://127.0.0.1:18114"


def test_search_episodes_tool_runs_through_the_full_mcp_path():
    payload = {"query": "bgp", "terms": ["bgp"], "library": True, "unmatched": 0,
              "episodes": [{"episode_id": "e1", "id": "1", "podcast_id": "p1", "title": "Routing",
                           "duration": 90.0, "matches": 1, "hits": []}]}
    server._client = client_for(lambda request: json_response(200, payload))

    tool = server.mcp._tool_manager.get_tool("search_episodes")
    content = run(tool.run({"query": "bgp"}, convert_result=True))

    assert len(content) == 1
    body = json.loads(content[0].text)
    assert body["episodes"][0]["episode_id"] == "e1"
    assert body["episodes"][0]["duration_clock"] == "1:30"


def test_search_episodes_tool_error_is_one_sentence_the_agent_can_act_on():
    server._client = client_for(lambda request: httpx.Response(401))

    tool = server.mcp._tool_manager.get_tool("search_episodes")
    with pytest.raises(ToolError, match="PODCAST_AUTH"):
        run(tool.run({"query": "bgp"}))


def test_get_transcript_tool_applies_defaults_from_the_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return json_response(200, {"episode_id": "e1", "origin": "feed", "timed": False, "text": "hello"})

    server._client = client_for(handler)
    tool = server.mcp._tool_manager.get_tool("get_transcript")
    result = run(tool.run({"episode_id": "e1"}))            # start/end/max_chars all omitted

    assert seen["path"] == "/companion/episodes/e1/transcript"
    assert result["text"] == "hello"


def test_agent_choice_tool_has_a_typed_schema_and_uses_agent_mode_without_an_ai_call():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return json_response(201, {"id": "plan1", "status": "ready", "mode": "agent", "spans": []})

    server._client = client_for(handler)
    tool = server.mcp._tool_manager.get_tool("plan_from_segments")
    selection = {"episode_id": "11111111-1111-1111-1111-111111111111", "transcript_digest": "a" * 32,
                 "keep": [{"start_id": "s2", "end_id": "s5", "why": "A new failure example"}],
                 "skip": [{"start_id": "s3", "end_id": "s3", "why": "Already recalled from book chapter 2"}]}
    result = run(tool.run({"want": "Troubleshooting examples", "selections": [selection], "minutes": 15}))
    assert result["mode"] == "agent"
    assert len(seen) == 1 and seen[0]["mode"] == "agent" and seen[0]["minutes"] == 15
    assert seen[0]["selections"][0]["skip"][0]["why"] == selection["skip"][0]["why"]
    selection["keep"][0]["start"] = 17  # IDs only, no agent-invented timestamps
    with pytest.raises(ToolError, match="Extra inputs"):
        run(tool.run({"want": "Examples", "selections": [selection]}))
    assert len(seen) == 1


def test_list_plans_tool_maps_to_the_read_only_http_endpoint():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return json_response(200, [])

    server._client = client_for(handler)
    tool = server.mcp._tool_manager.get_tool("list_plans")
    result = run(tool.run({"episode_id": "11111111-1111-1111-1111-111111111111", "limit": 7}))

    assert result == {"plans": [], "count": 0}
    assert seen == {"method": "GET", "path": "/companion/plans",
                    "params": {"limit": "7", "episode_id": "11111111-1111-1111-1111-111111111111"}}


@pytest.mark.parametrize("effort", ["focus", "chill"])
def test_effort_reaches_agent_and_ai_planning_and_saved_plan_filter(effort):
    seen = []

    def handler(request):
        seen.append(request)
        return json_response(200, [] if request.method == "GET" else {"id": "plan1", "status": "ready", "spans": [], "learning_mode": effort})

    server._client = client_for(handler)
    selected = {"episode_id": "11111111-1111-1111-1111-111111111111", "transcript_digest": "a" * 32,
                "keep": [{"start_id": "s1", "end_id": "s2", "why": "Fits the requested effort"}]}
    tool = server.mcp._tool_manager.get_tool("plan_from_segments")
    result = run(tool.run({"want": "Routing", "selections": [selected], "learning_mode": effort}))
    assert result["learning_mode"] == effort
    assert json.loads(seen[-1].content)["learning_mode"] == effort
    run(server.mcp._tool_manager.get_tool("plan_cut").run({"want": "Routing", "episode_ids": [selected["episode_id"]], "mode": "ai", "learning_mode": effort}))
    assert json.loads(seen[-1].content)["learning_mode"] == effort
    run(server.mcp._tool_manager.get_tool("list_plans").run({"learning_mode": effort}))
    assert dict(seen[-1].url.params)["learning_mode"] == effort
    with pytest.raises(ToolError):
        run(tool.run({"want": "Routing", "selections": [selected], "learning_mode": "unknown"}))
    assert len(seen) == 3
