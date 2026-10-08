"""Worth hearing: settings, the daily cap across a restart, the scheduler, the page and its feed.

PodFetch is an httpx.MockTransport and transcripts come from a fake loader, so nothing here touches
a network or a real AI provider (FakeLLM). The scheduler tests use a near-zero interval and bounded
polling, never a blind sleep — see autobrief.Scheduler's own docstring for why that's safe."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import autobrief
from companion import brief as briefs
from companion import feeds
from companion.deps import get_transcripts
from companion.llm import FakeLLM, get_llm
from companion.server import create_app

SHOW = "show-1"
OTHER_SHOW = "show-2"
ALICE = "Basic YWxpY2U6YWxpY2UtcGFzc3dvcmQ="   # a caller with a real login, matching routes/test_feed.py


def eid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


E1, E2, E3, E4 = eid(1), eid(2), eid(3), eid(4)


def episode(episode_id: str, name: str, date: str, podcast_id: str = SHOW, total_time: int = 600) -> dict:
    return {"id": episode_id, "episode_id": episode_id, "podcast_id": podcast_id, "name": name,
            "date_of_recording": date, "total_time": total_time, "url": f"https://example.test/{episode_id}.mp3"}


class Library:
    """A fake PodFetch: a couple of shows with a few episodes, all with transcripts unless listed
    in ``no_transcript``. ``login`` matches the two shapes companion/routes/test_feed.py already
    uses for auth.authenticator(...).checks_logins(): with it True, a caller with no login (the
    background check's and the feed's PodFetch client both send none) gets a 401 from
    ``/api/v1/users/me``, exactly like a real PodFetch with BASIC_AUTH/OIDC on; a caller sending
    ALICE's own header still resolves, for routes that need a login themselves."""

    def __init__(self, *, login: bool = False):
        self.episodes = {E1: episode(E1, "Newest", "2026-09-20"), E2: episode(E2, "Middle", "2026-09-10"),
                         E3: episode(E3, "Oldest", "2026-09-01")}
        self.no_transcript: set[str] = set()
        self.login = login
        self.calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/api/v1/users/me":
            if self.login and request.headers.get("authorization") != ALICE:
                return httpx.Response(401)
            username = "alice" if self.login else "user123"
            return httpx.Response(200, json={"username": username})
        rest = [p for p in path.split("/") if p][2:]   # drop 'api', 'v1'
        if len(rest) == 2 and rest[0] == "episodes":
            found = self.episodes.get(rest[1])
            return httpx.Response(200, json={"podcastEpisode": found}) if found else httpx.Response(404)
        if len(rest) == 4 and rest[:2] == ["podcasts", "episodes"] and rest[3] == "chapters":
            return httpx.Response(200, json=[])
        if len(rest) == 3 and rest[0] == "podcasts" and rest[2] == "episodes":
            items = [{"podcastEpisode": e, "podcastHistoryItem": None}
                     for e in self.episodes.values() if e["podcast_id"] == rest[1]]
            return httpx.Response(200, json=items)
        if len(rest) == 2 and rest[0] == "podcasts":
            return httpx.Response(200, json={"id": rest[1], "name": "A show"})
        return httpx.Response(404)

    def transcripts(self, episode_id: str, episode: dict | None = None) -> dict:
        if episode_id in self.no_transcript:
            return {"episode_id": episode_id, "source": None, "timed": False, "text": "", "segments": []}
        texts = ["This is the reasoning behind the design.", "Here is a story about what went wrong.",
                 "A wrap-up of what was covered."]
        segments = [{"id": f"s{n}", "start": n * 10.0, "end": (n + 1) * 10.0, "text": t} for n, t in enumerate(texts)]
        return {"episode_id": episode_id, "source": "feed", "timed": True, "text": " ".join(texts), "segments": segments}


def answer(verdict: str = "HEAR") -> dict:
    return {"summary": "A short summary of the episode.", "verdict": verdict, "verdict_reason": "Because reasons.",
            "who_for": "Everyone.", "chapters": [{"title": "Start", "start_segment_id": "1"}],
            "key_ideas": [{"text": "The main idea.", "segment_ids": ["1"]}]}


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "AUTOBRIEF_INTERVAL_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def fixture(tmp_path: Path, db_name: str = "notes.db"):
    library = Library()
    app = create_app("http://podfetch.test", tmp_path / db_name, transport=httpx.MockTransport(library.handle))
    app.dependency_overrides[get_transcripts] = lambda: library.transcripts
    return app, library


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- settings


