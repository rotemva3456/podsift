"""The tools' real work, kept separate from ``server.py`` so tests call these functions directly
with a fake :class:`~podcast_mcp.client.CompanionClient` instead of going through stdio.

Every function takes the client first and returns a plain ``dict`` (or raises
:class:`~podcast_mcp.client.PodcastError`, one sentence the agent can act on). ``server.py`` wires
these to the actual MCP tool signatures the agent calls.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from .client import CompanionClient, PodcastError
from .formatting import cap, clamp, clock, with_clocks

# PodFetch pages podcast episodes 75 at a time (usecases/podcast_episode/mod.rs), same constant
# companion/routes/search.py uses for the same endpoint.
PODFETCH_PAGE = 75

SHOWS_DEFAULT, SHOWS_MAX = 20, 50
EPISODES_DEFAULT, EPISODES_MAX = 20, 50
SEARCH_EPISODES_DEFAULT, SEARCH_EPISODES_MAX = 5, 20
SEARCH_HITS_DEFAULT, SEARCH_HITS_MAX = 3, 10
TRANSCRIPT_WINDOW_SECONDS = 300.0
TRANSCRIPT_CHARS_DEFAULT, TRANSCRIPT_CHARS_MAX = 4000, 20000
BRIEF_CHAPTERS_MAX = 60
BRIEF_IDEAS_DEFAULT, BRIEF_CITATIONS_DEFAULT = 8, 3
CUT_INDEX_DEFAULT, CUT_INDEX_MAX = 15, 60
PLAN_SPANS_MAX = 60
SPAN_TEXT_CHARS = 500
PLANS_DEFAULT, PLANS_MAX = 20, 50


def _short(text: Any, limit: int = SPAN_TEXT_CHARS) -> str | None:
    if not isinstance(text, str):
        return text
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# --------------------------------------------------------------------------------- search_episodes


async def search_episodes(client: CompanionClient, query: str, limit: int | None = None,
                          hits_per_episode: int | None = None) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise PodcastError('Say what you want to find, for example "BGP route selection".')
    data = await client.get("/companion/search", params={"q": query}) or {}
    episodes = data.get("episodes") or []
    shown, more_episodes = cap(episodes, clamp(limit, SEARCH_EPISODES_DEFAULT, SEARCH_EPISODES_MAX))
    hit_limit = clamp(hits_per_episode, SEARCH_HITS_DEFAULT, SEARCH_HITS_MAX)
    formatted = []
    for episode in shown:
        episode = dict(episode)
        hits, more_hits = cap(episode.get("hits") or [], hit_limit)
        episode["hits"] = [with_clocks(hit, "start", "end") for hit in hits]
        if more_hits:
            episode["more_hits"] = more_hits
        formatted.append(with_clocks(episode, "duration"))
    return {"query": data.get("query", query), "terms": data.get("terms", []),
            "library_connected": bool(data.get("library")), "unmatched": data.get("unmatched", 0),
            "episodes": formatted, "more_episodes": more_episodes}


# ----------------------------------------------------------------------------------- list_library


async def list_library(client: CompanionClient, podcast_id: str | None = None, cursor: str | None = None,
                       limit: int | None = None) -> dict[str, Any]:
    if podcast_id is None:
        raw = await client.get("/api/v1/podcasts") or []
        shows = [{"podcast_id": show.get("id"), "title": show.get("name"), "author": show.get("author")}
                 for show in raw if isinstance(show, dict)]
        shown, more_shows = cap(shows, clamp(limit, SHOWS_DEFAULT, SHOWS_MAX))
        return {"kind": "shows", "shows": shown, "more_shows": more_shows,
                "hint": "Call list_library with this podcast_id to list that show's episodes."}
    params = {"last_podcast_episode": cursor} if cursor else None
    raw = await client.get(f"/api/v1/podcasts/{podcast_id}/episodes", params=params) or []
    items = [item.get("podcastEpisode") for item in raw if isinstance(item, dict)]
    items = [item for item in items if isinstance(item, dict)]
    shown_raw, left_in_page = cap(items, clamp(limit, EPISODES_DEFAULT, EPISODES_MAX))
    episodes = [with_clocks({
        "episode_id": e.get("episode_id"), "id": e.get("id"), "podcast_id": e.get("podcast_id"),
        "title": e.get("name"), "duration": e.get("total_time"), "published": e.get("date_of_recording"),
        "downloaded": bool(e.get("status")),
    }, "duration") for e in shown_raw]
    next_cursor = None
    if left_in_page:
        next_cursor = shown_raw[-1].get("date_of_recording")
    elif len(raw) >= PODFETCH_PAGE and items:
        next_cursor = items[-1].get("date_of_recording")
    return {"kind": "episodes", "podcast_id": podcast_id, "episodes": episodes,
            "more": left_in_page, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------------- get_transcript


async def list_queue(client: CompanionClient, playlist_id: str | None = None,
                     cursor: int = 0, limit: int | None = None) -> dict[str, Any]:
    if cursor < 0:
        raise PodcastError("Use a nonnegative queue cursor.")
    playlists = await client.get("/api/v1/playlist") or []
    selected = next((p for p in playlists if str(p.get("id")) == playlist_id) if playlist_id
                    else (p for p in playlists if p.get("name") == "Listen next"), None)
    if playlist_id and selected is None:
        raise PodcastError("That library playlist no longer exists; call list_queue to list the available playlists.")
    items = [i["podcastEpisode"] for i in (selected or {}).get("items") or [] if i.get("podcastEpisode")]
    shown, more = cap(items[cursor:], clamp(limit, 20, 50))
    available, more_playlists = cap(playlists, 50)
    return {"playlist_id": selected.get("id") if selected else None,
            "name": selected.get("name") if selected else "Listen next",
            "playlists": [{"id": p.get("id"), "name": p.get("name"), "episodes": len(p.get("items") or [])}
                          for p in available], "more_playlists": more_playlists,
            "episodes": [with_clocks({"episode_id": e.get("episode_id"), "title": e.get("name"),
                          "duration": e.get("total_time"), "downloaded": bool(e.get("status"))}, "duration")
                         for e in shown],
            "total_episodes": len(items), "next_cursor": cursor + len(shown) if more else None}


def _overlaps(segment: dict[str, Any], window_start: float, window_end: float) -> bool:
    start = segment.get("start") or 0
    end = segment.get("end")
    return start < window_end and (end is None or end > window_start)


async def get_transcript(client: CompanionClient, episode_id: str, start: float | None = None,
                         end: float | None = None, max_chars: int | None = None,
                         cursor: int | None = None) -> dict[str, Any]:
    if cursor is not None and (cursor < 0 or start is not None or end is not None):
        raise PodcastError("Use a nonnegative cursor to read the whole transcript, or start/end for a time window.")
    data = await client.get(f"/companion/episodes/{episode_id}/transcript")
    if not isinstance(data, dict):
        raise PodcastError("The podcast app did not return a transcript.")
    budget = clamp(max_chars, TRANSCRIPT_CHARS_DEFAULT, TRANSCRIPT_CHARS_MAX, minimum=200)
    episode_id = data.get("episode_id", episode_id)
    common = {"episode_id": episode_id, "origin": data.get("origin"),
              "transcript_digest": data.get("digest")}
    if not data.get("timed"):
        text = data.get("text") or ""
        offset = cursor or 0
        next_cursor = offset + budget if offset + budget < len(text) else None
        note = None
        if start is not None or end is not None:
            note = "This transcript has no per-line timing, so start and end are ignored."
        return {**common, "timed": False, "text": text[offset:offset + budget],
                "more_text": max(0, len(text) - offset - budget), "note": note,
                "cursor": offset, "next_cursor": next_cursor, "complete": next_cursor is None,
                "total_chars": len(text)}
    segments = data.get("segments") or []
    window_start = 0.0 if start is None else max(0.0, float(start))
    window_end = window_start + TRANSCRIPT_WINDOW_SECONDS if end is None else max(window_start, float(end))
    picked = [(i, seg) for i, seg in enumerate(segments)
              if i >= cursor] if cursor is not None else [
                  (i, seg) for i, seg in enumerate(segments) if _overlaps(seg, window_start, window_end)]
    shown, chars, next_cursor = [], 0, None
    for idx, seg in picked:
        text_len = len(seg.get("text") or "")
        if shown and chars + text_len > budget:
            next_cursor = idx
            break
        shown.append(seg)
        chars += text_len
    else:
        if picked:
            last_idx = picked[-1][0]
            if last_idx + 1 < len(segments):
                next_cursor = last_idx + 1
        elif cursor is None:
            next_cursor = next((i for i, s in enumerate(segments)
                                if (s.get("start") or 0) >= window_end), None)
    next_start = segments[next_cursor].get("start") if next_cursor is not None else None
    return {**common, "timed": True,
            "window": with_clocks({"start": window_start, "end": window_end}, "start", "end")
                      if cursor is None else None,
            "segments": [with_clocks(seg, "start", "end") for seg in shown],
            "next_start": next_start, "next_start_clock": clock(next_start),
            "cursor": cursor, "next_cursor": next_cursor, "complete": next_cursor is None,
            "total_segments": len(segments)}


# --------------------------------------------------------------------------------------- get_brief


async def get_brief(client: CompanionClient, episode_id: str, generate: bool = False) -> dict[str, Any]:
    path = f"/companion/episodes/{episode_id}/brief"
    brief = await (client.post(path) if generate else client.get(path))
    if not isinstance(brief, dict):
        raise PodcastError("The podcast app did not return a brief.")
    out = with_clocks(dict(brief), "duration", "ads_seconds")
    chapters, more_chapters = cap(out.get("chapters") or [], BRIEF_CHAPTERS_MAX)
    out["chapters"] = [with_clocks(chapter, "start") for chapter in chapters]
    if more_chapters:
        out["more_chapters"] = more_chapters
    ideas, more_ideas = cap(out.get("key_ideas") or [], BRIEF_IDEAS_DEFAULT)
    formatted_ideas = []
    for idea in ideas:
        idea = dict(idea)
        citations, more_citations = cap(idea.get("citations") or [], BRIEF_CITATIONS_DEFAULT)
        idea["citations"] = [with_clocks(c, "start", "end") for c in citations]
        if more_citations:
            idea["more_citations"] = more_citations
        formatted_ideas.append(idea)
    out["key_ideas"] = formatted_ideas
    if more_ideas:
        out["more_key_ideas"] = more_ideas
    return out


# ---------------------------------------------------------------------------------------- plan_cut


async def plan_cut(client: CompanionClient, want: str, episode_ids: list[str] | None = None,
                   use_queue: bool = False, skip: str | None = None, minutes: float | None = None,
                   skip_ads: bool = True, mode: str = "keyword", learning_mode: str = "balanced") -> dict[str, Any]:
    if not (want or "").strip():
        raise PodcastError('Say what you want to hear, for example "BGP route selection".')
    if bool(episode_ids) == bool(use_queue):
        raise PodcastError("Give episode_ids, or set use_queue=true for your Listen next list "
                           "(not both, not neither).")
    body: dict[str, Any] = {"want": want, "skip_ads": skip_ads, "mode": mode}
    if learning_mode != "balanced":
        body["learning_mode"] = learning_mode
    if skip:
        body["skip"] = skip
    if minutes is not None:
        body["minutes"] = minutes
    if use_queue:
        body["source"] = "queue"
    else:
        body["episode_ids"] = list(episode_ids or [])
    plan = await client.post("/companion/plans", json=body)
    return _format_plan(plan)


def _format_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise PodcastError("The podcast app did not return a plan.")
    out = with_clocks(dict(plan), "kept_seconds", "source_seconds")
    spans, more_spans = cap(out.get("spans") or [], PLAN_SPANS_MAX)
    out["spans"] = [with_clocks({**span, "text": _short(span.get("text"))}, "start", "end") for span in spans]
    out["episodes"] = [with_clocks(dict(episode), "duration")
                       for episode in out.get("episodes") or []]
    out["omitted"] = [with_clocks(dict(item), "start", "end")
                      for item in out.get("omitted") or []]
    if more_spans:
        out["more_spans"] = more_spans
    out["next"] = ("Read get_plan_script(plan_id), then call render_cut(plan_id) to export it as one MP3."
                   if out.get("status") == "ready" else
                   "This plan has nothing ready to export yet; check needs_timing and omitted.")
    return out


async def list_plans(client: CompanionClient, episode_id: str | None = None,
                     limit: int | None = None, learning_mode: str | None = None) -> dict[str, Any]:
    """List stored plans while keeping their source episodes and human-readable clocks."""
    params: dict[str, Any] = {"limit": clamp(limit, PLANS_DEFAULT, PLANS_MAX)}
    if episode_id is not None:
        params["episode_id"] = episode_id
    if learning_mode is not None:
        params["learning_mode"] = learning_mode
    plans = await client.get("/companion/plans", params=params)
    if not isinstance(plans, list):
        raise PodcastError("The podcast app did not return saved plans.")
    return {"plans": [_format_plan(plan) for plan in plans], "count": len(plans)}


async def plan_from_segments(client: CompanionClient, want: str, selections: list[dict[str, Any]],
                             minutes: float | None = None, skip_ads: bool = True,
                             learning_mode: str = "balanced") -> dict[str, Any]:
    if not (want or "").strip() or not selections:
        raise PodcastError("Give a listening goal and selections from transcripts you have read.")
    body = {"want": want, "mode": "agent", "selections": selections,
            "episode_ids": [s["episode_id"] for s in selections], "skip_ads": skip_ads}
    if learning_mode != "balanced":
        body["learning_mode"] = learning_mode
    if minutes is not None:
        body["minutes"] = minutes
    return _format_plan(await client.post("/companion/plans", json=body))


async def get_plan_script(client: CompanionClient, plan_id: str, cursor: int = 0,
                          limit: int | None = None) -> dict[str, Any]:
    """Page kept/skipped sentences so even one long kept passage stays readable."""
    if cursor < 0:
        raise PodcastError("Use a nonnegative script cursor.")
    script = await client.get(f"/companion/plans/{plan_id}/script") or {}
    lines = [{"episode_id": ep["episode_id"], "title": ep.get("title"),
              "kind": part["kind"], "reason": part.get("reason"), "why": part.get("why"),
              "part_start": part["start"], "part_end": part["end"], **line}
             for ep in script.get("episodes") or [] for part in ep.get("parts") or []
             for line in part.get("lines") or [{"start": part["start"], "end": part["end"], "text": ""}]]
    shown, more = cap(lines[cursor:], clamp(limit, 20, 50))
    return with_clocks({"plan_id": plan_id,
            "learning_mode": script.get("learning_mode", "balanced"),
            "lines": [with_clocks(line, "start", "end", "part_start", "part_end") for line in shown],
            "total_lines": len(lines),
            "words_available": bool(script.get("words", lines)),
            "next_cursor": cursor + len(shown) if more else None, "complete": more == 0,
            "kept_seconds": script.get("kept_seconds"), "source_seconds": script.get("source_seconds")},
            "kept_seconds", "source_seconds")


async def discover_podcasts(client: CompanionClient, query: str, limit: int | None = None) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise PodcastError("Give a topic, host or podcast name to search for.")
    data = await client.get(f"/api/v1/podcasts/0/{quote(query, safe='')}/search") or {}
    found, more = cap(data.get("results") or [], clamp(limit, 10, 30))
    return {"query": query, "podcasts": [{"title": p.get("collectionName"), "author": p.get("artistName"),
            "feed_url": p.get("feedUrl"), "directory_id": p.get("collectionId") or p.get("trackId")}
            for p in found], "more_podcasts": more}


async def follow_podcast(client: CompanionClient, feed_url: str) -> dict[str, Any]:
    try:
        url = urlsplit(feed_url)
        valid = url.scheme in ("http", "https") and url.hostname and not url.username and not url.password
    except ValueError:
        valid = False
    if not valid:
        raise PodcastError("Give the podcast's HTTP or HTTPS RSS feed URL.")
    result = await client.post("/api/v1/podcasts/feed", json={"rssFeedUrl": feed_url})
    return {"feed_url": feed_url, "result": result,
            "next": "Call list_library to find the show and its episodes."}


async def prepare_episode(client: CompanionClient, episode_id: str, download: bool = False,
                          transcribe: bool = False) -> dict[str, Any]:
    raw = await client.get(f"/api/v1/episodes/{episode_id}") or {}
    episode = raw.get("podcastEpisode") or raw
    if not isinstance(episode, dict) or not episode.get("id"):
        raise PodcastError("The podcast app did not return this episode's details.")
    out = {"episode_id": episode_id, "title": episode.get("name"),
           "downloaded": bool(episode.get("status"))}
    if not out["downloaded"]:
        if transcribe:
            raise PodcastError("Download the episode first; then call prepare_episode with transcribe=true.")
        if download:
            await client.request("PUT", f"/api/v1/podcasts/{episode_id}/episodes/download")
        return {**out, "status": "downloading" if download else "not_downloaded",
                "next": "Call prepare_episode again to check the download; download=true starts it."}
    listed = await client.get(f"/api/v1/podcasts/episodes/{episode['id']}/transcripts") or []
    generated = [t for t in listed if t.get("source") == "generated"]
    pending = next((t for t in generated if t.get("status") in ("pending", "queued", "running", "fetching", "parsing")), None)
    ready = any(t.get("status") == "parsed" for t in generated)
    status = "transcription_running" if pending else "downloaded"
    if transcribe and not ready and not pending:
        await client.post(f"/api/v1/podcasts/episodes/{episode_id}/transcribe")
        status = "transcription_running"
    return {**out, "status": status, "transcripts": [{"id": t.get("id"), "source": t.get("source"),
            "status": t.get("status")} for t in listed],
            "next": "Call get_transcript(cursor=0); if transcription is running, check again later."}


# -------------------------------------------------------------------------------------- render_cut


async def render_cut(client: CompanionClient, plan_id: str) -> dict[str, Any]:
    result = await client.post(f"/companion/plans/{plan_id}/render")
    if not isinstance(result, dict) or "job_id" not in result:
        raise PodcastError("The podcast app did not return an export job.")
    return {"job_id": result["job_id"],
            "next": "Call job_status(job_id) until status is \"done\", then cut_link(cut_id)."}


# -------------------------------------------------------------------------------------- job_status


async def job_status(client: CompanionClient, job_id: str) -> dict[str, Any]:
    job = await client.get(f"/companion/jobs/{job_id}")
    if not isinstance(job, dict):
        raise PodcastError("The podcast app did not return this export's status.")
    out = dict(job)
    progress = out.get("progress")
    if isinstance(progress, (int, float)):
        out["progress_percent"] = round(progress * 100)
    if out.get("status") == "done" and out.get("cut_id"):
        out["next"] = "Call cut_link(cut_id) for the file link."
    return out


# --------------------------------------------------------------------------------------- cut_link


async def cut_link(client: CompanionClient, cut_id: str) -> dict[str, Any]:
    cut = await client.get(f"/companion/cuts/{cut_id}")
    if not isinstance(cut, dict):
        raise PodcastError("The podcast app did not return this cut.")
    out = with_clocks(dict(cut), "duration")
    size_bytes = out.get("size_bytes")
    if isinstance(size_bytes, (int, float)):
        out["size_mb"] = round(size_bytes / 1_000_000, 1)
    index, more_index = cap(out.get("index") or [], CUT_INDEX_DEFAULT)
    out["index"] = [with_clocks(item, "cut_start", "cut_end", "source_start", "source_end") for item in index]
    if more_index:
        out["more_index"] = more_index
    out["url"] = client.link(f"/companion/cuts/{cut_id}.mp3")
    return out
