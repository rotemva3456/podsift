""""Worth hearing": new episodes of the shows you watch, briefed for you.

An hourly background check (``Scheduler``, started with the app) looks at every user who has
turned the setting on, finds episodes of their watched shows that autobrief hasn't seen yet
(``autobrief_seen``), and briefs them one at a time through ``brief.generate_brief`` — never more
than that user's daily cap (``autobrief_settings``), and never when no AI provider is configured.
Both tables persist, so the cap and "already seen" state survive a restart.

An episode with no transcript yet is skipped without being marked seen (free to retry later,
never counts against the cap) so a few un-transcribed episodes can't block real progress within
one check.

For routes (``routes/worth.py``):
    read_settings / save_settings      the per-user setting (off by default)
    estimate_settings(ctx, user, ...)  input tokens a save would send today, before it is saved
    worth_items(db, podfetch, user)    what autobrief has briefed, newest first, with a HEAR verdict
                                       (or not) — the "Worth hearing" page and its feed both read this
    render_feed(items)                 the HEAR list as an RSS 2.0 feed
    run_once(app, now=...)             one check, for tests and for a manual real run
    start(app) / stop(app)             the background scheduler, tied to the app's lifespan
    login_blocks_auto(app)             True when PodFetch needs a login (see below)

The background task uses the install's own AI provider (Settings → AI) and an unauthenticated
PodFetch client, exactly like ``brief.open_context``'s defaults: it has no browser session to sign
in with. PodFetch's ``/api/v1`` routes take no API key at all (only its RSS, proxy and
transcript-file routes do — ``crates/podfetch-web/src/startup.rs``, ``auth_middleware.rs``), so on
an install with PodFetch login on there is no way for the background check, or the public feed, to
read anything -- ``login_blocks_auto`` (the same ``auth.authenticator(app).checks_logins()``
already reads) says so up front, and every caller here is honest about it instead of showing a
silent, permanently-empty result: the setting can't be turned on, the page explains why instead of
looking broken, and the feed stays a valid, empty, explained feed.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException

from . import auth
from . import brief as briefs
from .db import register_migration
from .deps import PodFetch
from .llm import LLM, get_llm

log = logging.getLogger(__name__)

DEFAULT_DAILY_CAP = 5
MAX_DAILY_CAP = 50
DEFAULT_INTERVAL_SECONDS = 3600.0
CANDIDATES_SCAN_MAX = 50    # episodes scanned per user per tick: enough backlog progress, bounded cost
WORTH_LIST_MAX = 100        # items the "Worth hearing" page and feed carry, newest first

LOGIN_BLOCKS_AUTO = ("Automatic briefs need PodFetch without a login: the app can't sign in by "
                     "itself in the background. You can still brief episodes one by one.")


def login_blocks_auto(app: FastAPI) -> bool:
    """True when PodFetch needs a login. The background check and the public feed have no
    session of their own to sign in with, and PodFetch's ``/api/v1`` routes accept no API key, so
    automatic briefs can never see anything on such an install -- callers use this to say so
    instead of quietly doing nothing."""
    return auth.authenticator(app).checks_logins()


register_migration("autobrief", 1, """
CREATE TABLE IF NOT EXISTS autobrief_settings (
    user_id TEXT NOT NULL DEFAULT 'default' PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    show_ids TEXT NOT NULL DEFAULT '[]',
    daily_cap INTEGER NOT NULL DEFAULT 5,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS autobrief_seen (
    user_id TEXT NOT NULL DEFAULT 'default',
    episode_id TEXT NOT NULL,
    briefed_on TEXT NOT NULL,
    verdict TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, episode_id));
CREATE INDEX IF NOT EXISTS autobrief_seen_by_day ON autobrief_seen (user_id, briefed_on)
""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _today(now: datetime) -> str:
    return now.date().isoformat()


# ---------------------------------------------------------------- settings


@dataclass
class Settings:
    enabled: bool
    show_ids: list[str]
    daily_cap: int
    updated_at: str | None


