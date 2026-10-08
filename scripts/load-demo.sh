#!/usr/bin/env bash
# Load the bundled demo pack: subscribes PodFetch to a feed of the demo show,
# copies its transcripts onto PODCAST_LIBRARY, and imports its precomputed briefs - so brief, Cut,
# Smart Play and search all work with no AI key. Safe to run more than once.
#
#   scripts/load-demo.sh
#
# Settings (env):
#   APP_URL                 where this script itself reaches the whole app (default the compose
#                            deployment's Caddy port, http://127.0.0.1:8080; a dev copy needs its
#                            own UI port instead, e.g. http://127.0.0.1:5189).
#   COMPANION_INTERNAL_URL   where PodFetch itself reaches the companion to fetch the demo feed
#                            (default http://companion:8000, the compose network's own DNS name).
#                            PodFetch runs in its own container, so "127.0.0.1" from here is not
#                            the same "127.0.0.1" as APP_URL: this can't just reuse APP_URL.
#   LIBRARY_DIR              host folder bind-mounted at the companion's PODCAST_LIBRARY, used
#                            only when there is no running "companion" container to copy through
#                            (default ./data/companion/library, matching compose.yaml).
#   SHOW_ID                  which demo/library/<show> to load (default hpr-bash-tips).
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
PYTHON_BIN="${APP_PYTHON:-python3}"
APP_URL="${APP_URL:-http://127.0.0.1:8080}"
COMPANION_INTERNAL_URL="${COMPANION_INTERNAL_URL:-http://companion:8000}"
LIBRARY_DIR="${LIBRARY_DIR:-data/companion/library}"
SHOW_ID="${SHOW_ID:-hpr-bash-tips}"
SOURCE_DIR="demo/library/$SHOW_ID"

if [[ ! -f "$SOURCE_DIR/_feed.json" ]]; then
  echo "No demo library at $SOURCE_DIR (looked for _feed.json)." >&2
  exit 1
fi
if ! curl -fsS -o /dev/null --max-time 5 "$APP_URL/companion/health"; then
  echo "Can't reach the companion at $APP_URL/companion/health. Is the app running (APP_URL correct)?" >&2
  exit 1
fi

# The companion container's entrypoint chowns ./data/companion to its own uid on every start
# (docker/companion-entrypoint.sh), so this host user usually cannot write into it directly. When
# a "companion" container is running for this compose project, copy through the Docker daemon
# instead (it can write into the bind mount regardless of the host directory's owner).
if [[ -n "$(docker compose -f compose.yaml ps --status running -q companion 2>/dev/null)" ]]; then
  echo "Copying $SOURCE_DIR onto the running companion's PODCAST_LIBRARY (via docker compose cp)"
  docker compose -f compose.yaml exec -T companion mkdir -p /data/library
  docker compose -f compose.yaml cp "$SOURCE_DIR" "companion:/data/library/"
  # docker cp keeps the source's owner and mode (this host user, sometimes 0600 on a private
  # checkout). The companion runs as its own unprivileged user, not this host user, so hand the
  # copy to it and make it world-readable. Never hard-code that user's id: ask the container,
  # since exec defaults to root there regardless of who the entrypoint drops PID 1 to.
  APP_UID="$(docker compose -f compose.yaml exec -T --user app companion id -u)"
  docker compose -f compose.yaml exec -T companion chown -R "$APP_UID:$APP_UID" /data/library
  docker compose -f compose.yaml exec -T companion chmod -R a+rX /data/library
else
  echo "Copying $SOURCE_DIR -> $LIBRARY_DIR/$SHOW_ID"
  mkdir -p "$LIBRARY_DIR/$SHOW_ID"
  cp -f "$SOURCE_DIR"/* "$LIBRARY_DIR/$SHOW_ID"/
  # No container/user boundary here (native or dev-mode companion), but a private checkout's
  # modes could still lock this host user's own process out if it runs as someone else; make
  # sure the copy is at least readable.
  chmod -R a+rX "$LIBRARY_DIR/$SHOW_ID" 2>/dev/null || true
fi

TITLE="$("$PYTHON_BIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['title'])" "$SOURCE_DIR/_feed.json")"
ALREADY="$(curl -fsS --max-time 10 "$APP_URL/api/v1/podcasts" | "$PYTHON_BIN" -c "
import json, sys
title = sys.argv[1]
shows = json.load(sys.stdin)
print('yes' if any(isinstance(s, dict) and s.get('name') == title for s in shows) else 'no')
" "$TITLE")"

if [[ "$ALREADY" == "yes" ]]; then
  echo "PodFetch already has \"$TITLE\" - not subscribing again."
else
  FEED_URL="$COMPANION_INTERNAL_URL/companion/demo/feed/$SHOW_ID.xml"
  echo "Subscribing PodFetch to $FEED_URL"
  curl -fsS --max-time 20 -X POST "$APP_URL/api/v1/podcasts/feed" \
    -H 'Content-Type: application/json' \
    -d "{\"rssFeedUrl\": \"$FEED_URL\"}" >/dev/null
fi

echo "Importing precomputed briefs (waits a little for PodFetch to finish parsing the feed)..."
RESULT="$(curl -fsS --max-time 30 -X POST "$APP_URL/companion/demo/import?show_id=$SHOW_ID")"
echo "$RESULT" | "$PYTHON_BIN" -m json.tool

SKIPPED="$(echo "$RESULT" | "$PYTHON_BIN" -c "import json,sys; print(len(json.load(sys.stdin).get('skipped') or []))")"
if [[ "$SKIPPED" != "0" ]]; then
  echo "Note: $SKIPPED episode(s) were not imported yet (see \"skipped\" above). Run this script again in a moment." >&2
fi
echo "Done."
