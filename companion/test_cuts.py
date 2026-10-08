"""Cut plans and MP3 export: both planning modes, exclusions and the budget, render jobs on
synthetic audio, Range and delete, cache eviction and low disk, the timing checks against the
downloaded file, and the transcript that fits the file (step 0).

Every test runs offline: a PodFetch stand-in answers through httpx.MockTransport, speech-to-text
and the AI are fakes, and any socket connection fails the test (``offline``)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import cuts, engine, jobs
from companion.llm import FakeLLM, LLMError, get_llm
from companion.server import create_app

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe are not installed")

EP = "3f0c2a8e-5b7d-4c1e-9a6f-0d2e4b6c8a10"         # the public episode_id our URLs use
PID = "01a0c878-04ff-7a30-8bb8-f51c9f54e8b0"        # PodFetch's own id for the same episode
EP2 = "7d1e9b2c-3a4f-4e5d-8c6b-1f2a3b4c5d6e"
PID2 = "01a0c878-0500-71b3-9bc4-3e7dcd804939"
AUDIO_URL = "https://feeds.example.test/show/ep1.mp3"
FILE_PATH = "/podcasts/Show/ep1/podcast.mp3"
DURATION = 120.0

# 15 segments of 8 s: a sponsor read, BGP passages, a history aside the listener skips, filler.
LINES = [
    "Welcome to the show. This episode is sponsored by Acme, use promo code NET.",
    "Today we look at BGP route selection and the best path algorithm in detail.",
    "First some history of the protocol from the early ARPANET days and old routers.",
    "BGP route selection starts with weight and then local preference on the router.",
    "Then the AS path length decides which BGP route wins among the candidates.",
    "Now a quick story about my cat Pixel who sleeps on the warm switch all day.",
    "The weather has been lovely this week and we went hiking up the green hills.",
    "My neighbour repaints his fence every spring in a slightly different blue.",
    "We tried a new bakery that sells sourdough loaves shaped like small owls.",
    "Our producer insists that tea tastes better from a heavy ceramic mug.",
    "Finally BGP route selection uses MED and the router id as tie breakers here.",
    "Somebody once mailed us a postcard showing a lighthouse on a foggy cape.",
    "The studio clock runs four minutes fast and nobody has fixed it for years.",
    "Next week the guests talk about carrots, gardens and patient soil work.",
    "Thanks for listening to the show today, see you soon everyone.",
]
TIMES = [(8.0 * i, 8.0 * i + 8.0) for i in range(len(LINES))]
AD, HISTORY = (0.0, 8.0), (16.0, 24.0)


def native(lines=LINES, times=TIMES, source="feed"):
    """PodFetch's preferred-transcript payload."""
    return {"id": "t-feed", "source": source, "segments": [
        {"idx": i + 1, "startMs": int(a * 1000), "endMs": int(b * 1000), "speaker": None, "text": text}
        for i, ((a, b), text) in enumerate(zip(times, lines, strict=True))]}


def vtt(lines=LINES, times=TIMES):
    def stamp(s):
        return f"00:{int(s) // 60:02d}:{s % 60:06.3f}"
    cues = [f"{stamp(a)} --> {stamp(b)}\n{text}" for (a, b), text in zip(times, lines, strict=True)]
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"


def tone(path: Path, seconds: float = DURATION) -> bytes:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}:sample_rate=44100", "-c:a", "libmp3lame",
                    "-q:a", "5", str(path)], check=True)
    return path.read_bytes()


@pytest.fixture(scope="module")
def audio(tmp_path_factory) -> bytes:
    if not HAVE_FFMPEG:
        return b"not audio"
    return tone(tmp_path_factory.mktemp("tone") / "tone.mp3")


class FakePodFetch:
    """PodFetch's API for the cut-plan routes; records every request."""

    def __init__(self, audio: bytes = b""):
        self.audio = audio
        self.episodes: dict[str, dict] = {}
        self.native: dict[str, dict] = {}
        self.listing: dict[str, list] = {}
        self.files: dict[tuple[str, str], tuple[str, str]] = {}
        self.playlist: list[str] = []
        self.download_answer = 200
        self.requests: list[tuple[str, str]] = []
        self.logins: dict[str, str] = {}        # Authorization value -> username; empty: login off
        self.carried: list[tuple[str, str | None]] = []

    def add(self, episode_id=EP, podfetch_id=PID, *, name="N4N064: BGP", downloaded=True, url=AUDIO_URL,
            transcript="feed"):
        self.episodes[episode_id] = {
            "id": podfetch_id, "episode_id": episode_id, "name": name, "url": url, "total_time": int(DURATION),
            "status": downloaded, "download_time": "2026-09-24T10:00:00" if downloaded else None,
            "local_url": f"http://podfetch.test{FILE_PATH}" if downloaded
            else f"http://podfetch.test/proxy/podcast?episodeId={episode_id}"}
        if transcript == "feed":
            self.native[podfetch_id] = native()
        elif transcript == "generated":
            self.listing[podfetch_id] = [{"id": "c3a1e0de-0b6f-4a53-8f55-0d8e2f1b7a90", "source": "generated",
                                          "status": "parsed", "mimeType": "text/vtt"}]
            self.files[(podfetch_id, "c3a1e0de-0b6f-4a53-8f55-0d8e2f1b7a90")] = (vtt(), "text/vtt")
            self.native[podfetch_id] = native(source="generated")
        return self.episodes[episode_id]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        login = request.headers.get("authorization")
        self.carried.append((path, login))
        if self.logins and login not in self.logins:
            return httpx.Response(403)                     # PodFetch's basic auth refuses
        if path == "/api/v1/users/me":
            return httpx.Response(200, json={"username": self.logins[login]}) if self.logins else httpx.Response(404)
        parts = path.strip("/").split("/")
        by_pid = {e["id"]: e for e in self.episodes.values()}
        if request.method == "GET" and parts[:3] == ["api", "v1", "episodes"] and parts[3] in self.episodes:
            return httpx.Response(200, json={"podcastEpisode": self.episodes[parts[3]]})
        if parts[:4] == ["api", "v1", "podcasts", "episodes"] and parts[4] in by_pid:
            pid = parts[4]
            if parts[5:] == ["transcripts"]:
                return httpx.Response(200, json=self.listing.get(pid, []))
            if parts[5:6] == ["transcripts"] and parts[7:] == ["file"] and (pid, parts[6]) in self.files:
                body, kind = self.files[(pid, parts[6])]
                return httpx.Response(200, content=body.encode(), headers={"content-type": kind})
            if parts[5:] == ["transcript"] and pid in self.native:
                return httpx.Response(200, json=self.native[pid])
            return httpx.Response(404)
        if request.method == "PUT" and parts[3:] == [parts[3], "episodes", "download"] and parts[3] in self.episodes:
            if self.download_answer == 200:
                episode = self.episodes[parts[3]]
                episode.update(status=True, download_time="2026-09-24T11:00:00",
                               local_url=f"http://podfetch.test{FILE_PATH}")
            return httpx.Response(self.download_answer)
        if path == "/api/v1/playlist":
            items = [{"podcastEpisode": self.episodes[e], "podcastHistoryItem": None} for e in self.playlist]
            return httpx.Response(200, json=[{"id": "q1", "name": "Listen next", "items": items}])
        if path == FILE_PATH and any(e["status"] for e in self.episodes.values()):
            return httpx.Response(200, content=self.audio, headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)


