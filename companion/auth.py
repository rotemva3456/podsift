"""Login for the companion: PodFetch decides who is calling.

The companion has no accounts. For every ``/companion/*`` request it asks PodFetch who the caller
is, passing on the caller's own login headers, and PodFetch's answer decides:

    PodFetch runs without login           user "default"
    PodFetch accepts the caller's login   the PodFetch username
    PodFetch refuses it (401 or 403)      401 here
    PodFetch can't be asked               503 here, never a guess

"Runs without login" means PodFetch answers ``GET /api/v1/users/me`` for a caller that sends no
login at all. That holds for each PodFetch login (BASIC_AUTH, OIDC_AUTH, REVERSE_PROXY) without
knowing which one is on. The same route with the caller's headers names the caller; the UI calls
it too (``ui/src/routing/Root.tsx``). A 404 there means no PodFetch user API at PODFETCH_BACKEND
(a test double, or a wrong URL): that counts as "without login", and a warning is logged.

Two kinds of path skip the check (``is_public``): ``/companion/health``, and ``/companion/feed/*``,
which podcast apps read without a login; those routes check their own secret token.

Caching: the login mode and each positive answer are kept for at most 60 s. An answer is keyed by
an HMAC of the login headers under a key that exists only in this process, so no credential is
stored. Refusals are never kept.

Users: rows saved while PodFetch ran without login keep ``user_id = 'default'``. Once login is on,
nobody sees them until a multi-user setup assigns them to the first admin, for every table with a
``user_id`` column (today only ``notes``):

    UPDATE notes SET user_id = '<first admin username>' WHERE user_id = 'default';

A PodFetch user who is literally named "default" would see them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

CACHE_SECONDS = 60.0
MAX_CACHED = 1024
NO_LOGIN_USER = "default"
WHOAMI = "/api/v1/users/me"
PLEASE_LOG_IN = "Please log in."
UNAVAILABLE = "The podcast library is unavailable. Please try again."


def is_public(request: Request) -> bool:
    """True for /companion/health and /companion/feed/*, judged by the matched route's path."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return path == "/companion/health" or path.startswith("/companion/feed/")


def proxy_header() -> str:
    """The header PodFetch's reverse-proxy login reads: its REVERSE_PROXY_HEADER setting."""
    return os.getenv("REVERSE_PROXY_HEADER", "").strip() or "X-Forwarded-User"


def login_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The caller's login headers to pass on to PodFetch: Authorization, Cookie, the proxy user."""
    found: dict[str, str] = {}
    for name in ("Authorization", proxy_header()):
        value = headers.get(name) or headers.get(name.lower())
        if value:
            found[name] = value
    getlist = getattr(headers, "getlist", None)
    cookies = getlist("cookie") if getlist else [headers.get("Cookie") or headers.get("cookie")]
    cookie = "; ".join(value for value in cookies if value)
    if cookie:
        found["Cookie"] = cookie
    return found


class Authenticator:
    """Asks PodFetch who the caller is, with a short cache. One per app; safe across threads."""

    def __init__(self, podfetch_url: str, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 10,
                 require_identity: bool = False):
        self.podfetch_url = podfetch_url
        self.transport = transport
        self.clock = clock
        self.timeout = timeout
        self.require_identity = require_identity
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._mode: tuple[bool, float] | None = None  # (PodFetch checks logins, expires at)
        self._users: OrderedDict[bytes, tuple[str, float]] = OrderedDict()  # HMAC -> (user, expires at)
        self._warned = False

    def user(self, headers: Mapping[str, str]) -> str:
        """The caller's user id. Raises HTTPException 401 (no valid login) or 503 (PodFetch can't answer)."""
        if not self.checks_logins():
            return NO_LOGIN_USER
        login = login_headers(headers)
        if not login:  # PodFetch refuses a caller without a login; that's how checks_logins knows
            raise HTTPException(401, PLEASE_LOG_IN)
        key = hmac.new(self._key, json.dumps(sorted(login.items())).encode(), hashlib.sha256).digest()
        now = self.clock()
        with self._lock:
            cached = self._users.get(key)
            if cached and now < cached[1]:
                return cached[0]
        response = self._get(login)
        if response.status_code in (401, 403):
            raise HTTPException(401, PLEASE_LOG_IN)
        username = self._username(response)
        with self._lock:
            self._users[key] = (username, now + CACHE_SECONDS)
            self._users.move_to_end(key)
            if len(self._users) > MAX_CACHED:
                for stale in [k for k, (_, expires) in self._users.items() if expires <= now]:
                    del self._users[stale]
                while len(self._users) > MAX_CACHED:
                    self._users.popitem(last=False)
        return username

    def checks_logins(self) -> bool:
        """Whether PodFetch runs with a login: it refuses a caller that sends none."""
        now = self.clock()
        with self._lock:
            if self._mode and now < self._mode[1]:
                return self._mode[0]
        response = self._get({})
        if response.status_code in (401, 403):
            checks = True
        elif response.status_code == 404:
            if self.require_identity:
                raise HTTPException(503, UNAVAILABLE)
            checks = False
            if not self._warned:
                self._warned = True
                log.warning("PodFetch at %s has no %s, so nobody's login can be checked. "
                            "The companion treats it as running without login.", self.podfetch_url, WHOAMI)
        else:
            self._username(response)  # a 200 that isn't a user (a web page, say) is not "no login"
            if self.require_identity:
                raise HTTPException(503, UNAVAILABLE)
            checks = False
        with self._lock:
            self._mode = (checks, now + CACHE_SECONDS)
        return checks

    def _get(self, login: Mapping[str, str]) -> httpx.Response:
        try:
            with httpx.Client(base_url=self.podfetch_url, transport=self.transport, timeout=self.timeout) as client:
                return client.get(WHOAMI, headers=dict(login))
        except httpx.HTTPError as exc:
            raise HTTPException(503, UNAVAILABLE) from exc

    @staticmethod
    def _username(response: httpx.Response) -> str:
        """The username from a 200 answer with a PodFetch user, else 503."""
        username: Any = None
        if response.status_code == 200:
            try:
                username = response.json().get("username")
            except (ValueError, AttributeError):
                username = None
        if isinstance(username, str) and username:
            return username
        raise HTTPException(503, UNAVAILABLE)


_creating = threading.Lock()


def authenticator(app: Any) -> Authenticator:
    """The app's Authenticator, made on first use from its settings (PodFetch URL and transport)."""
    found = getattr(app.state, "authenticator", None)
    if found is None:
        with _creating:
            found = getattr(app.state, "authenticator", None)
            if found is None:
                settings = app.state.settings
                found = Authenticator(settings.podfetch_url, transport=settings.transport)
                app.state.authenticator = found
    return found


def user_for(request: Request) -> str:
    """The caller's user id, asked once per request (see ``Authenticator.user``)."""
    user = getattr(request.state, "companion_user", None)
    if user is None:
        from .runtime import hosted_policy
        policy = hosted_policy()
        if policy is not None:
            identity = policy.verify(request)
            request.state.hosted_identity = identity
            user = identity.user_id
        else:
            user = authenticator(request.app).user(request.headers)
        request.state.companion_user = user
    return user
