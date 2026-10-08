"""Login: PodFetch decides who is calling, and every /companion/* path except health and feed needs it."""
import base64
import re
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import auth
from companion.server import create_app
from companion.test_foundation import drop_module
from companion.test_server import EPISODE

EPISODE_JSON = {"podcastEpisode": {"id": EPISODE, "episode_id": EPISODE, "name": "An episode",
                                   "url": "https://example.test/a.mp3", "total_time": 120}}


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


ALICE, BOB = basic("alice", "alice-password"), basic("bob", "bob-password")


class PodFetch:
    """PodFetch as the companion sees it. With login on, ``logins`` maps a login header value to a
    username and anything else is refused; with ``login=False`` every caller is PodFetch's own user."""

    def __init__(self, logins=None, *, login=True, header="Authorization", refuse=403):
        self.logins, self.login, self.header, self.refuse = dict(logins or {}), login, header, refuse
        self.seen = []  # (path, the login headers the request carried)
        self.answer = None  # optional function(request) -> Response, to break PodFetch on purpose

    def __call__(self, request):
        carried = {name: value for name, value in request.headers.items()
                   if name in ("authorization", "cookie", self.header.lower())}
        self.seen.append((request.url.path, carried))
        if self.answer:
            return self.answer(request)
        user = self.logins.get(request.headers.get(self.header, "")) if self.login else "user123"
        if user is None:
            return httpx.Response(self.refuse)
        if request.url.path == "/api/v1/users/me":
            return httpx.Response(200, json={"id": "7", "username": user, "role": "admin",
                                             "apiKey": "podfetch-api-key", "readOnly": not self.login})
        if request.url.path == f"/api/v1/episodes/{EPISODE}":
            return httpx.Response(200, json=EPISODE_JSON)
        return httpx.Response(404)

    def whoami(self, login=None):
        """How often PodFetch was asked who the caller is, with this login header (None: with none)."""
        return sum(1 for path, carried in self.seen if path == "/api/v1/users/me"
                   and carried.get(self.header.lower()) == login)


def start(tmp_path, podfetch, *, clock=None):
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(podfetch))
    if clock:
        auth.authenticator(app).clock = clock
    return TestClient(app), app


def note(text="A note"):
    return {"id": str(uuid4()), "episode_id": EPISODE, "position": 30, "text": text}


def saved_users(tmp_path):
    with sqlite3.connect(tmp_path / "companion.db") as db:
        return [row[0] for row in db.execute("SELECT user_id FROM notes ORDER BY created_at")]


def test_podfetch_without_login_lets_everyone_in_as_default(tmp_path):
    podfetch = PodFetch(login=False)
    client, _ = start(tmp_path, podfetch)
    assert client.post("/companion/notes", json=note()).status_code == 201
    assert len(client.get("/companion/notes").json()) == 1
    assert client.get("/companion/notes", headers={"Authorization": "Basic bm90OnVzZWQ="}).status_code == 200
    assert saved_users(tmp_path) == ["default"]
    assert podfetch.whoami() == 1  # one question, "does PodFetch check logins?", kept for 60 s


@pytest.mark.parametrize("refuse", [403, 401])  # basic auth refuses with 403, OIDC with 401
def test_with_login_on_no_header_or_a_bad_one_gets_401_and_a_good_one_200(tmp_path, refuse):
    podfetch = PodFetch({ALICE: "alice"}, refuse=refuse)
    client, _ = start(tmp_path, podfetch)
    for headers in ({}, {"Authorization": basic("alice", "wrong")}, {"Authorization": "Bearer made-up"}):
        for method, path in [("GET", "/companion/notes"), ("POST", "/companion/notes"),
                             ("GET", f"/companion/episodes/{EPISODE}/transcript"),
                             ("GET", "/companion/answers/status"), ("POST", f"/companion/episodes/{EPISODE}/answer")]:
            response = client.request(method, path, headers=headers, json=note() if method == "POST" else None)
            assert (response.status_code, response.json()) == (401, {"detail": "Please log in."}), (headers, path)
    assert client.post("/companion/notes", json=note(), headers={"Authorization": ALICE}).status_code == 201
    assert client.get("/companion/notes", headers={"Authorization": ALICE}).status_code == 200
    assert client.get("/companion/health").json()["status"] == "ok"
    assert saved_users(tmp_path) == ["alice"]


def test_bearer_tokens_and_cookies_pass_through_to_podfetch(tmp_path):
    podfetch = PodFetch({"Bearer oidc-token-1": "carol"})
    client, _ = start(tmp_path, podfetch)
    login = {"Authorization": "Bearer oidc-token-1", "Cookie": "sessionid=abc"}
    assert client.post("/companion/notes", json=note(), headers=login).status_code == 201
    asked = [(path, carried) for path, carried in podfetch.seen if carried]
    assert [path for path, _ in asked] == ["/api/v1/users/me", f"/api/v1/episodes/{EPISODE}"]
    assert all(carried == {"authorization": "Bearer oidc-token-1", "cookie": "sessionid=abc"} for _, carried in asked)
    assert saved_users(tmp_path) == ["carol"]


def test_the_login_cache_never_lasts_longer_than_60_seconds(tmp_path):
    now = [1000.0]
    podfetch = PodFetch({ALICE: "alice"})
    client, _ = start(tmp_path, podfetch, clock=lambda: now[0])

    def get_at(seconds):
        now[0] = 1000.0 + seconds
        return client.get("/companion/notes", headers={"Authorization": ALICE}).status_code

    assert get_at(0) == 200 and podfetch.whoami(ALICE) == 1
    assert get_at(59.9) == 200 and podfetch.whoami(ALICE) == 1  # kept
    podfetch.logins.clear()  # PodFetch stops accepting the login (password changed, user removed)
    assert get_at(59.9) == 200  # still inside the 60 s
    assert get_at(60) == 401 and podfetch.whoami(ALICE) == 2  # 60 s after the answer: asked again
    assert get_at(61) == 401 and podfetch.whoami(ALICE) == 3  # a refusal is never kept
    assert podfetch.whoami() == 2  # "does PodFetch check logins?" is asked again after 60 s too


