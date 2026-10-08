# Getting started

For the primary agent workflow, start with [Connect your AI agent](mcp.md). The browser
steps below are optional; agent-directed selection does not need the app's own chat AI key.

This walks through the first few minutes after `docker compose up -d --wait`
(`docker/README.md` has the install command and every setting). It assumes you're
running with login off, the simplest way to try it on your own machine first.

## 1. Open it and see something real without any setup

Open **http://127.0.0.1:8080/ui/**. With an empty library, the home screen offers a demo
show to load — or run it yourself:

```bash
COMPOSE_PROJECT_NAME=<your compose project name> scripts/load-demo.sh
```

`COMPOSE_PROJECT_NAME` only matters if you started the stack with `docker compose -p
<name> ...`; the plain `docker compose up` above doesn't need it. This subscribes you to
three CC BY-SA "HPR Bash Tips" episodes with a transcript and a precomputed brief
already attached, so brief, Cut, search and Smart Play all work before you add a real
show or an AI key. Safe to run again later. See `demo/LICENSE-CONTENT.md` for what it is
and its licence.

## 2. Add your own show

**More tools → Find podcasts** searches a public directory; **Library** also accepts a raw RSS
URL. A newly added show downloads its most recent episodes in the background — turn
that off first in **Settings** if you'd rather stream and choose per-episode.

## 3. Make a transcript

An episode needs a transcript before it has a brief, a cut, or search results. Some
feeds already publish one (Podcasting 2.0 `<podcast:transcript>`) and PodFetch picks it
up automatically. If an episode has none, its page offers **Make a transcript** — this
needs a downloaded copy of the episode and a speech-to-text key
(`TRANSCRIPTION_API_BASE_URL`/`TRANSCRIPTION_API_KEY`/`TRANSCRIPTION_MODEL` in `.env`;
Groq's free tier works for shorter episodes).

## 4. Connect your own AI key

**Settings -> AI** takes any OpenAI-compatible provider: Groq (free tier), OpenAI,
OpenRouter, or a local Ollama (`docker/README.md`'s "Local AI with Ollama"). The key
stays on your server and is never sent to the browser. Without a key, briefs, AI-mode
cuts, and Ask don't run — everything else still works. `docs/models.md` has real
measurements of which free Groq models are worth using for a brief and which aren't.

## 5. Get a brief, then cut it down

Open an episode's **Brief** tab for a summary, a HEAR/READ/SKIP verdict, chapters, and
what's new to you if you've heard similar episodes before. The **Cut** tab plans which
passages to keep — by keyword with no AI, or by describing what you want to hear with
AI on — then renders one MP3 with an index back to the source timestamps. **Smart Play**
skips a plan's cut passages live during normal playback instead of exporting a file.

**Learn** keeps the source words central. Open an episode, navigate its subjects or
search its exact transcript, and select passages to explain, save, or turn into a
listen. **Original transcript / My selected listen** switches between the full source
and a saved cut script. A connected agent's saved plan appears here too; opening it
does not require a second AI provider. The episode's Ask panel uses the optional app
provider for source-linked questions.

**Knowledge** holds the ideas and personal notes you deliberately save. Search their
words or source titles, then open the original timestamp to recover the context.
**Today** keeps listening and resuming close at hand. The listening queue remains
available under **More tools → Listen next**.

## 6. Install it on your phone

- **As a web app:** open the same URL on your phone's browser and use "Add to Home
  Screen" / "Install app". This needs HTTPS from another device — see "Phones and other
  computers" in `docker/README.md` for a reverse proxy in front of it. Android/Chrome
  names the installed icon from the app manifest, which says "Podsift"; the browser tab
  title and iOS's home-screen label still say "Podfetch" (PodFetch's Rust backend
  generates that page itself and hard-codes both — not something a fork can change
  without patching upstream).
- **Through your existing podcast app:** turn on `GPODDER_INTEGRATION_ENABLED=true` (needs
  a login first) and sync AntennaPod or another gpodder-compatible app to this server —
  your queue and play history stay in sync both ways.
- **As a private feed:** **Settings -> Podcast app feeds** makes a personal token and
  gives you an RSS URL per show (with AI chapters and sponsor segments added) and one URL
  for everything you've cut, so any podcast app can subscribe directly — this works with
  login off or on. With login on, make the token from a normal invited account: the
  `BASIC_AUTH` bootstrap admin can't hold the API key a feed needs then. Making a new
  token invalidates the old one immediately.

## 7. Before you invite anyone else

Turn on `BASIC_AUTH` (or `OIDC_AUTH`/`REVERSE_PROXY`) in `.env` and restart. Load the
demo *before* turning login on if you want it (step 1) — the bootstrap admin account
`BASIC_AUTH` creates can sign in, but it's never issued the ordinary invited-user account
a podcast-app feed link (step 6) needs; invite a normal account for that. Only give
accounts to people you already trust (see `SECURITY.md`).

## Back up before you experiment

`docker/README.md`'s Backup section covers this, but the short version: stop the app,
copy `./data`, start it again. Your AI settings live in `data/companion/ai-settings.json`
plus the `ai-settings.secret` file that seals the key inside it, and feed tokens (step 6)
depend the same way on `data/companion/feed.secret` — losing either `.secret` file makes
its settings unreadable even though the `.json`/database rows are still there. All of it
is inside `./data`, so a full copy of that folder is a full backup.
