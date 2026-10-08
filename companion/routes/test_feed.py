"""/companion/settings/feed and /companion/feed.

PodFetch is an httpx.MockTransport, in the same two login shapes as companion/test_auth.py's own
PodFetch fixture:
- login=False: a default `docker compose up` (no BASIC_AUTH/OIDC/reverse proxy at all). Every
  caller, with or without a header, is PodFetch's one bootstrap user. Its RSS route then needs no
  apiKey at all, so making a feed link never needs (or can capture) one either.
- login=True: only a caller carrying Alice's own Authorization header resolves as Alice. PodFetch's
  RSS route then needs the apiKey in the path, so making a feed link captures Alice's own key.
Both are real PodFetch behaviour (crates/podfetch-web/src/controllers/websocket_controller.rs:
the apiKey check only runs `if ENVIRONMENT_SERVICE.http_basic || oidc_configured`), not a setting
of our own -- companion/routes/feed.py reads it from auth.authenticator(...).checks_logins().
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import httpx
from fastapi.testclient import TestClient

from companion.db import connect as connect_db
from companion.server import create_app

API_KEY = "podfetch-api-key-abc"
PODCAST = "00000000-0000-4000-8000-0000000000c1"
EPISODE = "00000000-0000-4000-8000-000000000001"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
TRANSCRIPT_NO_KEY = "/api/v1/podcasts/episodes/e1/transcripts/t1/file"
TRANSCRIPT_WITH_KEY = f"{TRANSCRIPT_NO_KEY}/apiKey/{API_KEY}"


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


ALICE = basic("alice", "alice-password")
AUTH = {"Authorization": ALICE}

VTT = """WEBVTT

00:00:00.000 --> 00:00:20.000
Let's talk about BGP path selection today.

00:00:20.000 --> 00:00:40.000
This episode is sponsored by Example Corp. Learn more at example dot com.

