# Changes to the pinned PodFetch source

Upstream: `88ff1dfb96a7c8e85bdfab9f66f07c1a82d256bc` (see UPSTREAM.md). Regenerated
2026-09-26 from this project's own git history, from the base import commit onward, and
checked file-by-file against upstream's own tree at the pinned commit, so the list below
is what actually changed, not what was planned to.

## Existing PodFetch files changed

No changes to the imported Rust backend (`crates/`, `src/`, `migrations/`): unchanged,
run from the official pinned image (UPSTREAM.md). The UI and documentation paths below
were confirmed present in upstream's own tree at the pinned commit before editing:

- `ui/src/components/{Sidebar,Header,MainContentPanel}.tsx`: navigation, layout and
  upstream attribution, then the feature-registry mount points (`ui/src/ext`) that
  place nav links, home sections and player actions without editing these files again.
- `ui/src/pages/{Homepage,HomePageSelector,Podcasts,PodcastDetailPage,DiscoverPage}.tsx`:
  listening, show browsing, readable episode lists, directory search and RSS entry, then
  `HomePageSelector.tsx` gained feature home sections (2026-09-26).
- `ui/src/pages/SettingsPage.tsx`: added the feature settings-tab mount point (AI
  provider, login, feeds, "Worth hearing") alongside PodFetch's own tabs.
- `ui/src/components/{AudioComponents,DrawerAudioPlayer,HiddenAudioPlayer,
  PlayerProgressBar}.tsx`, `ui/src/utils/audioPlayer.ts`, `ui/src/store/AudioPlayerSlice.ts`:
  compact accessible player, explicit failures, stable pending seeks, cancelled stale
  requests, playback context, correct first-play behavior, then Smart Play's silent-span
  skipping and the sponsor/cut markers drawn on the progress bar.
- `ui/src/utils/http.ts`: the shared rule for attaching a caller's login header to every
  companion and PodFetch request once login exists.
- `ui/src/components/UserMenu.tsx`, `ui/src/pages/Login.tsx`: retain PodFetch login
  for self-hosted installations and connect the optional server-managed session client
  when that deployment mode is explicitly selected.
- `ui/src/{App,main}.tsx`, `ui/src/routing/Root.tsx`: notes/queue routes, persistent
  session, and mounting the feature registry.
- `ui/vite.config.ts`, `ui/vitest.config.ts`, `ui/package.json`, `ui/pnpm-lock.yaml`:
  loopback ports, the proxy to the local backend and companion, `cssCodeSplit: false` so
  PodFetch's single-stylesheet rule still ships every feature's CSS, and test config/deps
  for the feature registry and its tests.
- `ui/index.html`, `ui/public/manifest.webmanifest`: the installed app's title and name.
- Root `.gitignore`, `.dockerignore` and README (the original README is retained as
  `README.upstream.md`).
- `docs/src/AUTH.md`: correct the relative link to PodFetch's CLI instructions.

## Added by Podsift (not from upstream)

- **Companion container maintenance (v0.1.1)** — the Docker build uses a patched
  Python installer, checks the installed dependencies, and removes the installer
  from the runtime image. Application behavior and the pinned PodFetch backend
  are unchanged.
- **Native content skipping (2026-10-07)** — `companion/engine/skipping.py` detects
  explicitly signalled sponsor reads and the existing optional skip categories from
  timed transcripts, with source line IDs and reasons. Playback uses
  `/companion/episodes/{episode_id}/skip-segments`, independently of SponsorBlock
  timings. Existing per-user preferences are retained. App images and both Compose
  defaults disable the inherited external data lookup; no community data is copied.
- **Content-skip recovery and choices (2026-10-08)** — native per-user settings add
  manual category choices and a minimum passage length. The player offers Undo for
  actual jumps, permits one replay through overlapping Smart Play omissions, and
  uses the existing engine's timing check before installing automatic ranges.
  Unchecked publisher/library timestamps are manual suggestions; a loaded-media
  duration mismatch stops skipping. Missing timing and failed requests have visible
  recovery actions. See `docs/skipping.md` for scope and remaining accuracy evidence.

