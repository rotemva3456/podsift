from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest

from podcast_mcp.client import CompanionClient

BASE_URL = "http://companion.test"


def run(coro):
    """Every tool function is async; the venv has no pytest-asyncio, so drive the loop directly."""
    return asyncio.run(coro)


def json_response(status_code: int, body: Any) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def client_for(handler: Callable[[httpx.Request], httpx.Response], auth: str | None = None) -> CompanionClient:
    return CompanionClient(BASE_URL, auth, transport=httpx.MockTransport(handler))


def fail_if_called(request: httpx.Request) -> httpx.Response:  # pragma: no cover - only runs on a bug
    raise AssertionError(f"no HTTP call was expected, got {request.method} {request.url}")


@pytest.fixture
def no_calls():
    return client_for(fail_if_called)
