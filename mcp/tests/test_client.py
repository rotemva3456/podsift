from __future__ import annotations

import httpx
import pytest

from conftest import BASE_URL, client_for, json_response, run
from podcast_mcp.client import CompanionClient, PodcastError


def test_blank_base_url_names_the_env_var():
    with pytest.raises(PodcastError, match="PODCAST_URL"):
        CompanionClient("", None)


def test_from_env_without_podcast_url_names_the_env_var(monkeypatch):
    monkeypatch.delenv("PODCAST_URL", raising=False)
    monkeypatch.delenv("PODCAST_AUTH", raising=False)
    with pytest.raises(PodcastError, match="PODCAST_URL"):
        CompanionClient.from_env()


def test_no_auth_header_when_none_configured():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return json_response(200, {"ok": True})

    client = client_for(handler, auth=None)
    run(client.get("/companion/health"))
    assert seen["authorization"] is None


def test_auth_header_forwarded_verbatim():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return json_response(200, {"ok": True})

    client = client_for(handler, auth="Bearer secret-token")
    run(client.get("/companion/health"))
    assert seen["authorization"] == "Bearer secret-token"


def test_401_becomes_a_set_podcast_auth_message():
    client = client_for(lambda request: httpx.Response(401))
    with pytest.raises(PodcastError, match="PODCAST_AUTH"):
        run(client.get("/companion/notes"))


def test_string_detail_passes_through_unchanged():
    client = client_for(lambda request: json_response(409, {"detail": "This episode has no transcript yet."}))
    with pytest.raises(PodcastError, match=r"^This episode has no transcript yet\.$"):
        run(client.post("/companion/episodes/e1/brief"))


def test_list_detail_from_a_pydantic_validation_error_joins_into_one_line():
    body = {"detail": [{"loc": ["body", "mode"], "msg": "Input should be 'keyword' or 'ai'", "type": "literal_error"}]}
    client = client_for(lambda request: json_response(422, body))
    with pytest.raises(PodcastError, match="mode: Input should be 'keyword' or 'ai'"):
        run(client.post("/companion/plans", json={"mode": "auto"}))


def test_network_error_names_the_base_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = client_for(handler)
    with pytest.raises(PodcastError, match="Could not reach the podcast app"):
        run(client.get("/companion/health"))


def test_non_json_body_is_a_clear_error():
    client = client_for(lambda request: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(PodcastError, match="wasn't JSON"):
        run(client.get("/companion/health"))


def test_empty_body_returns_none():
    client = client_for(lambda request: httpx.Response(204))
    assert run(client.post("/companion/briefs/queue", json={})) is None


def test_link_builds_an_absolute_url():
    client = CompanionClient(BASE_URL + "/", "Bearer x")
    assert client.link("/companion/cuts/abc.mp3") == f"{BASE_URL}/companion/cuts/abc.mp3"
