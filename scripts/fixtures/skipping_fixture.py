"""Isolated browser fixture: real companion routes, synthetic transcript/audio only."""
import html
import io
import json
import os
import tempfile
import sys
import wave
from pathlib import Path

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ["EVIDENCE_DIR"])
STATE = Path(tempfile.mkdtemp(prefix="fixture-", dir=EVIDENCE))
UI_DIST = Path(os.environ.get("UI_DIST", str(APP_ROOT / "ui/dist"))).resolve()
ORIGIN = "http://127.0.0.1:" + os.environ.get("VERIFY_PORT", "5399")
sys.path.insert(0, str(APP_ROOT))
from companion.server import create_app
from companion.test_cuts import EP, PID, FakePodFetch, native, vtt
from companion.llm import get_llm

fake = FakePodFetch()
fake.add(name="A networking lesson with a sponsor", transcript="generated")
fake.episodes[EP].update(podcast_id="synthetic-show", description="Synthetic verification source.",
    local_url=ORIGIN + "/podcasts/native/lesson.wav", local_image_url="",
    date_of_recording="2026-10-07", guid="native-verification", deleted=False,
    episode_numbering_processed=False, image_url="")
fake.native[PID] = native([
    "Welcome to our podcast.",
    "This episode is sponsored by Acme.",
    "Their service helps your team work together.",
    "Visit acme.example for a free trial.",
    "Now we explain how BGP route selection works.",
    "Opengear is a networking vendor; learn more about routing in the reference.",
    "Support our show by buying our book.",
    "Please subscribe to our podcast.",
    "BGP uses weight, local preference and AS path length.",
    "Thanks for listening. See you next week.",
], [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 40),
    (40, 45), (45, 50), (50, 115), (115, 120)])

def set_source(origin):
    fake.episodes[EP]["total_time"] = 110 if origin == "mismatch" else 120
    if origin == "missing":
        fake.native.clear()
        fake.listing.clear()
        return
    if not fake.native:
        fake.native[PID] = native_fixture
    fake.native[PID]["source"] = "generated" if origin == "generated" else "feed"
    if origin == "generated":
        transcript_id = "c3a1e0de-0b6f-4a53-8f55-0d8e2f1b7a90"
        fake.listing[PID] = [{"id": transcript_id, "source": "generated", "status": "parsed"}]
        segments = fake.native[PID]["segments"]
        fake.files[(PID, transcript_id)] = (vtt([segment["text"] for segment in segments],
            [(segment["startMs"]/1000, segment["endMs"]/1000) for segment in segments]), "text/vtt")
    else:
        fake.listing.clear()

native_fixture = fake.native[PID]
set_source("generated")

app = create_app("http://podfetch.test", STATE / "browser.db",
                 transport=httpx.MockTransport(fake))
app.dependency_overrides[get_llm] = lambda: None
preferences = dict(enabled=True, skipSponsor=True, skipSelfpromo=True, skipInteraction=False,
    skipIntro=False, skipOutro=False, skipPreview=False, skipFiller=False, skipMusicOfftopic=False)
calls = []

@app.middleware("http")
async def record(request, call_next):
    calls.append({"method": request.method, "path": request.url.path})
    return await call_next(request)

@app.get("/ui/index.html")
def index():
    config = {"serverUrl": ORIGIN, "basicAuth": False, "oidcConfigured": False}
    built = UI_DIST / "index.html"
    content = built.read_text()
    span = '<span id="config" data-config="' + html.escape(json.dumps(config), quote=True) + '"></span>'
    return HTMLResponse(content.replace('</body>', span + '</body>'))

@app.get("/ui/{path:path}")
def ui(path: str):
    root = UI_DIST
    target = (root / path).resolve()
    if target.is_relative_to(root) and target.is_file():
        return FileResponse(target)
    return index()

@app.get("/verify/owner")
def owner():
    return {"owner": os.environ["VERIFY_OWNER"]}

@app.put("/verify/source")
async def change_source(request: Request):
    data = await request.json()
    origin = data.get("origin")
    if origin not in ("generated", "feed", "mismatch", "missing"):
        return JSONResponse({"detail": "Unknown fixture source"}, status_code=422)
    set_source(origin)
    return {"origin": origin}

@app.get("/verify/requests")
def requests():
    return {"http": calls, "podfetch": fake.requests}

@app.api_route("/api/{path:path}", methods=["GET", "PUT", "POST"])
async def upstream(path: str, request: Request):
    if path == "v1/settings/sponsorblock":
        if request.method == "PUT":
            preferences.update(await request.json())
        return preferences
    if path == "v1/users/me":
        return {"username": "fixture", "role": "admin", "apiKey": "", "locale": "en"}
    if path.startswith("v1/episodes/") or path.startswith("v1/podcasts/episodes/"):
        response = fake(httpx.Request(request.method, "http://podfetch.test/api/" + path))
        return Response(response.content, status_code=response.status_code,
                        media_type=response.headers.get("content-type", "application/json"))
    if path == "v1/playlist":
        return []
    if request.method == "GET" and path.startswith("v1/podcasts/episode/"):
        return {"total_time": 120}
    if request.method == "GET":
        return JSONResponse([], status_code=200)
    return Response(status_code=204)

@app.get("/podcasts/native/lesson.wav")
def audio(request: Request):
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * (120 * 8000))
    content = out.getvalue()
    headers = {"Accept-Ranges": "bytes"}
    requested = request.headers.get("range", "")
    if requested.startswith("bytes="):
        start, end = requested.removeprefix("bytes=").split("-", 1)
        start = int(start or "0")
        end = min(int(end) if end else len(content) - 1, len(content) - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        return Response(content[start:end + 1], status_code=206, media_type="audio/wav", headers=headers)
    return Response(content, media_type="audio/wav", headers=headers)
