"""Isolated video-learning acceptance fixture with deterministic local providers."""
from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ["EVIDENCE_DIR"])
STATE = Path(tempfile.mkdtemp(prefix="video-fixture-", dir=EVIDENCE))
UI_DIST = Path(os.environ.get("UI_DIST", str(APP_ROOT / "ui/dist"))).resolve()
ORIGIN = "http://127.0.0.1:" + os.environ.get("VERIFY_PORT", "5401")
sys.path.insert(0, str(APP_ROOT))

from companion.llm import get_llm
from companion.routes.videos import get_video_speech
from companion.server import create_app


class FixtureSpeech:
    base_url = "fixture://speech"
    speech_model = "synthetic-timed-speech-v1"

    def __init__(self):
        self.calls = 0

    def transcribe_words(self, _path):
        self.calls += 1
        passages = [
            (0.4, 6.5, "This synthetic lesson introduces shell conditionals and safe command checks."),
            (7.0, 13.5, "Use test dash f to check that a regular file exists before reading it."),
            (14.0, 20.5, "Quote variable expansions so spaces do not split one path into several arguments."),
            (21.0, 29.0, "Combine checks with and and or carefully, because their precedence can surprise readers."),
            (30.0, 37.5, "Double brackets offer safer pattern matching in Bash, while single brackets stay portable."),
            (38.0, 44.5, "The final practice is to validate input, report a useful error, and exit with a failure status."),
        ]
        return {
            "duration": 45.0,
            "text": " ".join(text for _, _, text in passages),
            "segments": [{"start": start, "end": end, "text": text} for start, end, text in passages],
            "words": [],
        }


class FixtureLearning:
    max_input_chars = 60_000

    def __init__(self):
        self.mode = "normal"
        self.release = threading.Event()
        self.release.set()
        self.calls = []
        self.lock = threading.Lock()

    def configure(self, mode):
        if mode not in ("normal", "wrong_provider", "block"):
            raise ValueError("Unknown fixture provider mode")
        with self.lock:
            self.mode = mode
            if mode == "block":
                self.release.clear()
            else:
                self.release.set()

    def complete_json(self, *, system, user, schema, max_tokens=1024):
        payload = json.loads(user)
        with self.lock:
            mode = self.mode
            self.calls.append({"mode": mode, "task": payload.get("task"), "goal": payload.get("goal")})
        if mode == "block":
            if not self.release.wait(30):
                raise RuntimeError("Fixture provider was not released")
        if mode == "wrong_provider":
            return {"points": [{"text": "This deliberately cites a passage from another source.",
                                 "citations": ["not-in-this-video"]}],
                    "moments": [], "caveat": "Synthetic invalid provider response."}
        if payload.get("task") == "watch_plan":
            return {
                "points": [{"text": "Check files before reading them and quote path expansions.",
                            "citations": ["v2", "v3"]}],
                "moments": [{"start_id": "v2", "end_id": "v4", "action": "watch",
                             "title": "Safe conditional checks", "why": "Covers file tests, quoting, and boolean operators."}],
                "caveat": "This guide uses synthetic speech evidence; the screen was not inspected.",
            }
        return {
            "points": [
                {"text": "Check that a file exists before reading it.", "citations": ["v2"]},
                {"text": "Quote variables and validate failures explicitly.", "citations": ["v3", "v6"]},
            ],
            "moments": [],
            "caveat": "This recap uses synthetic speech evidence; the screen was not inspected.",
        }


speech = FixtureSpeech()
learning = FixtureLearning()


def podfetch(_request):
    return httpx.Response(404)


app = create_app("http://podfetch.test", STATE / "browser.db",
                 transport=httpx.MockTransport(podfetch))
app.dependency_overrides[get_video_speech] = lambda: speech
app.dependency_overrides[get_llm] = lambda: learning


@app.get("/ui/index.html")
def index():
    config = {"serverUrl": ORIGIN, "basicAuth": False, "oidcConfigured": False}
    content = (UI_DIST / "index.html").read_text()
    marker = '<span id="config" data-config="' + html.escape(json.dumps(config), quote=True) + '"></span>'
    return HTMLResponse(content.replace("</body>", marker + "</body>"))


@app.get("/ui/{path:path}")
def ui(path: str):
    target = (UI_DIST / path).resolve()
    if target.is_relative_to(UI_DIST) and target.is_file():
        return FileResponse(target)
    return index()


@app.get("/verify/owner")
def owner():
    return {"owner": os.environ["VERIFY_OWNER"]}


@app.put("/verify/provider")
async def provider(request: Request):
    try:
        learning.configure((await request.json()).get("mode"))
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=422)
    return {"mode": learning.mode}


@app.post("/verify/provider/release")
def release_provider():
    learning.release.set()
    return {"released": True}


@app.get("/verify/provider")
def provider_state():
    return {"mode": learning.mode, "speech_calls": speech.calls, "learning_calls": learning.calls}


@app.api_route("/api/{path:path}", methods=["GET", "PUT", "POST"])
async def upstream(path: str, request: Request):
    if path == "v1/users/me":
        return {"username": "fixture", "role": "admin", "apiKey": "", "locale": "en"}
    if path == "v1/playlist":
        return []
    if request.method == "GET":
        return JSONResponse([], status_code=200)
    return Response(status_code=204)