def test_settings_default_off_and_round_trip(tmp_path):
    app, _ = fixture(tmp_path)
    client = TestClient(app)
    assert client.get("/companion/settings/worth-hearing").json() == {
        "enabled": False, "show_ids": [], "daily_cap": autobrief.DEFAULT_DAILY_CAP, "updated_at": None,
        "login_blocks_auto": False}
    saved = client.put("/companion/settings/worth-hearing",
                       json={"enabled": True, "show_ids": [SHOW, SHOW], "daily_cap": 3})
    assert saved.status_code == 200
    body = saved.json()
    assert body["enabled"] is True and body["show_ids"] == [SHOW] and body["daily_cap"] == 3
    assert body["updated_at"]
    assert client.get("/companion/settings/worth-hearing").json()["daily_cap"] == 3


def test_settings_rejects_a_bad_daily_cap(tmp_path):
    app, _ = fixture(tmp_path)
    client = TestClient(app)
    for bad in (0, -1, autobrief.MAX_DAILY_CAP + 1, "5", True):
        response = client.put("/companion/settings/worth-hearing", json={"enabled": True, "show_ids": [], "daily_cap": bad})
        assert response.status_code == 422, bad


def test_estimate_before_saving_never_calls_ai_and_respects_the_cap(tmp_path):
    app, library = fixture(tmp_path)
    app.dependency_overrides[get_llm] = lambda: FakeLLM([])   # any AI call here would raise
    client = TestClient(app)
    response = client.post("/companion/settings/worth-hearing/estimate",
                           json={"enabled": True, "show_ids": [SHOW], "daily_cap": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2               # capped at daily_cap even though 3 episodes exist
    assert body["input_tokens"] > 0


# ---------------------------------------------------------------- the cap and "already seen"


def test_check_user_briefs_up_to_the_cap_and_marks_seen(tmp_path):
    app, library = fixture(tmp_path)
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=2)
    llm = FakeLLM([answer("HEAR"), answer("READ")])
    result = autobrief.check_user(app, llm, "default", now=NOW, transcripts=library.transcripts)
    assert result["status"] == "ok"
    assert [b["episode_id"] for b in result["briefed"]] == [E1, E2]   # newest first, cap = 2
    with briefs.connection(app.state.settings.database) as db:
        seen = {row[0] for row in db.execute("SELECT episode_id FROM autobrief_seen WHERE user_id='default'")}
        assert seen == {E1, E2}
        assert autobrief._remaining_today(db, "default", 2, "2026-09-21") == 0


def test_off_or_no_shows_does_nothing(tmp_path):
    app, library = fixture(tmp_path)
    llm = FakeLLM([])   # any AI call would raise
    assert autobrief.check_user(app, llm, "default", now=NOW, transcripts=library.transcripts)["status"] == "off"
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[], daily_cap=5)
    assert autobrief.check_user(app, llm, "default", now=NOW, transcripts=library.transcripts)["status"] == "off"


def test_run_once_does_nothing_without_an_ai_provider(tmp_path):
    app, library = fixture(tmp_path)
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=5)
    result = autobrief.run_once(app, now=NOW, transcripts=library.transcripts)
    assert result == {"status": "no_llm", "users": []}
    with briefs.connection(app.state.settings.database) as db:
        assert db.execute("SELECT COUNT(*) FROM autobrief_seen").fetchone()[0] == 0


def test_no_transcript_is_skipped_without_spending_the_cap(tmp_path):
    app, library = fixture(tmp_path)
    library.no_transcript.add(E1)   # the newest episode has no transcript yet
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=2)
    llm = FakeLLM([answer("HEAR"), answer("SKIP")])
    result = autobrief.check_user(app, llm, "default", now=NOW, transcripts=library.transcripts)
    # E1 has no transcript (skipped, free); E2 and E3 are briefed, using both slots of the cap.
    assert [b["episode_id"] for b in result["briefed"]] == [E2, E3]
    with briefs.connection(app.state.settings.database) as db:
        seen = {row[0] for row in db.execute("SELECT episode_id FROM autobrief_seen")}
        assert seen == {E2, E3}   # E1 not marked seen: it can be tried again once it has a transcript


