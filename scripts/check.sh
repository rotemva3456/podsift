#!/usr/bin/env bash
# Offline checks: companion and release tooling, MCP, UI, then the production build.
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
"${APP_PYTHON:-python3}" -m pytest -q companion --ignore=companion/test_gateway_sessions.py
"${MCP_PYTHON:-mcp/.venv/bin/python}" -m pytest -q mcp/tests
cd ui
pnpm exec vitest run
pnpm run build
