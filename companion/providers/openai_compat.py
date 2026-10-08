"""One provider class for every OpenAI-compatible API: OpenAI, Groq, OpenRouter, Ollama, or your own.

complete_json   POST {base_url}/chat/completions. It asks for the answer in the strongest JSON
                mode the provider accepts: 1. response_format json_schema, 2. json_object with the
                schema in the system message, 3. no response_format, schema in the system message.
                A provider that refuses a mode (HTTP 400 about response_format) gets the next one,
                and that is remembered for its address and model. The answer is always checked
                against the schema here (`jsonschema.errors`), whatever the provider promised.
list_models     GET {base_url}/models, without the models that can't chat (speech, embeddings, …).
transcribe_audio  POST {base_url}/audio/transcriptions, only on OpenAICompatLLMWithSpeech (the
                OpenAI and Groq presets), so `getattr(llm, "transcribe_audio", None)` finds it
                exactly when the provider can do it. `transcribe_words` is the same call asking
                for each word's time too (the listen-back check of a cut).

Limits: 60 s per request and answers of at most 256 KB. A 429 (a rate limit: free tiers refuse
the next part of a long text until their per-minute budget refills) is waited out: after
Retry-After, else the x-ratelimit-reset-* time (Groq, OpenAI), else 2, 4, 8, 16 s; up to 4 more
tries and 150 s of waiting per call, and past that the call fails with the usual message. A 5xx
is tried once more, after at most 60 s, and so is a generation that failed the provider's own JSON
check (Groq's HTTP 400 json_validate_failed). Redirects are not followed.
Every request goes only to an address that `engine.net.blocked_reason(allow_private=True)`
accepts: localhost and the LAN are fine (Ollama), cloud metadata is refused, and plain http to
a public address is refused, so a key never crosses the internet unencrypted. The connection
is pinned to the address that was checked, so a second DNS answer can't redirect it.

Errors are `LLMError` with fixed messages. A provider's own error text is never shown or
logged, because some providers echo part of the key. Prompts and keys are never logged.
"""
from __future__ import annotations

import email.utils
import ipaddress
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from ..engine.net import blocked_reason, system_resolver
from ..llm import LLMError
from . import jsonschema

log = logging.getLogger(__name__)

TIMEOUT = 60.0
MAX_REPLY_BYTES = 256 * 1024
RATE_RETRIES = 4                 # a 429 is waited out and tried again up to 4 times ...
RATE_WAIT_CAP = 150.0            # ... while the waits for one call add up to at most 150 s
RETRY_AFTER_MAX = 60.0           # a 5xx is tried once more, after at most 60 s
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MODES = ("json_schema", "json_object", "prompt")
_WORKING_MODE: dict[tuple[str, str], int] = {}     # (base_url, model) -> index in MODES that works

Resolver = Callable[[str, int], list[str]]

KEY_REJECTED = "The key was rejected."
MODEL_NOT_FOUND = "Model not found. Check the model id, and that the base URL ends in /v1."
TOO_MUCH = "Too much text for this provider. Lower the input limit or pick another model."
DAILY_LIMIT = "You have used up this provider's daily limit. Try again tomorrow, or pick another model."
TIMED_OUT = "The AI provider took longer than 60 seconds. Try again, or pick a faster model."
TOO_BIG = "The AI provider's answer was larger than 256 KB. Try again, or pick another model."
NOT_COMPATIBLE = ("That address answered, but not like an OpenAI-compatible API. "
                  "Check the base URL (it usually ends in /v1).")
NOT_JSON = "The AI's answer wasn't valid JSON. Try again, or pick another model."
WRONG_SHAPE = "The AI's answer didn't have the expected fields. Try again, or pick another model."
CUT_OFF = "The AI's answer was cut off before it was complete. Try again, or pick another model."
NO_JSON_MODE = "This model can't answer in JSON. Pick another model."

