# Connect your AI agent

This is the primary way to use Podsift. Your existing agent brings your learning goal,
books, notes and previous learning. The MCP server gives it podcast discovery, full
transcripts and original-audio cut tools. The agent can choose what to hear or skip
using its own model and memory; no second chat AI key or browser session is required.

The MCP server is a separate small program (`mcp/`) talking to the running podcast
service over HTTP. It never imports the app's code. Start the service with the
[Docker install](../docker/README.md), then connect the client below.

For an effort-based listen, use `learning_mode="focus"` for difficult or confusing
material when sharp, or `learning_mode="chill"` for recap and clear explanations.
`plan_from_segments` stores your own choices; `plan_cut` needs `mode="ai"` for these
preferences. `list_plans(learning_mode=...)` retrieves the matching saved listens.
Default `balanced` preserves any-effort listening. [Mode behavior](listening-effort.md).

**Renders need a downloaded episode.** Cuts, Smart Play and every stored time refer to the
episode's downloaded audio file, never a stream (a stream can carry different ads on every
request). Use `prepare_episode(download=true)` and poll until downloaded, or let
auto-download do it. Publisher transcripts are preferred for a first read when available.
If text is missing, `prepare_episode(transcribe=true)` requests a transcript after the
download completes. That uses the configured speech provider and may incur charges.
Both action flags are false by default.

## Install

The server has its own virtual environment, separate from the app's. From the repository root:

```bash
cd mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

That installs exactly the versions pinned in `pyproject.toml` (the official MCP Python SDK, and
`httpx`). Nothing here touches the app's own Python environment.

To confirm the package imports (from the repository root):

```bash
mcp/.venv/bin/python -c 'from podcast_mcp.server import mcp; print(mcp.name)'
```

`podcast-mcp` itself starts a stdio server waiting for an MCP client. It has no help
command. Point your client at it using one of the configurations below. Any client
that supports a local stdio MCP server can use these tools.

## Configuration (environment variables)

| Variable | Required | What it is |
|---|---|---|
| `PODCAST_URL` | yes | The app's base address, for example `http://127.0.0.1:8080` (Caddy) or `http://127.0.0.1:5189` (a dev copy's UI port, which proxies both `/companion` and `/api`). |
| `PODCAST_AUTH` | only if login is on | A full `Authorization` header value: `Bearer <token>` or `Basic <base64>`. Leave unset when the app runs with no login. |

The server never prints these. If the app answers 401, the tool error says "Set PODCAST_AUTH"
instead of guessing — it never asks you to paste a key into chat.

## Tools

| Tool | What it does |
|---|---|
| `discover_podcasts(query, limit?)` | Find public RSS shows by topic, name or host. Searches directory metadata; returns feed URLs. |
| `follow_podcast(feed_url)` | Add a publisher's RSS feed to the library. The server's auto-download settings apply. |
| `search_episodes(query, limit?, hits_per_episode?)` | Search every downloaded episode's transcript for a topic or phrase; a few quoted hits per episode. |
| `list_library(podcast_id?, cursor?, limit?)` | No `podcast_id`: list subscribed shows. With one: list that show's episodes, newest first; page with the returned `next_cursor`. |
| `list_queue(playlist_id?, cursor?, limit?)` | Read ordered episode IDs from Listen next or another library playlist. Also lists available playlists; page using `next_cursor`. |
| `prepare_episode(episode_id, download?, transcribe?)` | Read audio/transcript status. Explicit flags request a download or transcription. Reuses pending or parsed generated transcripts. |
| `get_transcript(episode_id, start?, end?, max_chars?, cursor?)` | Read complete text in pages: start at `cursor=0`, then follow `next_cursor`. Works with timed and untimed transcripts. Returns `transcript_digest`. Time-window reading remains available with `start/end`. |
| `get_brief(episode_id, generate?)` | Read an episode's brief (summary, HEAR/READ/SKIP, chapters, key ideas). `generate=false` (default) never calls AI; `generate=true` makes one if needed. |
| `plan_from_segments(want, selections, minutes?, skip_ads?)` | Make a cut from the connected agent's own keep/skip line-ID ranges, bound to transcript digests. Uses the caller's reasoning and prior learning; no app model call. |
| `list_plans(episode_id?, limit?)` | Reopen the signed-in user's saved plans, newest first. Optionally filter by episode; returns plan IDs, source context, and kept duration. |
| `get_plan_script(plan_id, cursor?, limit?)` | Page every kept and omitted sentence with source timestamps and reasons, including prior-learning references. |
| `plan_cut(want, episode_ids? \| use_queue?, skip?, minutes?, skip_ads?, mode?)` | Plan which passages to keep from which episodes. `mode="keyword"` needs no AI; `mode="ai"` does. |
| `render_cut(plan_id)` | Export a plan's enabled passages as one MP3. Returns a `job_id`. |
| `job_status(job_id)` | Poll an export: `queued` / `running` / `done` / `failed`, with progress. |
| `cut_link(cut_id)` | A finished cut's playable link, length, size and index back to the source episodes. |

Searches and plan summaries are capped, with `more_*` counts. Transcripts and scripts
have continuation cursors so the agent can inspect all their words. Times come back
in seconds and as `..._clock` (`"12:34"`, for a person). Tool errors explain the next
action. Audio links require the same login when authentication is enabled; they are
not anonymous share links.

## Agent workflow

1. Take the listener's goal, time budget and known material from your existing context
   or memory. Distinguish planned reading from material already understood or recalled.