def test_cap_holds_across_a_restart(tmp_path):
    app, library = fixture(tmp_path, db_name="restart.db")
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=2)
    first = autobrief.check_user(app, FakeLLM([answer("HEAR"), answer("READ")]), "default",
                                 now=NOW, transcripts=library.transcripts)
    assert len(first["briefed"]) == 2

    # A fresh app over the same database file, as if the process restarted.
    restarted = create_app("http://podfetch.test", tmp_path / "restart.db", transport=httpx.MockTransport(library.handle))
    again = autobrief.check_user(restarted, FakeLLM([]), "default", now=NOW, transcripts=library.transcripts)
    assert again["status"] == "capped"
    assert again["briefed"] == []

    # The next day the cap resets: the one remaining episode (E3) gets briefed.
    tomorrow = autobrief.check_user(restarted, FakeLLM([answer("SKIP")]), "default",
                                    now=NEXT_DAY, transcripts=library.transcripts)
    assert [b["episode_id"] for b in tomorrow["briefed"]] == [E3]


# ---------------------------------------------------------------- PodFetch login on: honest, not silent


def test_login_on_blocks_the_background_check_before_any_data_call(tmp_path):
    """Conductor fix, 2026-09-26: PodFetch's /api/v1 routes take no API key at all, so the
    background check (no session of its own) can't read anything once login is on. It must say so
    and never even try -- not silently do nothing after spending a call on real data."""
    app, library = fixture(tmp_path)
    library.login = True
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=5)
    llm = FakeLLM([])   # any AI call would raise
    result = autobrief.check_user(app, llm, "default", now=NOW, transcripts=library.transcripts)
    assert result == {"user": "default", "status": "login_required", "briefed": []}
    assert library.calls == ["/api/v1/users/me"]   # only the login probe itself; no episode/show data read

    once = autobrief.run_once(app, now=NOW, transcripts=library.transcripts)
    assert once == {"status": "login_required", "users": []}
    assert library.calls == ["/api/v1/users/me"]   # cached for 60s: the probe itself isn't repeated either
    with briefs.connection(app.state.settings.database) as db:
        assert db.execute("SELECT COUNT(*) FROM autobrief_seen").fetchone()[0] == 0


def test_settings_and_page_expose_login_blocks_auto(tmp_path):
    app, _ = fixture(tmp_path)
    library_on = Library(login=True)
    on_app = create_app("http://podfetch.test", tmp_path / "on.db", transport=httpx.MockTransport(library_on.handle))
    off_client, on_client = TestClient(app), TestClient(on_app)
    auth_header = {"Authorization": ALICE}   # these routes need a login themselves once PodFetch has one
    assert off_client.get("/companion/settings/worth-hearing").json()["login_blocks_auto"] is False
    assert on_client.get("/companion/settings/worth-hearing", headers=auth_header).json()["login_blocks_auto"] is True
    assert off_client.get("/companion/worth-hearing").json()["login_blocks_auto"] is False
    assert on_client.get("/companion/worth-hearing", headers=auth_header).json()["login_blocks_auto"] is True
    saved = on_client.put("/companion/settings/worth-hearing", headers=auth_header,
                          json={"enabled": True, "show_ids": [SHOW], "daily_cap": 3})
    assert saved.json()["login_blocks_auto"] is True   # saving is still allowed; it just stays inert