_NOT_CHAT = re.compile(r"whisper|transcri|tts|orpheus|speech|audio|realtime|embed|moderation|guard|"
                       r"dall-e|image|sora|search|davinci|babbage|rerank", re.I)
_FORMAT = re.compile(r"response.?format|json.?schema|json.?object|structured output", re.I)
_CONTEXT = re.compile(r"context.?length|context window|maximum context|too many tokens|reduce the length"
                      r"|too large|too long", re.I)
_DAILY = re.compile(r"per day|daily|\bTPD\b|\bRPD\b", re.I)
_GENERATION = re.compile(r"json_validate_failed|failed_generation|failed to generate json", re.I)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|us|µs|ns|h|m|s)")
_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}


class AddressError(LLMError):
    """The base URL points somewhere the server must not send a request (or not in the clear)."""


class _FormatRefused(Exception):
    """The provider refused this response_format; try the next JSON mode."""


def checked_addresses(url: httpx.URL, resolver: Resolver | None = None) -> list[str]:
    """The addresses of the URL's host, each checked against the rules above. Raises LLMError."""
    host = url.host
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        found = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            found = list(dict.fromkeys(str(ip).split("%", 1)[0] for ip in (resolver or system_resolver)(host, port)))
        except (OSError, UnicodeError):
            raise LLMError(f"Couldn't find the server {host}. Check the base URL.") from None
    if not found:
        raise LLMError(f"Couldn't find the server {host}. Check the base URL.")
    for ip in found:
        reason = blocked_reason(ip, allow_private=True)
        if reason:
            raise AddressError(f"That address is blocked: {host} is {reason}.")
        if url.scheme == "http" and blocked_reason(ip, allow_private=False) is None:
            raise AddressError(f"Use https:// for {host}. Over plain http your key would cross the internet unencrypted.")
    return found


def address_problem(base_url: str, resolver: Resolver | None = None) -> str | None:
    """Why this base URL must be refused now, or None. A server that can't be found yet (Ollama
    not started) is not a reason; the check runs again before every request."""
    try:
        checked_addresses(httpx.URL(base_url), resolver)
    except AddressError as exc:
        return str(exc)
    except (LLMError, httpx.InvalidURL, ValueError):
        return None
    return None


class GuardedTransport(httpx.BaseTransport):
    """Sends each request to a checked address of its host, with the real Host header and TLS name."""

    def __init__(self, inner: httpx.BaseTransport | None = None, resolver: Resolver | None = None):
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.resolver = resolver

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url, last = request.url, None
        for ip in checked_addresses(url, self.resolver):
            extensions = dict(request.extensions)
            if url.scheme == "https" and ip != url.host:
                extensions["sni_hostname"] = url.host
            pinned = httpx.Request(request.method, url.copy_with(host=ip), headers=request.headers,
                                   stream=request.stream, extensions=extensions)
            try:
                return self.inner.handle_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = exc
        raise last or httpx.ConnectError("No address to connect to.", request=request)

    def close(self) -> None:
        self.inner.close()


