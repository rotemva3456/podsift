# Contributing to Podsift

Thanks for wanting to help. This project is a fork of [PodFetch](https://github.com/SamTV12345/PodFetch)
plus a Python companion service and a browser extension-style feature registry on top of
its UI. Most contributions only ever touch the companion or the UI — the Rust backend
runs from PodFetch's own official image and is not built here (below).

## Dev setup

Install Python 3.12+, Node.js 22+, pnpm 11.15.1, Docker with Compose, and
FFmpeg/ffprobe. On Ubuntu, the media tools are available with `sudo apt-get install ffmpeg`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r companion/requirements.txt pytest
python3 -m venv mcp/.venv && mcp/.venv/bin/pip install -e "./mcp[dev]"
cd ui && pnpm install --frozen-lockfile && cd ..
scripts/dev.sh
```

This starts the pinned official PodFetch backend in Docker, the companion, and the UI
dev server, and prints the URL to open. `scripts/dev.sh --preview` serves a production
UI build instead of the dev server (build it first with `cd ui && pnpm run build`).
Two copies can run side by side on different ports; see the comment at the top of
`scripts/dev.sh`. Set `APP_PYTHON=/path/to/python` if `python3` on your `PATH` isn't the
right interpreter.

## Checks

```bash
scripts/check.sh                 # companion, MCP, UI and build
```

Individually: `python3 -m pytest -q companion` and, inside `ui/`, `pnpm exec vitest run`
and `pnpm run build`. The separate agent package uses
`mcp/.venv/bin/python -m pytest -q mcp/tests`; set `MCP_PYTHON` if its environment is elsewhere.
The self-hosted app and these checks run without a managed-service identity,
billing or deployment stack. Service operations are maintained separately.
`python3 scripts/verify-agent-workflow.py` checks a real stdio MCP session through HTTP
to the companion and exports synthetic audio, without any external provider or browser.
The launch browser checks start their own companion and synthetic fixtures. After
building the UI and installing Chromium (`cd ui && pnpm exec playwright install --with-deps chromium`), run:

```bash
APP_PYTHON=.venv/bin/python node scripts/verify-skipping.cjs
APP_PYTHON=.venv/bin/python node scripts/verify-learning-modes.cjs
APP_PYTHON=.venv/bin/python node scripts/verify-video-learning.cjs
```

They cover desktop/mobile playback, saved listening modes, and video import/learning
without a personal library or external AI calls. Reports and screenshots go under
`evidence/`. Other `scripts/verify-*.cjs` checks can require a running dev copy.
Please add or update a test for any behavior change — the companion in particular has no type
checker to catch a broken contract for you.

## Adding a feature to the UI

Don't edit `App.tsx`, `Sidebar.tsx`, `Learn.tsx`, `SettingsPage.tsx`,
`ListenEpisodeRow.tsx` or `DrawerAudioPlayer.tsx` to add a screen, a settings tab, a nav
link or a player button — they read a plug-in registry instead. See
`ui/src/ext/README.md` for the `Feature` shape and where a new
`ui/src/ext/features/<name>/index.tsx` gets picked up automatically. New backend
routes follow the same pattern: a new `companion/routes/<name>.py` with
`router = APIRouter()` is included automatically — see `companion/deps.py` for the
shared dependencies (the AI provider, the current user, the database) every route uses.

## Style

Keep UI text in a feature's own `locales/en.json` (loaded through i18next), not string
literals in JSX — PodFetch already ships seven languages, so translators can extend
yours later. Reuse `ui/src/components/ui/*`, the tokens in `ui/src/listen.css`, and
lucide icons rather than adding a new design system. Every screen needs a loading,
empty and error state. Times are always seconds from the start of the episode's
**downloaded** audio file, never a live stream — see the note in `docs/getting-started.md`
if that's surprising.

## Good first issues

- **A brief with one bad citation fails the whole brief.** `companion/brief.py`'s
  `validate()` (around line 507) already drops an individual chapter or key idea whose
  `segment_id` doesn't resolve (see the filters around lines 568–587), but a citation
  that fails validation *before* that stage still lands in `problems` and fails the
  entire brief after one repair attempt (the raise around line 638). Weaker models (see
  `docs/models.md`) sometimes write a segment range or the literal `"??"` for one id in
  an otherwise-good brief. Make that one citation drop instead of failing the brief, and
  say so in the brief's `verdict_reason` or a new field, rather than surfacing a raw
  validation error.
- **Report a real upstream bug.** PodFetch's `utils/useDebounce` runs its effect inside
  a `useMemo`, which isn't guaranteed to run exactly once per render by React — worth a
  short issue on `SamTV12345/PodFetch` with a repro, not a fix in this fork.
- **The MCP `job_status` tool doesn't suggest a retry.** `mcp/podcast_mcp/logic.py`'s
  `job_status()` (around line 216) returns the raw `queued`/`running` status with no
  hint of when to check again. Add something like "check again in about 10 seconds" to
  a `running`/`queued` response, matching the plain, one-sentence-you-can-act-on style
  used elsewhere in the MCP tool errors.
- **Try OpenRouter's free models again.** `docs/models.md` recorded that both
  `qwen/qwen3.8-27b:free` and `google/gemma-4-26b-a4b-it:free` hit OpenRouter's
  rate limit before finishing even one fixture on the day this was measured. Re-run
  `scripts/eval.py --provider openrouter --model <name>` at a different time of day and
  add real numbers to that table if a run completes.
- **A phone's Settings tabs still clip.** On a 390px-wide screen, PodFetch's own
  settings tabs (Podcasts, Users, ...) are pushed partly off-screen by this project's
  added tabs (AI, Login, Feeds, Worth hearing) in `ui/src/pages/SettingsPage.tsx` and
  `ui/src/ext/ext.css`'s `.workspace-tabs[data-tools]` rules. The episode workspace tabs
  handle this the same way the phone player bar was fixed (auto-scroll the active tab
  into view); the settings page doesn't yet.

Anything else you find: open an issue first for anything larger than a small fix, so we
don't duplicate work. See `SECURITY.md` for security reports instead of a public issue.
