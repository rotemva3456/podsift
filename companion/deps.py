"""FastAPI dependencies shared by every route module in ``companion/routes``.

    get_settings     the app's Settings (PodFetch URL, database file, library folder)
    get_podfetch     a PodFetch API client that forwards the caller's login
    get_transcripts  a function: episode_id (and optionally its PodFetch episode) -> Transcript
    get_db           one SQLite connection per request, committed when the route returns
    current_user     the caller's user id: the PodFetch username, or "default" without login

Login (auth.py): each dependency here needs the caller's login first. get_settings runs
require_login and the others build on it; current_user asks auth.py itself. So a route that uses
any of them answers 401 without a login when PodFetch has login on. /companion/health and
/companion/feed/* skip require_login; feed routes check their own token.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request

from . import auth
from . import db as database
from .runtime import hosted_enabled
from .transcripts import Transcript, episode_transcript

if TYPE_CHECKING:
    import sqlite3

    from .answers import AnswerProvider


@dataclass(frozen=True)
class Settings:
    podfetch_url: str
    database: Path
    library_root: Path | None = None
    transport: httpx.BaseTransport | None = None  # tests pass an httpx.MockTransport
    answer_provider: AnswerProvider | None = None


def require_login(request: Request) -> None:
    """401 unless PodFetch knows the caller (auth.py). /companion/health and /companion/feed/* skip it."""
    if request.url.path != "/companion/health" and (hosted_enabled() or not auth.is_public(request)):
        auth.user_for(request)


def get_settings(request: Request, _: None = Depends(require_login)) -> Settings:
    return request.app.state.settings


class PodFetch:
    """PodFetch's HTTP API (paths start with ``/api/v1``). Errors become messages a user can act on."""

    def __init__(self, base_url: str, *, headers: Mapping[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20):
        self.base_url = base_url
        self.headers = dict(headers or {})
        self.transport = transport
        self.timeout = timeout

    def request(self, method: str, path: str, *, optional: bool = False,
                not_found: str = "This episode is no longer in your library.", **kwargs: Any) -> Any:
        """Send one request and return its JSON (None for an empty body, or for 404 when ``optional``)."""
        try:
            with httpx.Client(base_url=self.base_url, headers=self.headers, timeout=self.timeout,
                              transport=self.transport) as client:
                response = client.request(method, path, **kwargs)
                if response.status_code == 404:
                    if optional:
                        return None
                    raise HTTPException(404, not_found)
                if response.status_code == 401:
                    raise HTTPException(401, auth.PLEASE_LOG_IN)
                if response.status_code == 403:
                    raise HTTPException(403, "Your PodFetch account isn't allowed to do this.")
                response.raise_for_status()
                return response.json() if response.content else None
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "The podcast library is unavailable. Please try again.") from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def episode(self, episode_id: UUID | str) -> dict[str, Any]:
        """The PodFetch episode for the public ``episode_id`` that our URLs use."""
        return self.get(f"/api/v1/episodes/{episode_id}")["podcastEpisode"]


def forwarded_headers(request: Request) -> dict[str, str]:
    """The caller's login headers to pass on to PodFetch: Authorization, Cookie, the proxy user."""
    if hosted_enabled():
        return {auth.proxy_header(): auth.user_for(request)}
    return auth.login_headers(request.headers)


def get_podfetch(request: Request, settings: Settings = Depends(get_settings)) -> PodFetch:
    return PodFetch(settings.podfetch_url, headers=forwarded_headers(request), transport=settings.transport)


TranscriptLoader = Callable[..., Transcript]


def get_transcripts(request: Request, podfetch: PodFetch = Depends(get_podfetch)) -> TranscriptLoader:
    """``load(episode_id, episode=None)``; pass the PodFetch episode when you already have it."""
    library = request.app.state.transcript_library

    def load(episode_id: UUID | str, episode: dict[str, Any] | None = None) -> Transcript:
        if episode is None:
            episode = podfetch.episode(episode_id)
        return episode_transcript(episode_id, episode, podfetch, library)

    return load


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    """One connection per request: committed when the route returns, rolled back when it raises."""
    db = database.connect(settings.database)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def current_user(request: Request) -> str:
    """The caller's user id: the PodFetch username, or "default" when PodFetch runs without login.

    Every query on user data filters by it. It always needs a login, even on a public path.
    """
    return auth.user_for(request)
