#!/usr/bin/env bash
# Run the companion and the UI against a local PodFetch.
#   scripts/dev.sh [--preview]
# Settings (env): COMPANION_PORT (18081), UI_PORT (5189), COMPANION_DB (runtime/notes.db),
# PODFETCH_BACKEND (http://127.0.0.1:18080), PODCAST_LIBRARY. Two copies can run side by side:
#   COMPANION_PORT=18101 UI_PORT=5201 COMPANION_DB=runtime/second/companion.db scripts/dev.sh
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
export COMPANION_PORT="${COMPANION_PORT:-18081}" UI_PORT="${UI_PORT:-5189}"
export PODFETCH_BACKEND="${PODFETCH_BACKEND:-http://127.0.0.1:18080}"
COMPANION_DB="${COMPANION_DB:-${NOTES_DB:-runtime/notes.db}}"
[[ "$COMPANION_DB" == /* ]] || COMPANION_DB="$APP_DIR/$COMPANION_DB"
export COMPANION_DB
mkdir -p runtime/db runtime/podcasts "$(dirname -- "$COMPANION_DB")"
if [[ ! -d ui/node_modules ]]; then
  echo 'Install UI dependencies first: cd ui && pnpm install --frozen-lockfile' >&2
  exit 1
fi
if [[ "${1:-}" == "--preview" && ! -f ui/dist/index.html ]]; then
  echo 'Build the frontend first: cd ui && pnpm run build' >&2
  exit 1
fi
# A PodFetch that already answers is shared: never recreate or restart it from here.
# /api/v1/sys/config is public, so the probe also works when PodFetch requires a login.
if ! curl -fsS -o /dev/null --max-time 3 "$PODFETCH_BACKEND/api/v1/sys/config"; then
  docker compose -f compose.local.yaml up -d
fi
if [[ -z "${PODCAST_LIBRARY:-}" && -d operator/library ]]; then
  export PODCAST_LIBRARY="$APP_DIR/operator/library"
fi
PYTHON_BIN="${APP_PYTHON:-python3}"
"$PYTHON_BIN" scripts/initialize.py
set -m  # each background job gets its own process group, so the trap can stop Vite's children too
"$PYTHON_BIN" -m uvicorn companion.server:default_app --factory --host 127.0.0.1 --port "$COMPANION_PORT" &
NOTES_PID=$!
if [[ "${1:-}" == "--preview" ]]; then
  (cd ui && exec pnpm exec vite preview) &
else
  (cd ui && exec pnpm run dev) &
fi
UI_PID=$!
trap 'kill -- -"$NOTES_PID" -"$UI_PID" 2>/dev/null || true' EXIT INT TERM
printf 'Open http://127.0.0.1:%s/ui/\n' "$UI_PORT"
wait -n "$NOTES_PID" "$UI_PID"
