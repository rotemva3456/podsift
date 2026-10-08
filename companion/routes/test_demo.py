"""The bundled demo pack: GET .../demo/feed/{show}.xml and POST .../demo/import."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from companion.routes import demo
from companion.server import create_app

PODCAST = "01a0c877-0000-7000-8000-000000000009"
EP1 = "00000000-0000-4000-8000-000000000001"
EP2 = "00000000-0000-4000-8000-000000000002"
TITLE = "HPR Bash Tips (Hacker Public Radio)"


def timed_json(text: str, seconds: float = 5.0) -> dict:
    words = text.split()
    segments = [{"start": n * seconds, "end": (n + 1) * seconds, "text": word} for n, word in enumerate(words)]
    return {"duration": len(words) * seconds, "media": {"source_url": "", "sha256": "abc", "byte_length": 10}, "segments": segments}


def library(tmp_path, show_id="hpr-bash-tips", keys=("hpr1", "hpr2"), briefs_for=("hpr1",)):
    show = tmp_path / "library" / show_id
    show.mkdir(parents=True)
    episodes = []
    for n, key in enumerate(keys, start=1):
        episodes.append({"key": key, "guid": key, "n": n, "title": f"Episode {key}", "duration": 15.0,
                         "published": "2018-09-13", "audio": f"https://example.test/{key}.mp3",
                         "byte_length": 12345, "words_file": f"{key}.txt", "summary": f"About {key}."})
        (show / f"{key}.txt").write_text("one two three")
        (show / f"{key}.timed.json").write_text(json.dumps(timed_json("one two three")))
    (show / "_feed.json").write_text(json.dumps({"title": TITLE, "feed": "https://example.test/series", "episodes": episodes}))
    if briefs_for:
        body = {key: {"model": "demo-model", "body": {"summary": f"Summary of {key}.", "verdict": "HEAR",
                                                        "verdict_reason": "Reasoning.", "who_for": "Everyone.",
                                                        "chapters": [], "key_ideas": []}} for key in briefs_for}
        (show / "briefs.json").write_text(json.dumps(body))
    return tmp_path / "library"


def podfetch_transport(episode_ids: dict[str, str], podcast_exists: bool = True, calls: list | None = None):
    """episode_ids: audio url -> episode_id. Fakes enough of PodFetch for demo_import AND for
    reading the brief afterwards (get_brief calls PodFetch.episode(), a different path)."""
    calls = [] if calls is None else calls
    episodes = {eid: {"id": f"internal-{eid}", "episode_id": eid, "url": url, "name": f"Episode {eid}",
                      "podcast_id": PODCAST, "total_time": 15, "date_of_recording": f"2026-01-{n:02d}T00:00:00"}
               for n, (url, eid) in enumerate(episode_ids.items(), start=1)}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/api/v1/users/me":
            return httpx.Response(404)
        if path == "/api/v1/podcasts":
            return httpx.Response(200, json=[{"id": PODCAST, "name": TITLE, "rssfeed": "https://x/feed"}] if podcast_exists else [])
        if path == f"/api/v1/podcasts/{PODCAST}/episodes":
            page = [{"podcastEpisode": episode, "podcastHistoryItem": None} for episode in episodes.values()]
            return httpx.Response(200, json=page)
        if path.startswith("/api/v1/episodes/"):
            episode = episodes.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"podcastEpisode": episode}) if episode else httpx.Response(404)
        if path.startswith("/api/v1/podcasts/episodes/") and path.endswith("/chapters"):
            return httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handle), calls


def start(tmp_path, root, transport):
    app = create_app("http://podfetch.test", tmp_path / "companion.db", root, transport)
    return TestClient(app)


def test_feed_lists_every_episode_with_its_real_enclosure(tmp_path):
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    response = client.get("/companion/demo/feed/hpr-bash-tips.xml")
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/rss+xml")
    channel = ET.fromstring(response.text).find("channel")
    assert channel.findtext("title") == TITLE
    items = channel.findall("item")
    assert len(items) == 2
    urls = {item.find("enclosure").get("url") for item in items}
    assert urls == {"https://example.test/hpr1.mp3", "https://example.test/hpr2.mp3"}
    assert items[0].find("enclosure").get("length") == "12345"


def test_feed_404s_without_a_configured_library(tmp_path):
    transport, _ = podfetch_transport({})
    client = start(tmp_path, None, transport)
    assert client.get("/companion/demo/feed/hpr-bash-tips.xml").status_code == 404


def test_feed_404s_for_an_unknown_show(tmp_path):
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    assert client.get("/companion/demo/feed/no-such-show.xml").status_code == 404


def test_import_resolves_ids_and_stores_the_precomputed_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "RESOLVE_TRIES", 1)
    root = library(tmp_path)
    transport, calls = podfetch_transport({"https://example.test/hpr1.mp3": EP1, "https://example.test/hpr2.mp3": EP2})
    client = start(tmp_path, root, transport)
    result = client.post("/companion/demo/import").json()
    assert {item["key"]: item["episode_id"] for item in result["imported"]} == {"hpr1": EP1}
    assert [item["key"] for item in result["skipped"]] == ["hpr2"]   # no precomputed brief for hpr2
    brief = client.get(f"/companion/episodes/{EP1}/brief").json()
    assert brief["status"] == "ready" and brief["verdict"] == "HEAR" and brief["summary"] == "Summary of hpr1."


def test_import_is_safe_to_call_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "RESOLVE_TRIES", 1)
    root = library(tmp_path)
    transport, _ = podfetch_transport({"https://example.test/hpr1.mp3": EP1, "https://example.test/hpr2.mp3": EP2})
    client = start(tmp_path, root, transport)
    first = client.post("/companion/demo/import").json()
    second = client.post("/companion/demo/import").json()
    assert first["imported"] == second["imported"]
    assert client.get(f"/companion/episodes/{EP1}/brief").json()["status"] == "ready"


def test_episodes_podfetch_has_not_parsed_yet_are_reported_not_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "RESOLVE_TRIES", 1)
    root = library(tmp_path)
    transport, _ = podfetch_transport({}, podcast_exists=False)
    client = start(tmp_path, root, transport)
    result = client.post("/companion/demo/import").json()
    assert result["imported"] == []
    assert {item["key"] for item in result["skipped"]} == {"hpr1", "hpr2"}
    assert all("hasn't parsed" in item["reason"] for item in result["skipped"])


def test_import_waits_for_a_feed_that_is_still_being_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "RESOLVE_TRIES", 3)
    monkeypatch.setattr(demo, "RESOLVE_WAIT_SECONDS", 0)
    root = library(tmp_path, keys=("hpr1",), briefs_for=("hpr1",))
    calls = []
    attempts = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/api/v1/users/me":
            return httpx.Response(404)
        if path == "/api/v1/podcasts":
            attempts["n"] += 1
            return httpx.Response(200, json=[{"id": PODCAST, "name": TITLE, "rssfeed": "x"}] if attempts["n"] >= 2 else [])
        if path == f"/api/v1/podcasts/{PODCAST}/episodes":
            return httpx.Response(200, json=[{"podcastEpisode": {"episode_id": EP1, "url": "https://example.test/hpr1.mp3",
                                                                  "date_of_recording": "2026-01-01T00:00:00"},
                                              "podcastHistoryItem": None}])
        return httpx.Response(404)

    client = start(tmp_path, root, httpx.MockTransport(handle))
    result = client.post("/companion/demo/import").json()
    assert {item["key"]: item["episode_id"] for item in result["imported"]} == {"hpr1": EP1}


def test_import_409s_without_a_configured_library(tmp_path):
    transport, _ = podfetch_transport({})
    client = start(tmp_path, None, transport)
    assert client.post("/companion/demo/import").status_code == 409


def test_show_id_pattern_accepts_plain_ids_and_rejects_traversal():
    assert demo.SHOW_ID_RE.match("hpr-bash-tips")
    assert demo.SHOW_ID_RE.match("show2")
    for bad in ("..", "../etc", "hpr-bash-tips/../../etc", "a/b", "", ".hidden", "UPPER"):
        assert not demo.SHOW_ID_RE.match(bad), bad


def test_feed_rejects_a_traversal_show_id_without_touching_the_filesystem(tmp_path):
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    # "..xml" is one path segment; {show_id} captures ".." before the literal ".xml" suffix.
    response = client.get("/companion/demo/feed/...xml")
    assert response.status_code == 404
    assert "not a valid demo show id" in response.json()["detail"]


def test_import_rejects_a_traversal_show_id(tmp_path):
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    for bad in ("../../etc", "hpr-bash-tips/../../etc"):
        response = client.post("/companion/demo/import", params={"show_id": bad})
        assert response.status_code == 404 and "not a valid demo show id" in response.json()["detail"]


def test_unreadable_manifest_file_is_409_not_500(tmp_path, monkeypatch):
    """docker cp can leave the copied library owned by another user: a manifest the
    companion cannot even stat must answer 409 with a fix-it sentence, never a bare 500."""
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    real_is_file = Path.is_file

    def fake_is_file(self):
        if self.name == "_feed.json":
            raise PermissionError(13, "Permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    response = client.get("/companion/demo/feed/hpr-bash-tips.xml")
    assert response.status_code == 409
    assert "load-demo.sh" in response.json()["detail"]


def test_unreadable_manifest_contents_is_409_not_500(tmp_path, monkeypatch):
    root = library(tmp_path)
    transport, _ = podfetch_transport({})
    client = start(tmp_path, root, transport)
    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self.name == "_feed.json":
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    response = client.post("/companion/demo/import")
    assert response.status_code == 409
    assert "load-demo.sh" in response.json()["detail"]
