"""Settings → AI: the user's own OpenAI-compatible provider.

GET  /companion/settings/ai          the settings object; the key itself never comes back
PUT  /companion/settings/ai          {provider, base_url, model, max_input_chars, api_key?, clear_key?}
POST /companion/settings/ai/test     one tiny JSON answer, with the saved settings or unsaved ones in the body
GET  /companion/settings/ai/models   the provider's chat models (POST: for unsaved settings in the body)

AI settings belong to the whole install (one key for everyone who can log in). An unsaved key in a
Test or model-list body is used for that one request and never stored.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..deps import Settings, current_user, get_settings
from ..llm import LLMError
from ..providers import store
from ..runtime import require_operator

router = APIRouter(dependencies=[Depends(require_operator)])

TEST_SYSTEM = "You check that an AI connection works. Reply with JSON only."
TEST_USER = 'Reply with {"ok": true}.'
TEST_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
               "additionalProperties": False}


def _hooks(request: Request) -> dict[str, Any]:
    """Tests put an httpx.MockTransport and a fake DNS resolver on app.state; the app never does."""
    state = request.app.state
    return {"transport": getattr(state, "llm_transport", None), "resolver": getattr(state, "llm_resolver", None)}


def _config(settings: Settings, body: Any = None, *, use_body: bool = False) -> store.AIConfig:
    folder = store.data_folder(settings.database)
    try:
        return store.draft(folder, body) if use_body else store.load(folder)
    except store.SettingsError as exc:
        raise HTTPException(422, str(exc)) from None


def _missing(config: store.AIConfig) -> tuple[str, str] | None:
    if not config.base_url:
        return "base_url", "Enter the provider's API address first."
    if config.preset.needs_key and not config.api_key:
        return "key", f"Paste your {config.preset.label} key first."
    return None


@router.get("/companion/settings/ai")
def read_ai_settings(settings: Settings = Depends(get_settings), user: str = Depends(current_user)):
    return store.public(_config(settings))


@router.put("/companion/settings/ai")
def save_ai_settings(request: Request, body: Any = Body(None), settings: Settings = Depends(get_settings),
                     user: str = Depends(current_user)):
    try:
        config = store.save(store.data_folder(settings.database), body, resolver=_hooks(request)["resolver"])
    except store.SettingsError as exc:
        raise HTTPException(422, str(exc)) from None
    return store.public(config)


@router.post("/companion/settings/ai/test")
def test_ai_settings(request: Request, body: Any = Body(None), settings: Settings = Depends(get_settings),
                     user: str = Depends(current_user)):
    config = _config(settings, body, use_body=body is not None)
    missing = _missing(config)
    if missing:
        return {"ok": False, "needs": missing[0], "message": missing[1]}
    llm = store.build_llm(config, need_model=False, **_hooks(request))
    started = time.monotonic()
    try:
        if not config.model:
            count = len(llm.list_models())
            return {"ok": False, "needs": "model",
                    "message": f"The key works and the provider lists {count} models. Now pick a model."}
        llm.complete_json(system=TEST_SYSTEM, user=TEST_USER, schema=TEST_SCHEMA, max_tokens=400)
    except LLMError as exc:
        return {"ok": False, "needs": None, "message": str(exc)}
    seconds = round(time.monotonic() - started, 1)
    return {"ok": True, "needs": None, "seconds": seconds, "mode": llm.mode,
            "message": f"Connected. {config.model} answered in {seconds:.1f} s."}


def _models(request: Request, config: store.AIConfig) -> dict[str, Any]:
    missing = _missing(config)
    if missing:
        raise HTTPException(409, missing[1])
    try:
        models = store.build_llm(config, need_model=False, **_hooks(request)).list_models()
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from None
    ids = {model["id"] for model in models}
    return {"models": models, "suggested": next((m for m in config.preset.suggested_models if m in ids), None)}


@router.get("/companion/settings/ai/models")
def list_ai_models(request: Request, settings: Settings = Depends(get_settings), user: str = Depends(current_user)):
    return _models(request, _config(settings))


@router.post("/companion/settings/ai/models")
def list_ai_models_for(request: Request, body: Any = Body(None), settings: Settings = Depends(get_settings),
                       user: str = Depends(current_user)):
    return _models(request, _config(settings, body, use_body=True))
