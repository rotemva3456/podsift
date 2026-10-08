"""The user's own AI provider, behind one small interface.

Routes ask for it with ``llm: LLM | None = Depends(get_llm)``. ``None`` means no provider is set
up: the feature must still work in its non-AI form, or tell the user "Connect AI in Settings → AI".
Tests replace the dependency with a fake:

    app.dependency_overrides[get_llm] = lambda: FakeLLM([{"summary": "..."}])

Transcripts and feeds are untrusted text. Put them in ``user`` marked as evidence, never as
instructions, and have the model return segment ids (never times); the server turns ids into times.

Split long input into chunks of at most ``input_limit(llm)`` characters (Settings → AI → input
limit; Groq's free tier refuses much more than 20,000). A failed call raises ``LLMError`` whose
message can be shown as is ("The key was rejected.", "Too much text for this provider. …").

Speech: a provider with ``POST /audio/transcriptions`` (the OpenAI and Groq presets) also has
``transcribe_audio(path) -> {text, duration, segments}``; ``getattr(llm, "transcribe_audio", None)``
finds it exactly when the provider can do it. It raises ``LLMError`` too.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from fastapi import Depends, Request

from .deps import require_login

log = logging.getLogger(__name__)
DEFAULT_MAX_INPUT_CHARS = 60_000


class LLMError(Exception):
    """A provider failure with a message that is safe to show to the user."""


@runtime_checkable
class LLM(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict[str, Any],
                      max_tokens: int = 1024) -> dict[str, Any]:
        """Return one JSON object that matches ``schema``, or raise ``LLMError``."""
        ...


def get_llm(request: Request = None, _login: None = Depends(require_login)) -> LLM | None:  # type: ignore[assignment]
    """FastAPI dependency: the provider saved in Settings → AI, or None when AI isn't set up.

    It needs a login itself (``require_login``), so no route can spend the owner's key without one.
    The env vars LLM_BASE_URL, LLM_API_KEY and LLM_MODEL override the saved settings; empty values
    count as unset. Called directly as ``get_llm()`` (no request), only those env vars count.
    """
    from .providers import store

    state = getattr(getattr(request, "app", None), "state", None)
    settings = getattr(state, "settings", None)
    try:
        config = store.load(store.data_folder(settings.database) if settings is not None else None)
    except (OSError, ValueError) as exc:
        log.warning("AI settings couldn't be read (%s); AI stays off.", type(exc).__name__)
        return None
    # Tests put an httpx.MockTransport and a fake resolver on app.state; the app never does.
    return store.build_llm(config, transport=getattr(state, "llm_transport", None),
                           resolver=getattr(state, "llm_resolver", None))


def input_limit(llm: LLM | None) -> int:
    """How many characters of transcript one ``complete_json`` call may carry."""
    value = getattr(llm, "max_input_chars", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_MAX_INPUT_CHARS


Response = dict[str, Any] | Exception | Callable[..., dict[str, Any]]


class FakeLLM:
    """A test double: returns ``responses`` in order and records every call in ``calls``.

    A response can be a dict (returned as a copy), an exception (raised), or a function called
    with the same keyword arguments as ``complete_json``. It never touches the network.
    """

    def __init__(self, responses: Iterable[Response] = ()):
        self.responses: list[Response] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any],
                      max_tokens: int = 1024) -> dict[str, Any]:
        call = {"system": system, "user": user, "schema": schema, "max_tokens": max_tokens}
        self.calls.append(call)
        if not self.responses:
            raise AssertionError(f"FakeLLM got call {len(self.calls)} but has no response left for it.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(**call)
        return copy.deepcopy(response)