00:00:40.000 --> 00:01:00.000
Back to BGP path selection.
"""


def rss_xml(transcript_path: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="{PODCAST_NS}">
<channel><title>Networking show</title>
<item>
<title>Episode one</title>
<guid isPermaLink="false">{EPISODE}</guid>
<enclosure url="http://podfetch.test/ep1.mp3" length="1000" type="audio/mpeg"/>
<podcast:transcript url="http://podfetch.test{transcript_path}" type="text/vtt"/>
</item>
</channel>
</rss>'''


class FakePodFetch:
    """See the module docstring for the two ``login`` shapes. ``api_key`` is what ``/users/me``
    reports for the resolved caller (None simulates a real account with no key set yet)."""

    def __init__(self, *, login: bool, api_key: str | None = API_KEY):
        self.login = login
        self.api_key = api_key
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/api/v1/users/me":
            if self.login and request.headers.get("authorization") != ALICE:
                return httpx.Response(401)
            username = "alice" if self.login else "user123"
            return httpx.Response(200, json={"username": username, "apiKey": self.api_key})
        if not self.login and path == f"/rss/{PODCAST}":
            return httpx.Response(200, text=rss_xml(TRANSCRIPT_NO_KEY), headers={"content-type": "application/rss+xml"})
        if self.login and self.api_key and path == f"/rss/apiKey/{self.api_key}/{PODCAST}":
            return httpx.Response(200, text=rss_xml(TRANSCRIPT_WITH_KEY), headers={"content-type": "application/rss+xml"})
        if path in (TRANSCRIPT_NO_KEY, TRANSCRIPT_WITH_KEY):
            return httpx.Response(200, text=VTT, headers={"content-type": "text/vtt"})
        return httpx.Response(404)


def start(tmp_path, handler=None, *, login: bool = False):
    podfetch = handler if handler is not None else FakePodFetch(login=login)
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(podfetch))
    return TestClient(app), podfetch


def make_token(client, headers=None) -> dict:
    response = client.post("/companion/settings/feed/token", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_status_starts_unconfigured_then_true_after_a_token(tmp_path):
    client, _ = start(tmp_path)
    assert client.get("/companion/settings/feed").json() == {"configured": False, "created_at": None}
    make_token(client)
    status = client.get("/companion/settings/feed").json()
    assert status["configured"] is True and status["created_at"]


def test_token_response_carries_working_urls(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    assert made["cuts_url"].endswith("/cuts.xml") and "{podcast_id}" in made["shows_url_template"]
    assert made["token"] in made["cuts_url"]


def test_a_new_token_kills_the_old_link(tmp_path):
    client, _ = start(tmp_path)
    first = make_token(client)
    assert client.get(f"/companion/feed/{first['token']}/cuts.xml").status_code == 200
    second = make_token(client)
    assert second["token"] != first["token"]
    assert client.get(f"/companion/feed/{first['token']}/cuts.xml").status_code == 404
    assert client.get(f"/companion/feed/{second['token']}/cuts.xml").status_code == 200


def test_bad_or_garbage_tokens_get_404_not_500(tmp_path):
    client, _ = start(tmp_path)
    make_token(client)
    for bad in ("", "short", "x" * 500, "not-a-real-token-but-long-enough-to-pass-the-length-check"):
        assert client.get(f"/companion/feed/{bad}/cuts.xml").status_code == 404


def test_settings_routes_need_a_login_when_podfetch_has_one(tmp_path):
    client, _ = start(tmp_path, login=True)
    assert client.post("/companion/settings/feed/token").status_code == 401
    assert client.get("/companion/settings/feed").status_code == 401


def test_no_podfetch_key_yet_is_a_clear_409(tmp_path):
    client, _ = start(tmp_path, FakePodFetch(login=True, api_key=None))
    response = client.post("/companion/settings/feed/token", headers=AUTH)
    assert response.status_code == 409 and "API key" in response.json()["detail"]


# ── login off: the default `docker compose up`, no PodFetch API key ever needed ─────────────────

def test_login_off_makes_a_link_with_no_api_key_and_never_asks_for_one(tmp_path):
    client, podfetch = start(tmp_path, login=False)
    made = make_token(client)   # no Authorization header at all -- login is off
    response = client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert response.status_code == 200
    assert f"/rss/{PODCAST}" in podfetch.calls          # the keyless route
    assert f"/rss/apiKey/{API_KEY}/{PODCAST}" not in podfetch.calls
    root = ET.fromstring(response.text)
    assert root.find("channel/item/guid").text == EPISODE


def test_login_off_cuts_feed_never_touches_podfetch(tmp_path):
    client, podfetch = start(tmp_path, login=False)
    made = make_token(client)
    assert client.get(f"/companion/feed/{made['token']}/cuts.xml").status_code == 200
    assert not [call for call in podfetch.calls if call != "/api/v1/users/me"]


# ── login on: a real account (not PodFetch's read-only BASIC_AUTH bootstrap admin) ──────────────

def test_login_on_captures_the_key_and_uses_the_apikey_authenticated_rss_route(tmp_path):
    client, podfetch = start(tmp_path, login=True)
    made = make_token(client, headers=AUTH)
    response = client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert response.status_code == 200
    assert f"/rss/apiKey/{API_KEY}/{PODCAST}" in podfetch.calls
    assert f"/rss/{PODCAST}" not in podfetch.calls


def test_show_feed_when_login_is_turned_on_after_the_link_was_made_without_a_key(tmp_path):
    client, _ = start(tmp_path, login=False)
    made = make_token(client)                                   # captured no key: login was off
    turned_on = FakePodFetch(login=True)
    client, _ = start(tmp_path, turned_on)                       # same db, PodFetch now requires login
    response = client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert response.status_code == 404                           # asks for a fresh link, not a 500


def test_show_feed_parses_and_carries_a_chapters_link_per_episode(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    response = client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(response.text)   # "every feed parses"
    item = root.find("channel/item")
    assert item.find("guid").text == EPISODE
    assert item.find("enclosure").get("url") == "http://podfetch.test/ep1.mp3"          # kept as PodFetch sent it
    assert item.find(f"{{{PODCAST_NS}}}transcript") is not None                         # kept as it was
    chapters_el = item.find(f"{{{PODCAST_NS}}}chapters")
    assert chapters_el is not None and chapters_el.get("type") == "application/json+chapters"
    assert chapters_el.get("url") == f"http://testserver/companion/feed/{made['token']}/chapters/{EPISODE}.json"


def test_an_unknown_show_is_a_404(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    missing = "00000000-0000-4000-8000-0000000000ff"
    assert client.get(f"/companion/feed/{made['token']}/shows/{missing}.xml").status_code == 404


def test_chapters_json_has_the_sponsor_span_found_in_the_transcript(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")   # the fetch that computes and caches spans
    chapters = client.get(f"/companion/feed/{made['token']}/chapters/{EPISODE}.json").json()
    assert chapters == {"version": "1.2.0", "chapters": [{"startTime": 20.0, "title": "Sponsor", "endTime": 40.0}]}


def test_chapters_are_cached_so_an_unchanged_transcript_is_fetched_once(tmp_path):
    client, podfetch = start(tmp_path)
    made = make_token(client)
    client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert podfetch.calls.count(TRANSCRIPT_NO_KEY) == 1


def test_chapters_for_an_episode_never_shown_in_a_feed_yet_are_empty_not_an_error(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    unknown = "00000000-0000-4000-8000-000000000099"
    assert client.get(f"/companion/feed/{made['token']}/chapters/{unknown}.json").json() == {
        "version": "1.2.0", "chapters": []}


def test_a_transcript_fetch_that_fails_is_retried_on_the_next_poll_not_cached_as_empty(tmp_path):
    down = {"transcript": True}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == TRANSCRIPT_NO_KEY and down["transcript"]:
            return httpx.Response(503)
        return FakePodFetch(login=False)(request)

    client, _ = start(tmp_path, flaky)
    made = make_token(client)
    client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")
    assert client.get(f"/companion/feed/{made['token']}/chapters/{EPISODE}.json").json() == {
        "version": "1.2.0", "chapters": []}   # nothing usable yet, but not wrongly final
    down["transcript"] = False
    client.get(f"/companion/feed/{made['token']}/shows/{PODCAST}.xml")   # a later, healthy poll
    assert client.get(f"/companion/feed/{made['token']}/chapters/{EPISODE}.json").json() == {
        "version": "1.2.0", "chapters": [{"startTime": 20.0, "title": "Sponsor", "endTime": 40.0}]}


def test_cuts_feed_lists_a_cut_with_a_working_range_enclosure(tmp_path):
    client, _ = start(tmp_path)
    made = make_token(client)
    db = connect_db(tmp_path / "companion.db")
    cuts_dir = tmp_path / "cuts"
    cuts_dir.mkdir()
    cut_id = "00000000-0000-4000-8000-0000000000c9"
    audio = b"ID3" + bytes(range(256)) * 4
    (cuts_dir / f"{cut_id}.mp3").write_bytes(audio)
    data = ('{"title": "My cut", "index": [{"cut_start": 0, "cut_end": 60, "episode_id": "e1", '
            '"source_start": 10, "source_end": 70, "title": "Episode one"}]}')
    db.execute("INSERT INTO cut_files (id, user_id, plan_id, duration, size_bytes, data, created_at) "
              "VALUES (?,'default','plan-1',60.0,?,?,?)",
              (cut_id, len(audio), data, "2026-09-24T00:00:00+00:00"))
    db.commit()
    xml = client.get(f"/companion/feed/{made['token']}/cuts.xml").text
    root = ET.fromstring(xml)
    item = root.find("channel/item")
    assert item.find("title").text == "My cut"
    assert "Episode one" in item.find("description").text
    enclosure = item.find("enclosure")
    assert enclosure.get("type") == "audio/mpeg" and enclosure.get("length") == str(len(audio))
    mp3_url = enclosure.get("url").replace("http://testserver", "")
    whole = client.get(mp3_url)
    assert whole.status_code == 200 and whole.content == audio and whole.headers["accept-ranges"] == "bytes"
    part = client.get(mp3_url, headers={"Range": "bytes=0-2"})
    assert part.status_code == 206 and part.content == audio[:3]


def test_cuts_feed_and_cut_audio_also_get_404_for_a_bad_token(tmp_path):
    client, _ = start(tmp_path)
    assert client.get("/companion/feed/not-a-real-token-long-enough-to-pass/cuts.xml").status_code == 404
    assert client.get("/companion/feed/not-a-real-token-long-enough-to-pass/cuts/"
                      "00000000-0000-4000-8000-000000000001.mp3").status_code == 404