def test_feed_is_valid_and_empty_and_explained_when_login_is_on(tmp_path):
    """PodFetch's login is turned on after some episodes were already briefed: the feed must not
    leak that old data, and once turned on (a fresh app instance: auth.Authenticator's own 60 s
    login-mode cache is real PodFetch behaviour this feature already relies on, not something to fight in
    a test), the same install answers with a valid, empty, explained feed."""
    app, library = fixture(tmp_path, db_name="feed-login.db")   # login off while data is briefed
    brief_up(app, library, {E1: "HEAR"})
    with briefs.connection(app.state.settings.database) as db:
        token = feeds.create_token(db, feeds.data_folder(app.state.settings.database), "default", "http://podfetch.test", None)

    library_on = Library(login=True)
    on_app = create_app("http://podfetch.test", tmp_path / "feed-login.db", transport=httpx.MockTransport(library_on.handle))
    response = TestClient(on_app).get(f"/companion/feed/{token}/worth-hearing.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(response.content)
    assert root.find("channel").findall("item") == []
    assert root.find("channel/description").text == autobrief.LOGIN_BLOCKS_AUTO
    assert b"Newest" not in response.content   # the HEAR item briefed before login went on is never served
    assert f"/api/v1/episodes/{E1}" not in library_on.calls   # no episode data read once login blocks it


# ---------------------------------------------------------------- the page and the feed


def brief_up(app, library, verdicts: dict[str, str]) -> None:
    with briefs.connection(app.state.settings.database) as db:
        autobrief.save_settings(db, "default", enabled=True, show_ids=[SHOW], daily_cap=len(verdicts))
    autobrief.check_user(app, FakeLLM([answer(v) for v in verdicts.values()]), "default",
                         now=NOW, transcripts=library.transcripts)


def test_worth_hearing_page_lists_hear_and_collapses_the_rest(tmp_path):
    app, library = fixture(tmp_path)
    brief_up(app, library, {E1: "HEAR", E2: "SKIP", E3: "READ"})
    client = TestClient(app)
    body = client.get("/companion/worth-hearing").json()
    assert [item["episode_id"] for item in body["hear"]] == [E1]
    assert {item["episode_id"] for item in body["other"]} == {E2, E3}
    assert body["hear"][0]["title"] == "Newest"
    assert body["hear"][0]["summary"]


def test_feed_route_uses_p15s_real_token_check(tmp_path):
    """The feed opens with the private-feed token (feeds.user_for_token), the
    same one that opens the show and cuts feeds."""
    app, library = fixture(tmp_path)
    brief_up(app, library, {E1: "HEAR"})
    with briefs.connection(app.state.settings.database) as db:
        token = feeds.create_token(db, feeds.data_folder(app.state.settings.database), "default", "http://podfetch.test", None)
    client = TestClient(app)
    ok = client.get(f"/companion/feed/{token}/worth-hearing.xml")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/rss+xml")
    assert b"Newest" in ok.content
    bad = client.get("/companion/feed/not-a-real-token/worth-hearing.xml")
    assert bad.status_code == 404


def test_render_feed_is_valid_rss_with_only_hear_episodes():
    items = [{"episode_id": E1, "title": "Newest", "summary": "A summary.", "verdict_reason": "Why.",
             "enclosure_url": "https://example.test/1.mp3"}]
    body = autobrief.render_feed(items)
    root = ET.fromstring(body)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    entries = channel.findall("item")
    assert len(entries) == 1
    assert entries[0].find("title").text == "Newest"
    assert "A summary." in (entries[0].find("description").text or "")
    enclosure = entries[0].find("enclosure")
    assert enclosure is not None and enclosure.get("url") == "https://example.test/1.mp3"
    assert entries[0].find("guid").text == E1


# ---------------------------------------------------------------- the scheduler


def test_scheduler_ticks_on_its_interval_and_stops_promptly():
    calls: list[float] = []
    scheduler = autobrief.Scheduler(lambda: calls.append(time.monotonic()), interval_seconds=0.02)
    scheduler.start()
    try:
        deadline = time.monotonic() + 2.0
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(calls) >= 3
    finally:
        started_stop = time.monotonic()
        scheduler.stop()
        assert time.monotonic() - started_stop < 1.0   # stop() wakes the wait; it doesn't wait it out
    assert scheduler.is_running() is False


def test_scheduler_start_and_stop_are_idempotent():
    calls = []
    scheduler = autobrief.Scheduler(lambda: calls.append(1), interval_seconds=0.02)
    scheduler.start()
    scheduler.start()   # a second start is a no-op, not a second thread
    scheduler.stop()
    scheduler.stop()    # a second stop is a no-op, not an error
    assert scheduler.is_running() is False


def test_router_lifespan_starts_and_stops_the_scheduler_with_the_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOBRIEF_INTERVAL_SECONDS", "0.05")
    app, _ = fixture(tmp_path)   # no AI configured: every tick is a fast, free no-op
    assert getattr(app.state, "autobrief_scheduler", None) is None   # not started by create_app() alone
    with TestClient(app) as client:
        client.get("/companion/settings/worth-hearing")
        scheduler = app.state.autobrief_scheduler
        assert scheduler is not None and scheduler.is_running() is True
    assert app.state.autobrief_scheduler is None
    assert scheduler.is_running() is False