def test_user_a_cannot_read_or_overwrite_user_b_notes(tmp_path):
    client, _ = start(tmp_path, PodFetch({ALICE: "alice", BOB: "bob"}))
    alice, bob = {"Authorization": ALICE}, {"Authorization": BOB}
    mine = note("Alice's note")
    assert client.post("/companion/notes", json=mine, headers=alice).status_code == 201
    assert client.get("/companion/notes", headers=bob).json() == []
    assert client.get(f"/companion/notes?episode_id={EPISODE}", headers=bob).json() == []
    assert client.post("/companion/notes", json=mine, headers=bob).status_code == 409
    assert [n["text"] for n in client.get("/companion/notes", headers=alice).json()] == ["Alice's note"]
    assert saved_users(tmp_path) == ["alice"]


def test_every_route_needs_a_login_except_health_and_feed(tmp_path, monkeypatch):
    drop_module(monkeypatch, tmp_path, "zz_feed_probe", '''
from fastapi import APIRouter, Depends
from companion.deps import get_db
router = APIRouter()
@router.get("/companion/feed/{token}/probe.xml")
def feed(token: str, db=Depends(get_db)):
    return {"token": token}
''')
    podfetch = PodFetch({ALICE: "alice"})
    client, app = start(tmp_path, podfetch)
    answered = []
    # OpenAPI resolves included routers across FastAPI versions; app.routes is
    # now a route tree rather than a flat list of APIRoute instances.
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/companion/"):
            for method in sorted(operations):
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                    continue
                if client.request(method, re.sub(r"\{[^}]+\}", EPISODE, path)).status_code != 401:
                    answered.append(f"{method.upper()} {path}")
    assert {"GET /companion/health", "GET /companion/feed/{token}/probe.xml"} <= set(answered)
    assert [x for x in answered if not x.endswith(" /companion/health") and " /companion/feed/" not in x] == [], (
        "These routes answer without a login. Give each one a companion.deps dependency "
        "(current_user, get_db, get_podfetch, get_settings or get_transcripts).")
    assert not [seen for seen in podfetch.seen if seen[1]]  # nothing was sent with a login


def podfetch_is_down(request):
    raise httpx.ConnectError("PodFetch is down")


@pytest.mark.parametrize("answer", [
    podfetch_is_down,
    lambda request: httpx.Response(500),
    lambda request: httpx.Response(200, text="<html>a web page, not PodFetch's API</html>"),
    lambda request: httpx.Response(200, json={"no": "username"}),
])
def test_when_podfetch_cannot_say_who_is_calling_nobody_gets_in(tmp_path, answer):
    podfetch = PodFetch(login=False)
    podfetch.answer = answer
    client, _ = start(tmp_path, podfetch)
    response = client.get("/companion/notes")
    assert (response.status_code, response.json()) == (503, {"detail": auth.UNAVAILABLE})


def test_with_login_on_a_podfetch_that_cannot_name_the_caller_is_503(tmp_path):
    podfetch = PodFetch({ALICE: "alice"})
    client, _ = start(tmp_path, podfetch)
    assert client.get("/companion/notes").status_code == 401  # login mode is now known
    podfetch.answer = lambda request: httpx.Response(404 if request.headers.get("Authorization") else 403)
    assert client.get("/companion/notes", headers={"Authorization": ALICE}).status_code == 503


def test_no_credential_is_kept_in_memory(tmp_path):
    client, app = start(tmp_path, PodFetch({ALICE: "alice"}))
    login = {"Authorization": ALICE, "Cookie": "sessionid=secret-cookie"}
    assert client.get("/companion/notes", headers=login).status_code == 200
    kept = repr(vars(auth.authenticator(app)))
    assert "alice" in kept  # the answer is kept...
    for secret in (ALICE, ALICE.split()[1], "alice-password", "secret-cookie", "podfetch-api-key"):
        assert secret not in kept  # ...but never the login itself, nor PodFetch's API key


def test_reverse_proxy_user_header_is_passed_on(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSE_PROXY_HEADER", "Remote-User")
    podfetch = PodFetch({"dave": "dave"}, header="Remote-User")
    client, _ = start(tmp_path, podfetch)
    assert client.get("/companion/notes").status_code == 401
    assert client.post("/companion/notes", json=note(), headers={"Remote-User": "dave"}).status_code == 201
    assert saved_users(tmp_path) == ["dave"]
    assert podfetch.seen[-1] == (f"/api/v1/episodes/{EPISODE}", {"remote-user": "dave"})


@pytest.mark.parametrize("status, detail", [(401, "Please log in."),
                                            (403, "Your PodFetch account isn't allowed to do this.")])
def test_podfetch_refusing_a_forwarded_call_is_a_login_problem_not_an_outage(tmp_path, status, detail):
    podfetch = PodFetch({ALICE: "alice"})
    client, _ = start(tmp_path, podfetch)
    assert client.get("/companion/notes", headers={"Authorization": ALICE}).status_code == 200  # logged in, cached
    podfetch.answer = lambda request: httpx.Response(status)
    response = client.post("/companion/notes", json=note(), headers={"Authorization": ALICE})
    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert saved_users(tmp_path) == []
