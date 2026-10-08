"""POST /companion/speech/transcribe and GET /companion/speech/status."""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion.llm import LLMError, get_llm
from companion.routes import speech
from companion.routes.speech import get_speech
from companion.server import create_app

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe are not installed")


def recording(path: Path, seconds: float = 2.0) -> bytes:
    """A short webm/opus clip, the shape a browser's MediaRecorder makes - never sent to an AI
    provider directly (see providers/openai_compat.py)."""
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}:sample_rate=48000",
                    "-c:a", "libopus", "-f", "webm", str(path)], check=True)
    return path.read_bytes()


class FakeSpeech:
    """Same interface as the AI provider's ``transcribe_audio(path)``. Records what it was given
    so a test can prove the clip was converted (never opus) before it arrived."""

    def __init__(self, text: str = "Remember to check the BGP config.", duration: float = 2.0, fail=None):
        self.text, self.duration, self.fail = text, duration, fail
        self.calls: list[bytes] = []

    def transcribe_audio(self, path):
        head = Path(path).read_bytes()[:3]
        self.calls.append(head)
        if self.fail:
            raise self.fail
        assert head == b"ID3" or head[:1] == b"\xff", "the clip must be mp3, never opus, by the time the provider sees it"
        return {"text": self.text, "duration": self.duration, "segments": []}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test here may open a network connection."""
    def refuse(name):
        def blocked(*args, **kwargs):
            raise AssertionError(f"a test tried the network ({name})")
        return blocked
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse(name))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))


@pytest.fixture
def app(tmp_path):
    def handle(request):
        return httpx.Response(404)   # no PodFetch login endpoint: every caller is "default"
    application = create_app("http://podfetch.test", tmp_path / "companion.db",
                             transport=httpx.MockTransport(handle))
    application.dependency_overrides[get_llm] = lambda: None
    return application


def client_with(app, fake) -> TestClient:
    app.dependency_overrides[get_speech] = lambda: fake
    return TestClient(app)


@needs_ffmpeg
def test_transcribes_a_converted_clip(app, tmp_path):
    fake = FakeSpeech(text="Remember to check the BGP config.")
    client = client_with(app, fake)
    body = recording(tmp_path / "note.webm")
    response = client.post("/companion/speech/transcribe", content=body, headers={"content-type": "audio/webm"})
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "Remember to check the BGP config.", "duration": 2.0}
    assert fake.calls, "the provider was never called"


def test_no_speech_provider_is_409(app, tmp_path):
    client = client_with(app, None)
    body = b"anything, this must never reach ffmpeg"
    response = client.post("/companion/speech/transcribe", content=body, headers={"content-type": "audio/webm"})
    assert response.status_code == 409
    assert response.json()["detail"] == speech.NEED_SPEECH


def test_empty_body_is_422(app):
    client = client_with(app, FakeSpeech())
    response = client.post("/companion/speech/transcribe", content=b"", headers={"content-type": "audio/webm"})
    assert response.status_code == 422
    assert response.json()["detail"] == speech.EMPTY_RECORDING


def test_unreadable_audio_is_422(app):
    client = client_with(app, FakeSpeech())
    response = client.post("/companion/speech/transcribe", content=b"not audio at all",
                           headers={"content-type": "audio/webm"})
    assert response.status_code == 422
    assert response.json()["detail"] == speech.UNREADABLE


@needs_ffmpeg
def test_too_long_is_422(app, tmp_path):
    client = client_with(app, FakeSpeech())
    body = recording(tmp_path / "long.webm", seconds=speech.MAX_SECONDS + speech.DURATION_SLACK + 5)
    response = client.post("/companion/speech/transcribe", content=body, headers={"content-type": "audio/webm"})
    assert response.status_code == 422
    assert response.json()["detail"] == speech.TOO_LONG


def test_upload_over_the_byte_cap_is_413(app, monkeypatch):
    monkeypatch.setattr(speech, "MAX_UPLOAD_BYTES", 10)
    client = client_with(app, FakeSpeech())
    response = client.post("/companion/speech/transcribe", content=b"x" * 100, headers={"content-type": "audio/webm"})
    assert response.status_code == 413


@needs_ffmpeg
def test_no_speech_recognized_is_422(app, tmp_path):
    client = client_with(app, FakeSpeech(text="   "))
    body = recording(tmp_path / "silence.webm")
    response = client.post("/companion/speech/transcribe", content=body, headers={"content-type": "audio/webm"})
    assert response.status_code == 422
    assert response.json()["detail"] == speech.NO_SPEECH


@needs_ffmpeg
def test_provider_failure_is_502(app, tmp_path):
    client = client_with(app, FakeSpeech(fail=LLMError("The key was rejected.")))
    body = recording(tmp_path / "note.webm")
    response = client.post("/companion/speech/transcribe", content=body, headers={"content-type": "audio/webm"})
    assert response.status_code == 502
    assert response.json()["detail"] == "The key was rejected."


def test_status_reflects_whether_a_speech_provider_is_connected(app):
    assert client_with(app, None).get("/companion/speech/status").json() == {"configured": False}
    assert client_with(app, FakeSpeech()).get("/companion/speech/status").json() == {"configured": True}