2. Use `discover_podcasts` and `follow_podcast`, or `list_library`/`list_queue`/`search_episodes` to
   choose candidates. A directory result or a search snippet is not a full review.
3. Read each candidate with `get_transcript(cursor=0)`, then pass each `next_cursor`
   until it is null. Keep the digest from the first page. If it changes, restart the
   read. Cursor reading covers overlapping timestamps and long gaps, and lets you
   finish untimed text that previously could only be read at its beginning.
4. Compare actual explanations and examples with the listener's books, earlier podcasts
   and notes. Recommend HEAR, READ or SKIP with citations. Preserve new depth, useful
   disagreements, needed prerequisites and material the listener still cannot recall.
5. For a focused listen, call `plan_from_segments` with your keep ranges and explicit
   skip ranges. Every range uses transcript IDs, a reason, and optionally a relevance
   of 1–3 (3 most useful). Rank ranges from most to least useful within each episode.
6. Read every page of `get_plan_script`. Check context, the reasons for omissions,
   source timings and the actual kept duration. When exporting fits the user's request,
   call `render_cut`, poll `job_status`, then return `cut_link` and its source index.
7. After the listener hears it, ask a recall question. Keep their answer and follow-up
   needs in your own learner memory. A produced cut is not evidence that they heard,
   understood or mastered it.

The browser reads these same plans. Open `/ui/learn?episode=<episode_id>&plan=<plan_id>`
to inspect an episode's original transcript beside its selected listen. The browser
checks that a plan belongs to this source before displaying its selection. Users can
remove or restore proposed passages, Smart Play, or export the original audio. Use
`list_plans` to recover an earlier plan; creating a new plan does not overwrite it.

`selections` contains one object for each episode:

```json
{
  "episode_id": "11111111-1111-1111-1111-111111111111",
  "transcript_digest": "<copy the digest returned by get_transcript>",
  "keep": [
    {"start_id": "s24", "end_id": "s31", "why": "Unfamiliar failure example", "relevance": 3}
  ],
  "skip": [
    {"start_id": "s26", "end_id": "s27", "why": "Already recalled from the routing book, chapter 12"}
  ]
}
```

IDs above are illustrative; use the actual IDs and 32-character digest from the tool.
The server refuses unknown IDs, backwards ranges, duplicate episode selections, stale
transcripts and missing timing. Explicit skips win even when they overlap a keep range
or when sentence adjustment or context expansion would otherwise pull them back in.
Whole sentences count toward the initial time budget. The service stores the plan and
its keep/skip reasons; it does not need a copy of the listener's book.

Untimed text can support a recommendation, but it cannot supply exact cut ranges.
Generate timing from downloaded audio first. Cached briefs and the optional built-in
AI/keyword planner remain available; the built-in novelty score compares vocabulary
within one show and should not substitute for the agent's prior-learning comparison.

External platform account and playlist connections are not implemented. Today the
input is public RSS, library episodes, and the server's queue. The agent's own learner
memory is used; there is no automatic semantic memory sync between AI clients.

## Claude Code

```bash
claude mcp add podcast \
  --env PODCAST_URL=http://127.0.0.1:8080 \
  --env PODCAST_AUTH="Bearer your-token" \
  -- /path/to/podsift/mcp/.venv/bin/podcast-mcp
```

Or a project `.mcp.json`:

```json
{
  "mcpServers": {
    "podcast": {
      "command": "/path/to/podsift/mcp/.venv/bin/podcast-mcp",
      "env": {
        "PODCAST_URL": "http://127.0.0.1:8080",
        "PODCAST_AUTH": "Bearer your-token"
      }
    }
  }
}
```

Drop the `PODCAST_AUTH` line entirely when the app runs with no login.

## Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "podcast": {
      "command": "/path/to/podsift/mcp/.venv/bin/podcast-mcp",
      "env": {
        "PODCAST_URL": "http://127.0.0.1:8080",
        "PODCAST_AUTH": "Bearer your-token"
      }
    }
  }
}
```

## Cursor

Add to `.cursor/mcp.json` (project) or Cursor's global MCP settings — same shape as above:

```json
{
  "mcpServers": {
    "podcast": {
      "command": "/path/to/podsift/mcp/.venv/bin/podcast-mcp",
      "env": {
        "PODCAST_URL": "http://127.0.0.1:8080",
        "PODCAST_AUTH": "Bearer your-token"
      }
    }
  }
}
```

## Imported videos

Import a downloaded video in the browser's Learn flow. `list_videos` and `get_video`
expose the same user-owned source records and processing status. Request
`transcribe_video` only when the learner asks for STT; it may incur provider charges.
Read `get_video_transcript` from cursor zero through every `next_cursor`, keeping
the same digest, to inspect the complete speech on the original video clock.
Use those IDs and seconds for explanations and watch guidance. The screen remains
unobserved unless it has actually been reviewed in Course Watcher.

An agent can answer directly with its own model. `request_video_learning` optionally
asks the app's configured provider to save a `summary` or `watch_plan`; poll
`get_video` for its result. A prepared visual handoff has not run vision.
See [the video workflow](video-learning.md) for configuration and current limits.

## Development checks

```bash
cd mcp
.venv/bin/pip install -e ".[dev]"
.venv/bin/python3 -m pytest -q tests
```

Tests run fully offline against a fake companion (`httpx.MockTransport`) — no network, no real
podcast app needed.