def _clean_show_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise HTTPException(422, "Pick shows to watch from your library.")
    ids = [str(v) for v in value if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip()]
    if len(ids) > 200:
        raise HTTPException(422, "That's too many shows to watch at once.")
    return list(dict.fromkeys(ids))


def _clean_daily_cap(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_DAILY_CAP:
        raise HTTPException(422, f"The daily cap must be a whole number from 1 to {MAX_DAILY_CAP}.")
    return value


def read_settings(db: sqlite3.Connection, user: str) -> Settings:
    row = db.execute("SELECT enabled, show_ids, daily_cap, updated_at FROM autobrief_settings WHERE user_id=?",
                     (user,)).fetchone()
    if row is None:
        return Settings(False, [], DEFAULT_DAILY_CAP, None)
    try:
        parsed = json.loads(row["show_ids"])
        show_ids = [str(s) for s in parsed] if isinstance(parsed, list) else []
    except ValueError:
        show_ids = []
    cap = row["daily_cap"] if isinstance(row["daily_cap"], int) and 1 <= row["daily_cap"] <= MAX_DAILY_CAP else DEFAULT_DAILY_CAP
    return Settings(bool(row["enabled"]), show_ids, cap, row["updated_at"])


def save_settings(db: sqlite3.Connection, user: str, *, enabled: Any, show_ids: Any, daily_cap: Any) -> Settings:
    ids, cap = _clean_show_ids(show_ids), _clean_daily_cap(daily_cap)
    db.execute("""INSERT INTO autobrief_settings (user_id, enabled, show_ids, daily_cap, updated_at)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT (user_id) DO UPDATE SET enabled=excluded.enabled, show_ids=excluded.show_ids,
                  daily_cap=excluded.daily_cap, updated_at=excluded.updated_at""",
              (user, int(bool(enabled)), json.dumps(ids), cap, _now()))
    return read_settings(db, user)


def public_settings(settings: Settings) -> dict[str, Any]:
    return {"enabled": settings.enabled, "show_ids": settings.show_ids, "daily_cap": settings.daily_cap,
            "updated_at": settings.updated_at}


def enabled_users(db: sqlite3.Connection) -> list[str]:
    return [row[0] for row in db.execute("SELECT user_id FROM autobrief_settings WHERE enabled=1")]


# ---------------------------------------------------------------- candidates and the cap


def _episode_date(episode: Mapping[str, Any]) -> str:
    return str(episode.get("date_of_recording") or "")


def candidates(ctx: briefs.BriefContext, show_ids: Sequence[str], seen: set[str], limit: int) -> list[dict[str, Any]]:
    """Episodes of the watched shows that autobrief hasn't seen yet, newest first, at most ``limit``."""
    found: dict[str, dict[str, Any]] = {}
    for show_id in show_ids:
        for item in briefs.show_episodes(ctx.podfetch, show_id) or ():
            episode = item.get("podcastEpisode") if isinstance(item, Mapping) else None
            if not isinstance(episode, Mapping):
                continue
            episode_id = str(episode.get("episode_id") or "")
            if episode_id and episode_id not in seen:
                found[episode_id] = episode
    ordered = sorted(found.values(), key=_episode_date, reverse=True)
    return ordered[:limit]


def _seen_ids(db: sqlite3.Connection, user: str) -> set[str]:
    return {row[0] for row in db.execute("SELECT episode_id FROM autobrief_seen WHERE user_id=?", (user,))}


def _remaining_today(db: sqlite3.Connection, user: str, cap: int, today: str) -> int:
    used = db.execute("SELECT COUNT(*) FROM autobrief_seen WHERE user_id=? AND briefed_on=?",
                      (user, today)).fetchone()[0]
    return max(0, cap - used)


def _mark_seen(db: sqlite3.Connection, user: str, episode_id: str, today: str, verdict: str | None) -> None:
    db.execute("""INSERT INTO autobrief_seen (user_id, episode_id, briefed_on, verdict, created_at)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT (user_id, episode_id) DO UPDATE SET
                  briefed_on=excluded.briefed_on, verdict=excluded.verdict, created_at=excluded.created_at""",
              (user, episode_id, today, verdict, _now()))


def check_user(app: FastAPI, llm: LLM, user: str, *, now: datetime,
               transcripts: briefs.TranscriptLoader | None = None) -> dict[str, Any]:
    """One user's check: brief new episodes of their watched shows, up to today's remaining cap.

    Does nothing (and touches no data, and makes no PodFetch call) when PodFetch needs a login the
    background has no session for, the setting is off, no show is watched, or today's cap is
    already used. An episode with no transcript yet is skipped without spending the cap.
    ``transcripts`` overrides the app's own loader (tests; see ``brief.open_context``)."""
    if login_blocks_auto(app):
        return {"user": user, "status": "login_required", "briefed": []}
    today = _today(now)
    with briefs.open_context(app, llm=llm, transcripts=transcripts) as ctx:
        settings = read_settings(ctx.db, user)
        if not settings.enabled or not settings.show_ids:
            return {"user": user, "status": "off", "briefed": []}
        remaining = _remaining_today(ctx.db, user, settings.daily_cap, today)
        if remaining <= 0:
            return {"user": user, "status": "capped", "briefed": []}
        pool = candidates(ctx, settings.show_ids, _seen_ids(ctx.db, user), CANDIDATES_SCAN_MAX)
        briefed: list[dict[str, Any]] = []
        for episode in pool:
            if remaining <= 0:
                break
            episode_id = str(episode.get("episode_id") or "")
            if not episode_id:
                continue
            try:
                result = briefs.generate_brief(episode_id, user, ctx)
            except HTTPException as exc:
                log.warning("autobrief: episode %s couldn't be briefed (%s)", episode_id, exc.detail)
                continue
            if result["status"] == "no_transcript":
                continue    # cheap to check again next time; doesn't touch the cap
            _mark_seen(ctx.db, user, episode_id, today, result.get("verdict"))
            remaining -= 1
            briefed.append({"episode_id": episode_id, "status": result["status"], "verdict": result.get("verdict")})
        return {"user": user, "status": "ok", "briefed": briefed}


def run_once(app: FastAPI, *, now: datetime | None = None,
            transcripts: briefs.TranscriptLoader | None = None) -> dict[str, Any]:
    """One check across every user with the setting on. Safe to call directly (tests; a real run
    with a shortened check interval); the scheduler below just
    calls this on a timer. Does nothing when PodFetch needs a login (never touches PodFetch or the
    database in that case) or no AI provider is configured."""
    if login_blocks_auto(app):
        return {"status": "login_required", "users": []}
    now = now or datetime.now(timezone.utc)
    llm = get_llm(SimpleNamespace(app=app))  # no request: only the saved/.env provider, like brief.open_context
    if llm is None:
        return {"status": "no_llm", "users": []}
    with briefs.connection(app.state.settings.database) as db:
        users = enabled_users(db)
    return {"status": "ok", "users": [check_user(app, llm, user, now=now, transcripts=transcripts) for user in users]}


# ---------------------------------------------------------------- the settings-page estimate


def estimate_settings(ctx: briefs.BriefContext, user: str, show_ids: Sequence[str], daily_cap: Any) -> dict[str, Any]:
    """What turning this on (or changing it) would send today: up to
    ``daily_cap`` new episodes across ``show_ids`` that autobrief hasn't seen yet. Shown before the
    setting is saved. Never calls AI (reuses ``brief.estimate``)."""
    ids = _clean_show_ids(show_ids)
    cap = _clean_daily_cap(daily_cap)
    pool = candidates(ctx, ids, _seen_ids(ctx.db, user), cap)
    episode_ids = [str(episode.get("episode_id")) for episode in pool if episode.get("episode_id")]
    return briefs.estimate(episode_ids, user, ctx)


# ---------------------------------------------------------------- the "Worth hearing" list and feed


def worth_items(db: sqlite3.Connection, podfetch: PodFetch, user: str) -> list[dict[str, Any]]:
    """What autobrief has briefed for this user, newest first: title, verdict, summary and "N% new
    to you" for the page and the feed. An episode no longer in the library,
    or whose brief failed or found no transcript, is left out."""
    rows = db.execute("SELECT episode_id, briefed_on FROM autobrief_seen WHERE user_id=? "
                      "ORDER BY briefed_on DESC, created_at DESC LIMIT ?", (user, WORTH_LIST_MAX)).fetchall()
    ids = [row["episode_id"] for row in rows]
    by_id = {b["episode_id"]: b for b in briefs.cached_briefs(db, user, ids)}
    items: list[dict[str, Any]] = []
    for row in rows:
        brief = by_id.get(row["episode_id"])
        if brief is None or brief.get("verdict") is None:
            continue
        try:
            episode = podfetch.episode(row["episode_id"])
        except HTTPException:
            continue
        items.append({"episode_id": row["episode_id"], "title": episode.get("name"), "verdict": brief["verdict"],
                      "summary": brief["summary"], "verdict_reason": brief["verdict_reason"],
                      "percent_new": brief["percent_new"], "duration": brief["duration"],
                      "briefed_on": row["briefed_on"], "enclosure_url": episode.get("url")})
    return items


def render_feed(items: Sequence[Mapping[str, Any]], *, note: str | None = None) -> bytes:
    """The HEAR list as an RSS 2.0 feed: a title, the summary and verdict
    reason as the description, and PodFetch's own enclosure for each episode. ``note`` replaces the
    channel description -- used for a valid, empty feed when PodFetch needs a login
    (``login_blocks_auto``), so a podcast app shows why instead of just an empty list forever."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Worth hearing"
    ET.SubElement(channel, "description").text = note or "New episodes worth your time, briefed for you."
    for item in items:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = str(item.get("title") or "Episode")
        summary = str(item.get("summary") or "").strip()
        reason = str(item.get("verdict_reason") or "").strip()
        description = "\n\n".join(part for part in (summary, reason) if part)
        if description:
            ET.SubElement(node, "description").text = description
        ET.SubElement(node, "guid", isPermaLink="false").text = str(item.get("episode_id") or "")
        url = str(item.get("enclosure_url") or "")
        if url:
            ET.SubElement(node, "enclosure", url=url, type="audio/mpeg", length="0")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode").encode("utf-8")


# ---------------------------------------------------------------- the background scheduler


class Scheduler:
    """Calls ``tick()`` once, then every ``interval_seconds``, until ``stop()``.

    ``stop()`` wakes the wait immediately (``Event.wait``, never a plain ``time.sleep``), so it
    returns as soon as the tick in progress (if any) finishes — tests never wait out a real
    interval. ``start``/``stop`` are each idempotent: a second ``start()`` is a no-op, and ``stop()``
    is safe to call more than once (both matter because ``worth.py``'s router lifespan can run more
    than once per process, once per app the tests create)."""

    def __init__(self, tick: Callable[[], None], interval_seconds: float):
        self._tick = tick
        self._interval = max(0.01, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="autobrief", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("autobrief check failed")
            if self._stop.wait(self._interval):
                return

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def start(app: FastAPI, *, interval_seconds: float | None = None) -> Scheduler:
    """Start the hourly check for this app; a second call is a no-op (returns the same scheduler).

    The interval defaults to ``AUTOBRIEF_INTERVAL_SECONDS`` (an hour when unset) so the real check
    in tests can shorten it without a code change."""
    existing = getattr(app.state, "autobrief_scheduler", None)
    if existing is not None:
        return existing
    if interval_seconds is None:
        interval_seconds = float(os.getenv("AUTOBRIEF_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS)
    scheduler = Scheduler(lambda: run_once(app), interval_seconds)
    app.state.autobrief_scheduler = scheduler
    scheduler.start()
    return scheduler


def stop(app: FastAPI) -> None:
    scheduler = getattr(app.state, "autobrief_scheduler", None)
    if scheduler is not None:
        scheduler.stop()
        app.state.autobrief_scheduler = None