class FakeSpeech:
    """Speech-to-text for the 3 spot-check windows: each window 'hears' the transcript line
    `said[n]`, starting `at[n]` seconds into the clip. Same interface as the AI provider's
    ``transcribe_audio(path)``."""

    def __init__(self, said, at):
        self.said, self.at, self.calls, self.listened = said, at, [], []

    def transcribe_audio(self, path):
        name = Path(path).name
        if not name.startswith("window-"):             # the render's listen-back: a tone has no words
            self.listened.append(name)
            return {"text": "", "duration": None, "segments": []}
        number = int(name.split("-")[1].split(".")[0])
        self.calls.append(name)
        head = Path(path).read_bytes()[:3]
        assert head == b"ID3" or head[:1] == b"\xff", "windows are mp3, never opus"
        text = LINES[self.said[number]]
        return {"text": text, "duration": 15.0,
                "segments": [{"start": self.at[number], "end": self.at[number] + 7.5, "text": text}]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test here may open a network connection; planning must not even try."""
    attempts: list[str] = []

    def refuse(name):
        def blocked(*args, **kwargs):
            attempts.append(name)
            raise AssertionError(f"a test tried the network ({name})")
        return blocked

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse(name))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))
    monkeypatch.delenv("ALLOW_PRIVATE_FEEDS", raising=False)
    monkeypatch.delenv("CACHE_MAX_MB", raising=False)
    monkeypatch.setattr(jobs, "DOWNLOAD_POLL_SECONDS", 0.01)
    return attempts


@pytest.fixture
def world(tmp_path, audio):
    fake = FakePodFetch(audio)
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[cuts.get_speech] = lambda: None
    return fake, app, TestClient(app), tmp_path


def plan(client, **body):
    body = {"episode_ids": [EP], "want": "BGP route selection", "skip": "history", "skip_ads": True,
            "mode": "keyword", **body}
    response = client.post("/companion/plans", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def times(spans, enabled_only=False):
    return [(s["start"], s["end"]) for s in spans if s["enabled"] or not enabled_only]


def overlaps(span, interval):
    return span[0] < interval[1] and span[1] > interval[0]


def wait_for(client, job_id, timeout=60.0, headers=None):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/companion/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("done", "failed") or time.monotonic() > deadline:
            return job
        time.sleep(0.05)


def render(client, plan_id):
    response = client.post(f"/companion/plans/{plan_id}/render")
    assert response.status_code == 202, response.text
    return wait_for(client, response.json()["job_id"])


# ── plans ───────────────────────────────────────────────────────────────────


def test_keyword_plan_keeps_what_you_want_and_never_what_you_skip(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    assert made["status"] == "ready" and made["mode"] == "keyword"
    assert (made["want"], made["skip"], made["minutes"]) == ("BGP route selection", "history", None)
    # keyword padding (6 s) ends mid-sentence; each edge then moves out to its whole sentence
    assert times(made["spans"]) == [(8.0, 16.0), (24.0, 48.0), (72.0, 96.0)]
    for span in made["spans"]:
        assert not overlaps((span["start"], span["end"]), AD) and not overlaps((span["start"], span["end"]), HISTORY)
        assert set(span) >= {"id", "episode_id", "start", "end", "text", "why", "enabled"}
        assert span["enabled"] and span["episode_id"] == EP and "bgp" in span["why"]
    assert [s["id"] for s in made["spans"]] == ["s1", "s2", "s3"]
    assert made["kept_seconds"] == 56.0 and made["source_seconds"] == DURATION
    assert made["omitted"] == [] and made["needs_timing"] == []
    assert made["episodes"][0]["origin"] == "feed" and made["episodes"][0]["timing"] == "unverified"
    assert client.get(f"/companion/plans/{made['id']}").json() == made
    assert not {"base_start", "digest", "exclude"} & ({k for s in made["spans"] for k in s} | set(made["episodes"][0]))


def test_the_budget_holds_and_what_did_not_fit_is_listed(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client, minutes=0.5)                     # 30 s: the two densest spans fit, one doesn't
    assert made["kept_seconds"] == 32.0                  # ... and the last sentence is finished
    assert times(made["spans"]) == [(8.0, 16.0), (24.0, 48.0)]
    assert made["omitted"] == [{"episode_id": EP, "start": 74.0, "end": 94.0, "reason": "over the time budget"}]


def test_sponsor_reads_stay_in_only_when_asked(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client, want="sponsored promo code acme", skip="", minutes=None)
    assert made["status"] == "empty" and made["spans"] == []
    kept = plan(client, want="sponsored promo code acme", skip="", skip_ads=False)
    assert kept["spans"] and overlaps(times(kept["spans"])[0], AD)


def test_patch_toggles_passages_and_adds_context_inside_the_episode(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    url = f"/companion/plans/{made['id']}"
    changed = client.patch(url, json={"spans": [{"id": "s1", "enabled": False}], "context_seconds": 5}).json()
    assert changed["context_seconds"] == 5
    # s2 never widens into history; 5 s of context then finishes the sentence it lands in
    assert times(changed["spans"]) == [(8.0, 16.0), (24.0, 56.0), (64.0, 104.0)]
    assert [s["enabled"] for s in changed["spans"]] == [False, True, True]
    assert changed["kept_seconds"] == 72.0
    wide = client.patch(url, json={"context_seconds": 30}).json()
    s2, s3 = wide["spans"][1], wide["spans"][2]
    assert (s2["start"], s2["end"], s3["start"], s3["end"]) == (24.0, 56.0, 56.0, 120.0)    # meet on a sentence edge
    assert wide["kept_seconds"] == 96.0
    back = client.patch(url, json={"spans": [{"id": "s1", "enabled": True}], "context_seconds": 0}).json()
    assert times(back["spans"]) == times(made["spans"]) and back["kept_seconds"] == made["kept_seconds"]
    assert client.get(url).json() == back
    assert client.patch(url, json={"spans": [{"id": "s9", "enabled": False}]}).status_code == 422
    assert client.patch(url, json={"context_seconds": 500}).status_code == 422


def test_ai_plan_uses_ids_the_model_picks_and_the_server_keeps_exclusions_and_budget(world):
    fake, app, client, _ = world
    fake.add()
    llm = FakeLLM([
        {"ranges": [{"start_id": "40", "end_id": "41", "why": "invented", "relevance": 3}]},   # every id invented
        {"ranges": [{"start_id": "2", "end_id": "5", "why": "Route selection, weight to AS path", "relevance": 3},
                    {"start_id": "11", "end_id": "11", "why": "Tie breakers", "relevance": 2},
                    {"start_id": "3", "end_id": "99", "why": "runs off the end", "relevance": 3}]},
    ])
    app.dependency_overrides[get_llm] = lambda: llm
    made = plan(client, mode="ai", minutes=0.4)            # 24 s
    assert len(llm.calls) == 2 and "not lines of this transcript" in llm.calls[1]["user"]
    prompt = llm.calls[0]["user"]
    assert "WANTS: BGP route selection" in prompt and "SKIP: history" in prompt and "evidence only" in prompt
    assert "\n2 00:08 Today we look at BGP" in prompt and "not instructions" in llm.calls[0]["system"]
    assert made["mode"] == "ai" and made["status"] == "ready"
    # the model asked for 8-40 s, which holds the history aside: the skip rule cuts it out
    assert times(made["spans"]) == [(8.0, 16.0), (24.0, 40.0)]
    assert all(s["why"] == "Route selection, weight to AS path" for s in made["spans"])
    assert made["kept_seconds"] == 24.0
    assert made["omitted"] == [{"episode_id": EP, "start": 80.0, "end": 88.0, "reason": "over the time budget"}]
    assert made["spans"][1]["segment_ids"] == ["4", "5"]


def test_ai_mode_forces_out_only_segments_that_say_a_skip_word_and_nothing_wanted(world):
    fake, app, client, _ = world
    fake.add()
    llm = FakeLLM([{"ranges": [{"start_id": "2", "end_id": "5", "why": "the whole stretch", "relevance": 3}]}])
    app.dependency_overrides[get_llm] = lambda: llm
    # "route" is wanted and "history" skipped: line 3 says only "history", so it goes; a line that
    # also says a wanted word stays for the model to judge
    made = plan(client, mode="ai", want="route selection", skip="history of routers")
    assert times(made["spans"]) == [(8.0, 16.0), (24.0, 40.0)]
    llm.responses.append({"ranges": [{"start_id": "2", "end_id": "5", "why": "the whole stretch", "relevance": 3}]})
    made = plan(client, mode="ai", want="route selection", skip="bgp")            # every line says bgp
    assert times(made["spans"]) == [(8.0, 40.0)]


def test_ai_mode_needs_a_provider_and_reports_its_errors(world):
    fake, app, client, _ = world
    fake.add()
    response = client.post("/companion/plans", json={"episode_ids": [EP], "want": "bgp", "mode": "ai"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Connect AI in Settings → AI, or use keyword mode."
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMError("The key was rejected.")])
    response = client.post("/companion/plans", json={"episode_ids": [EP], "want": "bgp", "mode": "ai"})
    assert response.status_code == 502 and response.json()["detail"] == "The key was rejected."


def test_long_transcripts_are_sent_in_parts_and_share_one_budget(world):
    fake, app, client, _ = world
    fake.add()
    fake.add(EP2, PID2, name="Second episode")

    def answer(user, **_call):
        """Picks whatever lines this part holds: line 11 matters most, line 2 is background."""
        ranges = []
        if "\n11 01:20 " in user:
            ranges.append({"start_id": "11", "end_id": "11", "relevance": 3,
                           "why": "tie breakers" if "N4N064" in user else "the other episode"})
        if "\n2 00:08 " in user:
            ranges.append({"start_id": "2", "end_id": "2", "why": "intro", "relevance": 2})
        return {"ranges": ranges}
    llm = FakeLLM([answer] * 20)
    llm.max_input_chars = 1200                          # a small provider: each episode goes in parts
    app.dependency_overrides[get_llm] = lambda: llm
    made = plan(client, episode_ids=[EP, EP2], mode="ai", minutes=0.2)          # 12 s: one passage
    assert len(llm.calls) >= 4 and "(part 1 of" in llm.calls[0]["user"]
    assert sum("\n11 01:20 " in c["user"] for c in llm.calls) == 2          # each line sent once per episode
    assert [(s["episode_id"], s["start"], s["why"]) for s in made["spans"]] == [(EP, 80.0, "tie breakers")]
    over = "over the time budget"
    assert made["omitted"] == [{"episode_id": EP, "start": 8.0, "end": 16.0, "reason": over},
                               {"episode_id": EP2, "start": 8.0, "end": 16.0, "reason": over},
                               {"episode_id": EP2, "start": 80.0, "end": 88.0, "reason": over}]


def test_planning_makes_no_network_calls(world, offline):
    fake, app, client, _ = world
    fake.add()
    fake.add(EP2, PID2, transcript=None)
    plan(client, episode_ids=[EP, EP2])
    app.dependency_overrides[get_llm] = lambda: FakeLLM([{"ranges": []}])
    plan(client, mode="ai")
    assert offline == []
    assert not any(method == "PUT" or path.startswith("/podcasts/") for method, path in fake.requests)


def test_episodes_without_timing_wait_and_the_queue_is_a_source(world):
    fake, _app, client, _ = world
    fake.add()
    fake.add(EP2, PID2, name="No transcript", transcript=None)
    fake.playlist = [EP2, EP]
    made = plan(client, episode_ids=None, source="queue")
    assert made["status"] == "ready" and {s["episode_id"] for s in made["spans"]} == {EP}
    assert made["needs_timing"] == [{"episode_id": EP2, "reason": cuts.NO_TIMING}]
    assert [e["episode_id"] for e in made["episodes"]] == [EP2, EP]
    waiting = plan(client, episode_ids=[EP2])
    assert waiting["status"] == "needs_timing" and waiting["spans"] == []
    fake.playlist = []
    response = client.post("/companion/plans", json={"source": "queue", "want": "bgp"})
    assert response.status_code == 422 and "Listen next list is empty" in response.json()["detail"]


@pytest.mark.parametrize("body, message", [
    ({"want": "bgp"}, "Choose the episodes"),
    ({"episode_ids": [EP], "want": "  "}, "Say what you want to hear"),
    ({"episode_ids": [EP], "want": "what is it"}, "in a few words"),
    ({"episode_ids": [EP], "want": "bgp", "minutes": 0}, "between 1 and 600"),
])
def test_requests_that_cannot_be_planned_say_why(world, body, message):
    fake, _app, client, _ = world
    fake.add()
    response = client.post("/companion/plans", json=body)
    assert response.status_code == 422 and message in response.json()["detail"]


def test_a_transcript_podfetch_made_from_the_file_counts_as_checked_before_any_export(world):
    fake, _app, client, tmp_path = world
    fake.add(transcript="generated")
    made = plan(client)
    assert made["status"] == "ready"
    assert (made["episodes"][0]["origin"], made["episodes"][0]["timing"]) == ("generated", "ok")
    assert not (tmp_path / "audio-cache").exists()                       # nothing was cached
    assert not any(path.startswith("/podcasts/") for _method, path in fake.requests)


def test_publisher_transcripts_stay_unverified_until_an_export_checks_them(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    assert made["status"] == "ready"
    assert (made["episodes"][0]["origin"], made["episodes"][0]["timing"]) == ("feed", "unverified")


def test_a_library_transcript_made_from_this_exact_file_counts_as_checked(tmp_path):
    show = tmp_path / "library" / "show"
    show.mkdir(parents=True)
    (show / "_feed.json").write_text(json.dumps({"feed": "https://example.test/feed", "episodes": [
        {"key": "one", "title": "Episode one", "duration": DURATION, "audio": AUDIO_URL, "words_file": "one.txt"}]}))
    (show / "one.txt").write_text(" ".join(LINES))
    made_from = {"source_url": AUDIO_URL, "sha256": "c" * 64, "byte_length": 10, "duration_seconds": DURATION}
    (show / "one.timed.json").write_text(json.dumps({"duration": DURATION, "media": made_from, "segments": [
        {"start": a, "end": b, "text": text} for (a, b), text in zip(TIMES, LINES, strict=True)]}))
    fake = FakePodFetch()
    episode = fake.add(transcript=None)
    client = TestClient(create_app("http://podfetch.test", tmp_path / "c.db", tmp_path / "library",
                                   httpx.MockTransport(fake)))
    made = plan(client)
    assert (made["episodes"][0]["origin"], made["episodes"][0]["timing"]) == ("library", "unverified")
    (tmp_path / "audio-cache").mkdir()
    (tmp_path / "audio-cache" / f"{EP}.mp3").write_bytes(b"x" * 10)
    for sha, timing in (("c" * 64, "ok"), ("d" * 64, "unverified")):   # this exact file, then another copy
        with sqlite3.connect(tmp_path / "c.db") as db:
            cuts.save_media(db, cuts.Media(EP, tmp_path / "audio-cache" / f"{EP}.mp3", "podfetch",
                                           cuts.audio_version(episode), {**made_from, "sha256": sha}))
        assert plan(client)["episodes"][0]["timing"] == timing


@pytest.mark.parametrize("transcript", ["feed", "generated"])
def test_a_downloaded_file_that_is_too_short_puts_the_episode_in_needs_timing(world, transcript):
    fake, _app, client, tmp_path = world
    episode = fake.add(transcript=transcript)          # even PodFetch's own transcript: 120 s, 90 s file
    folders = cuts.Folders(tmp_path, tmp_path / "audio-cache", tmp_path / "cuts", cuts.cache_max_bytes())
    folders.cache.mkdir()
    (folders.cache / f"{EP}.mp3").write_bytes(b"x" * 10)
    with sqlite3.connect(tmp_path / "companion.db") as db:
        db.row_factory = sqlite3.Row
        cuts.save_media(db, cuts.Media(EP, folders.cache / f"{EP}.mp3", "podfetch", cuts.audio_version(episode),
                                       {"source_url": AUDIO_URL, "sha256": "a" * 64, "byte_length": 10,
                                        "duration_seconds": 90.0}))
    made = plan(client)
    assert made["status"] == "needs_timing" and made["spans"] == []
    assert "past the end of your audio file" in made["needs_timing"][0]["reason"]
    assert made["episodes"][0]["timing"] == "mismatch" and made["episodes"][0]["duration"] == 90.0


def test_the_script_splits_a_line_a_cut_falls_inside_at_the_same_word_the_check_does():
    words = " ".join(f"w{k}" for k in range(30))
    record = {"id": "p", "want": "w", "skip": "", "kept_seconds": 10.0, "source_seconds": 30.0, "omitted": [],
              "spans": [{"id": "s1", "episode_id": EP, "start": 10.0, "end": 20.0, "enabled": True, "why": "w"}],
              "episodes": [{"episode_id": EP, "title": "T", "duration": 30.0, "ads": [], "skips": []}],
              "lines": {EP: [[0.0, 30.0, words, 0]]}}
    parts = cuts.script(record)["episodes"][0]["parts"]
    assert [(p["kind"], p["start"], p["end"]) for p in parts] == [("cut", 0.0, 10.0), ("keep", 10.0, 20.0), ("cut", 20.0, 30.0)]
    kept = parts[1]["lines"]
    assert kept == [{"start": 10.0, "end": 20.0, "text": " ".join(f"w{k}" for k in range(10, 20)), "partial": True}]
    shown = [t for line in kept for t in engine.verify.tokens(line["text"])]
    checked = [t for t, _time in engine.verify.expected_words([(0.0, 30.0, words)], 10.0, 20.0)]
    assert shown == checked


def test_ads_endpoint_lists_sponsor_reads(world):
    fake, _app, client, _ = world
    fake.add()
    assert client.get(f"/companion/episodes/{EP}/ads").json() == {
        "episode_id": EP, "spans": [{"start": 0.0, "end": 8.0, "label": "Sponsor"}], "origin": "feed"}


# ── the transcript whose timing fits the file (step 0) ─────────────────────


def test_podfetchs_own_whisper_transcript_wins_over_the_feed(world):
    fake, _app, client, _ = world
    fake.add(transcript="generated")
    fake.native[PID] = native(lines=["A feed line about something else."] * 15)       # PodFetch prefers this
    doc = client.get(f"/companion/episodes/{EP}/transcript").json()
    assert doc["origin"] == "generated" and doc["source"] == "Made from your audio file"
    assert doc["segments"][1] == {"id": "seg-0002", "start": 8.0, "end": 16.0, "text": LINES[1]}
    fake.listing[PID][0]["status"] = "pending"            # a Whisper job still running
    doc = client.get(f"/companion/episodes/{EP}/transcript").json()
    assert doc["origin"] == "feed" and doc["source"] == "Publisher transcript"
    assert doc["segments"][0] == {"id": "1", "start": 0.0, "end": 8.0, "text": "A feed line about something else."}
    fake.native.pop(PID)
    fake.listing.pop(PID)
    doc = client.get(f"/companion/episodes/{EP}/transcript").json()
    assert (doc["origin"], doc["timed"], doc["segments"]) == (None, False, [])


def test_library_transcripts_say_where_they_came_from_and_how_long_their_audio_was(tmp_path):
    from companion.test_server import EPISODE, fixture
    from companion.transcripts import TranscriptLibrary, load_timing

    client, _db, library_root, transport = fixture(tmp_path)
    public = client.get(f"/companion/episodes/{EPISODE}/transcript").json()
    assert public["origin"] == "library"
    timed = library_root / "sample" / "one.timed.json"            # give the timed file its word times
    doc = json.loads(timed.read_text())
    doc["words"] = [{"word": w, "start": 20 + i, "end": 20.5 + i} for i, w in enumerate("A prefix defines".split())]
    timed.write_text(json.dumps(doc))
    from companion.deps import PodFetch
    podfetch = PodFetch("http://podfetch.test", transport=transport)
    timing = load_timing(EPISODE, podfetch.episode(EPISODE), podfetch, TranscriptLibrary(library_root))
    assert timing.origin == "library" and timing.made_from == {"duration_seconds": 120.0}
    assert [s.id for s in timing.segments] == [s["id"] for s in public["segments"]]
    assert [w.start for w in timing.segments[1].words] == [20, 21, 22]      # spot checks match on these
    assert client.get(f"/companion/episodes/{EPISODE}/transcript").json() == public   # the JSON is unchanged
    file_150s = {"source_url": "https://example.test/source.mp3", "sha256": "b" * 64, "duration_seconds": 150.0}
    check = engine.check_timing(timing.segments, "library", file_150s, timing.made_from)
    assert check == "mismatch" and "02:00 version" in check.reason


def test_a_library_transcript_saved_as_srt_in_a_txt_file_is_served_with_its_timing(tmp_path):
    show = tmp_path / "library" / "show"
    show.mkdir(parents=True)
    (show / "_feed.json").write_text(json.dumps({"feed": "https://example.test/feed", "episodes": [
        {"key": "zero", "title": "Episode zero", "duration": 120, "audio": AUDIO_URL, "words_file": "x-zero.txt"}]}))
    (show / "x-zero.txt").write_text("1\n00:00:00,080 --> 00:00:02,480\nWelcome to the show, where BGP\n\n"
                                     "2\n00:00:02,480 --> 00:00:05,379\nand the rest of the jargon get explained.\n")
    fake = FakePodFetch()
    fake.add(transcript=None)
    client = TestClient(create_app("http://podfetch.test", tmp_path / "c.db", tmp_path / "library",
                                   httpx.MockTransport(fake)))
    doc = client.get(f"/companion/episodes/{EP}/transcript").json()
    assert (doc["origin"], doc["source"], doc["timed"]) == ("library", "Your transcript library", True)
    assert doc["segments"] == [
        {"id": "seg-0001", "start": 0.08, "end": 2.48, "text": "Welcome to the show, where BGP", "words": []},
        {"id": "seg-0002", "start": 2.48, "end": 5.379, "text": "and the rest of the jargon get explained.",
         "words": []}]
    assert doc["text"] == "Welcome to the show, where BGP\nand the rest of the jargon get explained."
    made = plan(client, want="bgp jargon", skip="")
    assert made["status"] == "ready" and made["episodes"][0]["origin"] == "library"
    (show / "x-zero.txt").write_text("Plain words with no timing at all.")
    doc = client.get(f"/companion/episodes/{EP}/transcript").json()
    assert (doc["timed"], doc["segments"], doc["text"]) == (False, [], "Plain words with no timing at all.")


# ── render jobs ─────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_render_makes_one_mp3_with_an_index_and_supports_range_and_delete(world):
    fake, _app, client, tmp_path = world
    fake.add(transcript="generated")
    made = plan(client)
    job = render(client, made["id"])
    assert job["status"] == "done" and job["progress"] == 1.0 and job["error"] is None, job
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    # a tone never pauses, so each of the 2 joins gets a short beat of silence (engine.edges.pace)
    assert cut["plan_id"] == made["id"] and abs(cut["duration"] - made["kept_seconds"] - 0.6) <= 0.5
    assert [(i["source_start"], i["source_end"]) for i in cut["index"]] == [(8.0, 16.0), (24.0, 48.0), (72.0, 96.0)]
    assert [(i["cut_start"], i["cut_end"]) for i in cut["index"]] == [(0.0, 8.0), (8.3, 32.3), (32.6, 56.6)]
    assert cut["index"][0]["episode_id"] == EP and cut["index"][0]["title"] == "N4N064: BGP"
    assert cut["label"] is None and cut["timing"][0]["status"] == "ok"      # made from this very file
    mp3 = f"/companion/cuts/{job['cut_id']}.mp3"
    whole = client.get(mp3)
    assert whole.status_code == 200 and whole.headers["content-type"] == "audio/mpeg"
    assert len(whole.content) == cut["size_bytes"] and whole.headers["accept-ranges"] == "bytes"
    part = client.get(mp3, headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == whole.content[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{cut['size_bytes']}"
    assert client.head(mp3).status_code == 200
    files = sorted(p.name for p in (tmp_path / "cuts").iterdir())
    assert files == sorted(f"{job['cut_id']}{ext}" for ext in (".json", ".md", ".mp3"))
    assert client.get("/companion/storage").json()["cuts"] == 1
    assert client.delete(f"/companion/cuts/{job['cut_id']}").json() == {"deleted": True}
    assert client.get(f"/companion/cuts/{job['cut_id']}").status_code == 404
    assert client.get(mp3).status_code == 404 and list((tmp_path / "cuts").iterdir()) == []


@needs_ffmpeg
def test_render_asks_podfetch_to_download_first_and_reuses_the_cached_file(world):
    fake, _app, client, tmp_path = world
    fake.add(downloaded=False, transcript="generated")
    made = plan(client)
    job = render(client, made["id"])
    assert job["status"] == "done", job
    assert ("PUT", f"/api/v1/podcasts/{EP}/episodes/download") in fake.requests
    assert [p.name for p in (tmp_path / "audio-cache").iterdir()] == [f"{EP}.mp3"]
    fetched = fake.requests.count(("GET", FILE_PATH))
    assert render(client, made["id"])["status"] == "done"
    assert fake.requests.count(("GET", FILE_PATH)) == fetched          # the second export reuses the copy


@needs_ffmpeg
def test_when_podfetch_refuses_the_publishers_file_is_fetched_safely_and_labelled(world, monkeypatch, audio):
    fake, _app, client, tmp_path = world
    fake.add(downloaded=False, transcript="generated")
    fake.download_answer = 403
    monkeypatch.setenv("ALLOW_PRIVATE_FEEDS", "true")
    fetched = []

    def safe_fetch(url, dest, max_bytes, allow_private=False, **kwargs):
        fetched.append((url, allow_private, max_bytes))
        Path(dest).write_bytes(audio)
    monkeypatch.setattr(engine, "safe_fetch", safe_fetch)
    made = plan(client)
    job = render(client, made["id"])
    assert job["status"] == "done", job
    assert fetched == [(AUDIO_URL, True, engine.net.DEFAULT_MAX_BYTES)]
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    # PodFetch's Whisper ran on PodFetch's copy; ours may carry other ads
    assert cut["label"] == "timing not checked" and cut["timing"][0]["status"] == "unverified"
    assert "speech-to-text" in cut["timing"][0]["reason"]


@needs_ffmpeg
def test_a_spot_check_shifts_the_cut_by_a_shared_offset_and_is_remembered(world):
    fake, app, client, _ = world
    fake.add()                                            # a feed transcript: unverified until checked
    # windows at 14.4, 60 and 105 s hear lines 2, 7 and 13, 5 s later than the transcript says
    speech = FakeSpeech(said=[2, 7, 13], at=[16 + 5 - 14.4, 56 + 5 - 60, 104 + 5 - 105])
    app.dependency_overrides[cuts.get_speech] = lambda: speech
    first = plan(client)
    job = render(client, first["id"])
    assert job["status"] == "done", job
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    assert cut["timing"][0]["status"] == "ok" and cut["timing"][0]["offset"] == pytest.approx(5.0, abs=1.0)
    assert cut["label"] is None and len(speech.calls) == 3
    shifted = [(round(i["source_start"]), round(i["source_end"])) for i in cut["index"]]
    assert shifted == [(13, 21), (29, 53), (77, 101)]
    again = plan(client)                                   # the stored check now shifts the plan itself
    assert again["episodes"][0]["timing"] == "ok"
    assert [(round(a), round(b)) for a, b in times(again["spans"])] == shifted
    job = render(client, again["id"])
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    assert [(round(i["source_start"]), round(i["source_end"])) for i in cut["index"]] == shifted
    assert len(speech.calls) == 3                          # remembered, not transcribed again


@needs_ffmpeg
def test_a_shift_too_small_to_measure_is_not_applied(tmp_path, audio):
    path = tmp_path / "tone.mp3"
    path.write_bytes(audio)
    segments = engine.from_podfetch(native(), group=False)
    speech = FakeSpeech(said=[2, 7, 13], at=[16 + 1.5 - 14.4, 56 + 1.5 - 60, 104 + 1.5 - 105])
    check = cuts.spot_check_file(segments, path, DURATION, speech)
    assert check == "ok" and check.offset == 0.0 and check.reason == "The transcript matches your audio file."


@needs_ffmpeg
def test_a_mismatched_spot_check_fails_the_job_with_a_way_out(world):
    fake, app, client, _ = world
    fake.add()
    # the first two windows agree with the transcript; the last one runs 20 s later: ads inserted mid-way
    speech = FakeSpeech(said=[2, 8, 11], at=[16 - 14.4, 64 - 60, 88 + 20 - 105])
    app.dependency_overrides[cuts.get_speech] = lambda: speech
    job = render(client, plan(client)["id"])
    assert job["status"] == "failed" and job["cut_id"] is None
    assert job["error"] == "This transcript doesn't match your audio file. Make a transcript from the file."
    assert "ads were probably inserted" in job["detail"]
    later = plan(client)                                   # the next plan knows before any export
    assert later["status"] == "needs_timing" and "Make a transcript from the file" in later["needs_timing"][0]["reason"]


@needs_ffmpeg
def test_a_transcript_that_changed_after_planning_fails_the_job(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    fake.native[PID] = native(times=[(a + 1, b + 1) for a, b in TIMES])
    job = render(client, made["id"])
    assert job["status"] == "failed" and job["error"] == jobs.TRANSCRIPT_CHANGED


@needs_ffmpeg
def test_with_login_on_everything_needs_it_and_a_queued_job_carries_the_callers_login(world):
    fake, _app, client, _ = world
    alice = {"Authorization": "Basic YWxpY2U6YWxpY2UtcGFzc3dvcmQ="}
    fake.logins = {alice["Authorization"]: "alice"}
    fake.add(transcript="generated")
    body = {"episode_ids": [EP], "want": "BGP route selection", "skip": "history"}
    assert client.post("/companion/plans", json=body).status_code == 401
    made = client.post("/companion/plans", json=body, headers=alice).json()
    assert client.get(f"/companion/plans/{made['id']}").status_code == 401
    assert client.post(f"/companion/plans/{made['id']}/render").status_code == 401
    job = wait_for(client, client.post(f"/companion/plans/{made['id']}/render", headers=alice).json()["job_id"],
                   headers=alice)
    assert job["status"] == "done", job
    asked = [(path, login) for path, login in fake.carried if path != "/api/v1/users/me"]
    assert ("/api/v1/podcasts/episodes/" + PID + "/transcripts", alice["Authorization"]) in asked
    assert (FILE_PATH, alice["Authorization"]) in asked                # the job, after the request ended
    assert all(login == alice["Authorization"] for _path, login in asked)
    mp3 = f"/companion/cuts/{job['cut_id']}.mp3"
    for path in (mp3, f"/companion/cuts/{job['cut_id']}", f"/companion/jobs/{job['id']}", "/companion/storage",
                 f"/companion/episodes/{EP}/ads"):
        assert client.get(path).status_code == 401, path
    part = client.get(mp3, headers={**alice, "Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")


def test_render_is_refused_when_the_disk_is_almost_full(world, monkeypatch):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    monkeypatch.setattr(cuts, "free_bytes", lambda path: int(1.2 * cuts.GB))
    response = client.post(f"/companion/plans/{made['id']}/render")
    assert response.status_code == 507
    assert response.json()["detail"].startswith("Only 1.2 GB of disk space is free")
    assert "free up space" in response.json()["detail"]


def test_nothing_to_export_is_refused(world):
    fake, _app, client, _ = world
    fake.add()
    made = plan(client)
    client.patch(f"/companion/plans/{made['id']}", json={"spans": [{"id": s["id"], "enabled": False}
                                                                   for s in made["spans"]]})
    response = client.post(f"/companion/plans/{made['id']}/render")
    assert response.status_code == 422 and "Turn on at least one passage" in response.json()["detail"]


def test_jobs_run_one_at_a_time_five_wait_and_deleting_one_cancels_it(world, monkeypatch):
    fake, app, client, _ = world
    fake.add()
    made = plan(client)
    started, release, ran = threading.Event(), threading.Event(), []

    def slow(job, queue):
        ran.append(job.id)
        started.set()
        release.wait(10)
    monkeypatch.setattr(jobs, "run", slow)
    url = f"/companion/plans/{made['id']}/render"
    first = client.post(url).json()["job_id"]
    assert started.wait(5)
    waiting = [client.post(url).json()["job_id"] for _ in range(jobs.MAX_WAITING)]
    full = client.post(url)
    assert full.status_code == 429 and "exports are already waiting" in full.json()["detail"]
    assert client.get(f"/companion/jobs/{waiting[0]}").json()["status"] == "queued"
    assert client.delete(f"/companion/jobs/{waiting[0]}").json() == {"deleted": True}
    assert client.get(f"/companion/jobs/{waiting[0]}").status_code == 404
    queue = jobs.job_queue(app)
    assert not queue.knows(waiting[0]) and queue.knows(waiting[1])
    assert client.delete(f"/companion/jobs/{first}").status_code == 200       # cancels the running one
    release.set()
    deadline = time.monotonic() + 5
    while queue.knows(waiting[-1]) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ran == [first, *waiting[1:]]                    # in order, and never the deleted one


def test_a_job_lost_in_a_restart_says_so(world):
    _fake, _app, client, tmp_path = world
    with sqlite3.connect(tmp_path / "companion.db") as db:
        db.execute("INSERT INTO cut_jobs (id, user_id, plan_id, status, created_at, updated_at) "
                   "VALUES ('5b0e7c7a-1d2e-4f3a-9b8c-7d6e5f4a3b2c', 'default', 'p', 'running', 'x', 'x')")
    job = client.get("/companion/jobs/5b0e7c7a-1d2e-4f3a-9b8c-7d6e5f4a3b2c").json()
    assert job["status"] == "failed" and job["error"] == jobs.RESTARTED


# ── the audio cache ─────────────────────────────────────────────────────────


def test_eviction_never_deletes_audio_a_queued_job_needs(tmp_path):
    folders = cuts.Folders(tmp_path, tmp_path / "cache", tmp_path / "cuts", cache_max_bytes=3000)
    folders.cache.mkdir()
    db = sqlite3.connect(tmp_path / "c.db")
    db.row_factory = sqlite3.Row
    from companion.db import migrate
    migrate(tmp_path / "c.db")
    for age, name in enumerate(["needed", "old", "new"]):          # "needed" is the oldest file
        path = folders.cache / f"{name}.mp3"
        path.write_bytes(b"x" * 1000)
        os.utime(path, (1_000_000 + age, 1_000_000 + age))
        db.execute("INSERT INTO cut_media VALUES (?,?,?,?,?,?,?)", (name, path.name, "podfetch", "v", "{}", "{}", "t"))
    db.commit()
    assert cuts.make_room(folders, 1500, {"needed"}, db) == ["old", "new"]
    assert [p.name for p in folders.cache.iterdir()] == ["needed.mp3"]
    assert [r[0] for r in db.execute("SELECT episode_id FROM cut_media")] == ["needed"]
    with pytest.raises(cuts.CacheFull, match="CACHE_MAX_MB"):
        cuts.make_room(folders, 2500, {"needed"}, db)
    assert (folders.cache / "needed.mp3").exists()


def test_the_queue_reports_every_episode_its_jobs_still_need(tmp_path):
    queue = jobs.JobQueue()
    folders = cuts.Folders(tmp_path, tmp_path / "cache", tmp_path / "cuts", 1)

    def job(job_id, *episodes):
        return jobs.RenderJob(job_id, "default", "p", frozenset(episodes), None, None, folders, tmp_path / "db", None)
    queue._waiting.extend([job("a", "ep-1", "ep-2"), job("b", "ep-3")])
    queue._running = job("c", "ep-4")
    assert queue.needed() == {"ep-1", "ep-2", "ep-3", "ep-4"}
    assert queue.cancel("b") and queue.needed() == {"ep-1", "ep-2", "ep-4"}


def test_cache_limit_comes_from_cache_max_mb(monkeypatch):
    monkeypatch.setenv("CACHE_MAX_MB", "250")
    assert cuts.cache_max_bytes() == 250 * cuts.MB
    monkeypatch.setenv("CACHE_MAX_MB", "nonsense")
    assert cuts.cache_max_bytes() == cuts.DEFAULT_CACHE_MB * cuts.MB


def test_podfetch_file_links_are_only_ever_fetched_from_the_configured_podfetch():
    podfetch = type("P", (), {"base_url": "http://podfetch:8000"})()
    link = {"local_url": "https://podcasts.example.com/podcasts/Show/ep%201/podcast.mp3?apiKey=k"}
    assert jobs.podfetch_file_url(podfetch, link) == "http://podfetch:8000/podcasts/Show/ep%201/podcast.mp3?apiKey=k"
    for bad in ("http://podfetch:8000/proxy/podcast?episodeId=1", "http://x/podcasts/../db/podcast.db", ""):
        assert jobs.podfetch_file_url(podfetch, {"local_url": bad}) is None


def test_storage_reports_cache_and_cut_sizes(world):
    _fake, _app, client, tmp_path = world
    (tmp_path / "audio-cache").mkdir()
    (tmp_path / "audio-cache" / f"{EP}.mp3").write_bytes(b"x" * 300)
    (tmp_path / "cuts").mkdir()
    (tmp_path / "cuts" / "c.mp3").write_bytes(b"y" * 200)
    (tmp_path / "cuts" / "c.json").write_bytes(b"{}")
    sizes = client.get("/companion/storage").json()
    assert (sizes["cache_bytes"], sizes["cache_files"], sizes["cuts_bytes"], sizes["cuts"]) == (300, 1, 202, 1)
    assert sizes["cache_max_bytes"] == cuts.DEFAULT_CACHE_MB * cuts.MB and sizes["free_bytes"] > 0


def test_plans_and_cuts_belong_to_their_user(world):
    fake, app, client, _ = world
    fake.add()
    made = plan(client)
    from companion.deps import current_user
    app.dependency_overrides[current_user] = lambda: "someone-else"
    assert client.get(f"/companion/plans/{made['id']}").status_code == 404
    assert client.post(f"/companion/plans/{made['id']}/render").status_code == 404
    assert client.patch(f"/companion/plans/{made['id']}", json={"context_seconds": 5}).status_code == 404


def test_spot_checks_use_the_ai_providers_speech_to_text_when_it_has_one():
    assert cuts.get_speech(None) is None
    assert cuts.get_speech(FakeLLM()) is None                   # a provider without /audio/transcriptions
    speech = FakeSpeech(said=[0, 0, 0], at=[0, 0, 0])
    assert cuts.get_speech(speech) is speech