def extract_json(text: str) -> dict[str, Any] | None:
    """The JSON object in a model's reply: plain, in a ``` fence, or after a <think> block."""
    text = _FENCE.sub("", _THINK.sub("", text or "").strip()).strip()
    candidates = [text]
    if "{" in text:
        candidates.append(text[text.find("{"):text.rfind("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, RecursionError):      # RecursionError: absurdly deep nesting
            continue
        if isinstance(value, dict):
            return value
    return None


def _retry_after(headers: httpx.Headers) -> float | None:
    """Seconds in a Retry-After header (a number or an HTTP date), or None."""
    value = headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _duration(value: str) -> float | None:
    """Seconds in a rate-limit reset header: "7.66s", "1m26.4s", "150ms", or a plain number."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        parts = _DURATION.findall(value)
        if not parts or "".join(number + unit for number, unit in parts) != value:
            return None
        seconds = sum(float(number) * _UNITS[unit] for number, unit in parts)
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def rate_limit_wait(headers: httpx.Headers) -> float | None:
    """How long a 429 asks to wait: Retry-After, else the reset time of the exhausted budget
    (x-ratelimit-reset-tokens or -requests, as Groq and OpenAI send them), else None."""
    retry = _retry_after(headers)
    if retry is not None:
        return retry
    names = ["x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"]
    if headers.get("x-ratelimit-remaining-requests", "").strip() == "0":
        names.reverse()
    for name in names:
        reset = _duration(headers.get(name, ""))
        if reset is not None:
            return reset + 0.25          # the exact reset moment can still be refused
    return None


def _error_text(body: bytes) -> tuple[str, str]:
    """(param, lowercase type/code/message) of an OpenAI-style error body, for matching only."""
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError, RecursionError):
        return "", body[:2000].decode("utf-8", "replace").lower()
    if isinstance(error, str):
        return "", error.lower()
    if not isinstance(error, dict):
        return "", ""
    parts = [str(error.get(key) or "") for key in ("type", "code", "message")]
    return str(error.get("param") or ""), " ".join(parts).lower()


class OpenAICompatLLM:
    """The `LLM` protocol (companion/llm.py) for an OpenAI-compatible API, plus list_models."""

    def __init__(self, *, base_url: str, model: str = "", api_key: str | None = None, provider: str = "custom",
                 max_input_chars: int = 60_000, token_param: str = "max_tokens", speech_model: str | None = None,
                 transport: httpx.BaseTransport | None = None, resolver: Resolver | None = None,
                 timeout: float = TIMEOUT, sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider
        self.max_input_chars = max_input_chars
        self.token_param = token_param
        self.speech_model = speech_model
        self.mode: str | None = None          # the JSON mode of the last answer
        self._key = api_key or None
        self._transport, self._resolver, self._timeout, self._sleep = transport, resolver, timeout, sleep

    def __repr__(self) -> str:                # never shows the key
        return f"OpenAICompatLLM(provider={self.provider!r}, base_url={self.base_url!r}, model={self.model!r})"

    # ── the LLM protocol ─────────────────────────────────────────────────────

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any],
                      max_tokens: int = 1024) -> dict[str, Any]:
        if not self.model:
            raise LLMError("Pick a model in Settings → AI.")
        try:
            wire = jsonschema.inline_refs(schema)
        except jsonschema.SchemaError:
            wire = schema
        remembered = (self.base_url, self.model)
        for index in range(_WORKING_MODE.get(remembered, 0), len(MODES)):
            try:
                reply = self._send("POST", "/chat/completions",
                                   json=self._chat(MODES[index], system, user, wire, max_tokens))
            except _FormatRefused:
                _WORKING_MODE[remembered] = index + 1
                continue
            self.mode = MODES[index]
            return self._answer(reply, schema)
        raise LLMError(NO_JSON_MODE)

    def _chat(self, mode: str, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        if mode != "json_schema":
            system = (f"{system}\n\nReply with one JSON object and nothing else. It must match this JSON Schema:\n"
                      f"{json.dumps(schema, separators=(',', ':'))}")
        body: dict[str, Any] = {"model": self.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}], self.token_param: max_tokens}
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def _answer(self, reply: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        try:
            choice = reply["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(NOT_COMPATIBLE) from None
        if not isinstance(message, dict):
            raise LLMError(NOT_COMPATIBLE)
        if message.get("refusal"):
            raise LLMError("The AI declined to answer this request.")
        content = message.get("content")
        if isinstance(content, list):             # some servers send content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        value = extract_json(content if isinstance(content, str) else "")
        problems = jsonschema.errors(value, schema) if value is not None else ["no JSON"]
        if problems and choice.get("finish_reason") == "length":
            raise LLMError(CUT_OFF)
        if value is None:
            raise LLMError(NOT_JSON)
        if problems:
            raise LLMError(WRONG_SHAPE)
        return value

    # ── models and speech ────────────────────────────────────────────────────

    def list_models(self) -> list[dict[str, Any]]:
        """[{id, context}] of the models that can chat, sorted by id."""
        items = self._send("GET", "/models").get("data")
        if not isinstance(items, list):
            raise LLMError(NOT_COMPATIBLE)
        models: dict[str, dict[str, Any]] = {}
        for item in items:
            model_id = item.get("id") if isinstance(item, dict) else None
            if (not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200
                    or _NOT_CHAT.search(model_id) or item.get("active") is False):
                continue
            context = item.get("context_window") or item.get("context_length")
            models[model_id] = {"id": model_id, "context": context if isinstance(context, int) and context > 0 else None}
        return sorted(models.values(), key=lambda model: model["id"].lower())[:1000]

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _send(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        host = httpx.URL(self.base_url).host
        rate_retries = server_retries = generation_retries = 0
        waited = 0.0
        while True:
            status, body, reply_headers = self._once(method, self.base_url + path, headers, host, kwargs)
            if 200 <= status < 300:
                try:
                    data = json.loads(body)
                except (ValueError, RecursionError):
                    raise LLMError(NOT_COMPATIBLE) from None
                if not isinstance(data, dict):
                    raise LLMError(NOT_COMPATIBLE)
                return data
            wait = None
            if status == 429 and rate_retries < RATE_RETRIES:
                hint = rate_limit_wait(reply_headers)
                wait = 2.0 * 2 ** rate_retries if hint is None else hint
                if waited + wait > RATE_WAIT_CAP:
                    wait = None                      # the budget won't refill in time: say so now
                else:
                    rate_retries += 1
            elif status == 400 and generation_retries < 1 and _GENERATION.search(body[:8192].decode("utf-8", "replace")):
                log.info("AI provider %s couldn't produce valid JSON this time (HTTP 400); trying once more", host)
                generation_retries += 1
                continue                             # the next generation usually passes
            elif status >= 500 and server_retries < 1:
                hint = _retry_after(reply_headers)
                wait = 2.0 if hint is None else hint
                if wait > RETRY_AFTER_MAX:
                    wait = None
                else:
                    server_retries += 1
            if wait is None:
                log.warning("AI provider %s answered HTTP %s to %s %s", host, status, method, path)
                raise self._failure(status, body, path, kwargs)
            log.info("AI provider %s answered HTTP %s; waiting %.1f s before trying again", host, status, wait)
            waited += wait
            self._sleep(wait)

    def _once(self, method: str, url: str, headers: dict[str, str], host: str,
              kwargs: dict[str, Any]) -> tuple[int, bytes, httpx.Headers]:
        deadline = time.monotonic() + self._timeout
        try:
            with httpx.Client(transport=GuardedTransport(self._transport, self._resolver), trust_env=False,
                              follow_redirects=False, timeout=httpx.Timeout(self._timeout, connect=10)) as client:
                with client.stream(method, url, headers=headers, **kwargs) as response:
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > MAX_REPLY_BYTES:
                        raise LLMError(TOO_BIG)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_REPLY_BYTES:
                            raise LLMError(TOO_BIG)
                        if time.monotonic() > deadline:
                            raise LLMError(TIMED_OUT)
                    return response.status_code, bytes(body), response.headers
        except httpx.TimeoutException:
            raise LLMError(TIMED_OUT) from None
        except httpx.HTTPError:
            raise LLMError(f"Couldn't reach the AI provider at {host}. Check the base URL, "
                           "and that the server is running.") from None

    def _failure(self, status: int, body: bytes, path: str, kwargs: dict[str, Any]) -> Exception:
        param, text = _error_text(body)
        if status == 400 and _GENERATION.search(body[:8192].decode("utf-8", "replace")):
            return LLMError(NOT_JSON)                # the model's output, not the request, was wrong
        asked_format = "response_format" in (kwargs.get("json") or {})
        if status in (400, 422) and asked_format and (param == "response_format" or _FORMAT.search(text)):
            return _FormatRefused()
        if status == 401:
            return LLMError(KEY_REJECTED)
        if status == 403:
            return LLMError("The provider refused this request (HTTP 403). Check what your key is allowed to use.")
        if status == 404:
            if path == "/models":
                return LLMError("No model list at this address. Check the base URL (it usually ends in /v1).")
            if path == "/audio/transcriptions":
                return LLMError("This AI provider can't make transcripts.")
            return LLMError(MODEL_NOT_FOUND)
        if status == 413:
            return LLMError(TOO_MUCH)
        if status == 429:
            return LLMError(DAILY_LIMIT if _DAILY.search(text) else TOO_MUCH)
        if status in (400, 422):
            if _CONTEXT.search(text):
                return LLMError(TOO_MUCH)
            return LLMError("The provider refused the request (HTTP 400). Check the model and the base URL.")
        if 300 <= status < 400:
            return LLMError("The provider's address redirects somewhere else. Use the final address as the base URL.")
        if status >= 500:
            return LLMError(f"The AI provider had a problem (HTTP {status}). Try again in a minute.")
        return LLMError(f"The AI provider answered HTTP {status}.")


_AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4"}


class OpenAICompatLLMWithSpeech(OpenAICompatLLM):
    """A provider that also offers POST /audio/transcriptions with `speech_model`."""

    def transcribe_audio(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """{text, duration, segments: [{start, end, text}]} for a short clip, times in seconds from
        the clip's start. Send mp3, wav or flac: Groq stretches opus timings 1.74x
        (a timing incident documented by the original CLI). Raises LLMError with a message to show."""
        return self._transcribe(path, words=False)

    def transcribe_words(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """``transcribe_audio`` plus ``words: [{word, start, end}]``, each word's own time: the
        listen-back check of a cut compares words at its edges. Both presets' models (Groq's
        whisper-large-v3-turbo, OpenAI's whisper-1) give word times in verbose_json; a model that
        doesn't returns no ``words``, and callers fall back to the segments."""
        return self._transcribe(path, words=True)

    def _transcribe(self, path: str | os.PathLike[str], *, words: bool) -> dict[str, Any]:
        name = os.path.basename(os.fspath(path))
        try:
            with open(path, "rb") as handle:
                audio = handle.read(MAX_AUDIO_BYTES + 1)
        except OSError:
            raise LLMError("The audio clip couldn't be read.") from None
        if not audio:
            raise LLMError("The audio clip is empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise LLMError("The audio clip is larger than 25 MB.")
        if audio[:4] in (b"OggS", b"\x1a\x45\xdf\xa3") or name.lower().endswith((".opus", ".ogg", ".oga", ".webm")):
            raise LLMError("Send the clip as mp3, wav or flac. Opus audio comes back with the wrong timings.")
        kind = _AUDIO_TYPES.get(os.path.splitext(name)[1].lower(), "audio/mpeg")
        form: dict[str, Any] = {"model": self.speech_model, "response_format": "verbose_json", "temperature": "0"}
        if words:
            form["timestamp_granularities[]"] = ["word", "segment"]
        reply = self._send("POST", "/audio/transcriptions", files={"file": (name, audio, kind)}, data=form)
        segments = []
        for segment in reply.get("segments") or []:
            try:
                start, end, text = float(segment["start"]), float(segment["end"]), str(segment["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= start <= end and text:
                segments.append({"start": start, "end": end, "text": text})
        try:
            duration = float(reply["duration"])
        except (KeyError, TypeError, ValueError):
            duration = None
        out = {"text": str(reply.get("text") or "").strip(), "duration": duration, "segments": segments}
        if words:
            heard = []
            for word in reply.get("words") or []:
                try:
                    start, end, text = float(word["start"]), float(word["end"]), str(word["word"]).strip()
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= start <= end and text and math.isfinite(start) and math.isfinite(end):
                    heard.append({"word": text, "start": start, "end": end})
            out["words"] = heard
        return out