Confirmed absent from upstream's tree at the pinned commit. Grouped by area:

- **`companion/`** — the whole FastAPI service behind `/companion/*`: SQLite notes,
  the engine (`engine/`: select, ads, novelty, cards, render/ffmpeg, safe_fetch,
  align, edges and verify for cuts that land in pauses and are checked by listening),
  the listen-back check and re-cuts of an export (`listen.py`),
  the user's own AI provider (`llm.py`, `providers/`), login and
  sealed secrets (`auth.py`, `sealed.py`), episode briefs (`brief.py`), cut
  plans/jobs/export (`cuts.py`, `jobs.py`), search (`search.py`), the welcome
  flow and bundled demo pack (`demo.py`, `profile.py`), highlights and recap
  (`recap.py`), flashcards (`cards_store.py`), hands-free voice notes
  (`speech.py`), private feeds with AI chapters (`feeds.py`), model quality
  evals (`evals/`), and "Worth hearing" autobriefs (`worth`/`autobrief`).
- **`ui/src/ext/`** — the feature plug-in registry and every feature built on it:
  AI settings, login, brief, cuts/Smart Play (with the cut's script and its listen-back
  check), search, welcome, recap, cards, voice notes, feeds, "Worth hearing".
- **`ui/src/pages/Learn.tsx`, `ui/src/components/AskEpisode.tsx`, `ui/src/pages/
  ListenQueue.tsx`, `ui/src/components/ListenEpisodeRow.tsx`, `ui/src/utils/{companion,
  listening}.ts`** — the episode workspace (transcript, Ask, notes), the listening
  queue built on PodFetch's playlist API, and the client for this project's own API.
- **`mcp/`** — a separate MCP server package (its own venv, own `pyproject.toml`) so an
  AI agent can discover/follow RSS shows, prepare episodes, read complete transcript
  pages, submit its own version-bound keep/skip ranges, inspect the full script and
  export cuts without importing the app's code or configuring another chat model.
- **`docker/`, `compose.yaml`, `.env.example`, `.github/workflows/ci.yml`** — the
  one-command install: Caddy, the app and companion Dockerfiles, an optional local
  Ollama profile, and CI (tests, both images for amd64/arm64, an install smoke test).
- **`demo/`** — the bundled CC BY-SA demo pack and its licence/attribution record.
- **`docs/mcp.md`**, **`docs/agent-first.md`**, **`docs/models.md`**.
- **`scripts/`** — `dev.sh`, `check.sh`, `load-demo.sh`, `eval.py`,
  `import_library.py`, `initialize.py`, the `verify-*.cjs` browser checks.

## Not carried into this project

Two scratch scripts used only during this project's own development
(`scripts/check-handoff.sh`, `scripts/verify-handoff-browser.cjs`) were removed; they
never shipped and are not part of the product.

Personal library data is not vendored, and the demo pack ships as licensed transcripts
only (no audio committed — see `demo/LICENSE-CONTENT.md`). No model inference beyond the
free-tier checks used while building this was invoked.
# Distribution preparation — 8 October 2026

Self-hosted startup and PodFetch authentication no longer require the optional
managed-service implementation (`companion/runtime.py`, `companion/server.py`,
`companion/auth.py`, `companion/deps.py`, `companion/routes/settings_ai.py`). The
public release excludes service operations and internal deployment tooling.
`.github/workflows/ci.yml` checks that boundary and verifies synthetic desktop/mobile
skipping, Focus/Chill planning, video learning and the stdio agent-to-audio workflow.
The install check follows the built UI's module graph through the public proxy.
Installation steps in
`README.md` now start with downloading the repository. `CONTRIBUTING.md` and
`scripts/check.sh` describe independent self-hosted checks.
