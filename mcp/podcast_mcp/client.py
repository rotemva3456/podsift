"""A small HTTP client for the podcast app's companion (and, through the same address, PodFetch's
own ``/api/v1`` API — one Caddy or Vite address serves both).

No imports from ``companion/``: everything here is a plain HTTP call, so this package can run in
its own venv against any deployment of the app (dev, docker compose, or the public release).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30.0
SET_AUTH = "Set PODCAST_AUTH (the podcast app needs a login)."


class PodcastError(Exception):
    """One sentence the calling agent can act on. Every tool error is this type."""


def _detail(response: httpx.Response) -> str:
    """FastAPI's own errors are ``{"detail": "<sentence>"}``. A pydantic validation error is
    ``{"detail": [{"msg": ..., "loc": [...]}, ...]}`` — join those into one readable line instead
    of handing the agent a list it has to parse itself."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        parts = []
        for item in detail:
            if isinstance(item, dict) and item.get("msg"):
                where = ".".join(str(p) for p in item.get("loc", []) if p not in ("body", "query", "path"))
                parts.append(f"{where}: {item['msg']}" if where else str(item["msg"]))
        if parts:
            return "; ".join(parts)
    return f"The podcast app answered {response.status_code} {response.reason_phrase}."


class CompanionClient:
    """Talks to one podcast app deployment. Build with :meth:`from_env`, or directly for tests."""

    def __init__(self, base_url: str, auth: str | None = None, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = DEFAULT_TIMEOUT):
        if not base_url or not base_url.strip():
            raise PodcastError("Set PODCAST_URL to your podcast app's address, "
                               "for example http://127.0.0.1:8080.")
        self.base_url = base_url.strip().rstrip("/")
        self._headers = {"Authorization": auth} if auth else {}
        self._transport = transport
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "CompanionClient":
        base_url = os.environ.get("PODCAST_URL", "")
        auth = os.environ.get("PODCAST_AUTH", "").strip() or None
        return cls(base_url, auth)

    def link(self, path: str) -> str:
        """The absolute URL for a path this client would call, for a tool to hand back to a user."""
        return f"{self.base_url}{path}"

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers,
                                         timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise PodcastError(f"Could not reach the podcast app at {self.base_url}: {exc}.") from exc
        if response.status_code == 401:
            raise PodcastError(SET_AUTH)
        if response.status_code >= 400:
            raise PodcastError(_detail(response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise PodcastError("The podcast app sent back something that wasn't JSON.") from exc

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)
