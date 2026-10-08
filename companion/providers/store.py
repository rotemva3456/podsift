"""Settings → AI, kept on the server in the data folder (the folder of COMPANION_DB; /data in Docker).

ai-settings.json     provider, base_url, model, max_input_chars and the sealed key   (mode 0600)
ai-settings.secret   32 random bytes that seal the key                               (mode 0600)

The key is sealed (an HMAC-SHA256 stream with a tag) so it never appears as plain text in a
file, a backup or a search of the data folder. Anyone who can read the whole data folder can
still recover it, so keep that folder private. Backup and restore copy both files.

A key is bound to the origin (scheme, host and port) it was saved for, and is only ever sent
there. Saving another origin as the base URL forgets the saved key, so nobody can redirect it
to their own server by editing the address.

The env vars LLM_BASE_URL, LLM_API_KEY and LLM_MODEL override the saved settings; an empty
value counts as unset. LLM_API_KEY is used only together with LLM_BASE_URL (the same rule).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import sealed
from .openai_compat import OpenAICompatLLM, OpenAICompatLLMWithSpeech, Resolver, address_problem
from .presets import DEFAULT_PROVIDER, PRESETS, Preset, preset_for_url

log = logging.getLogger(__name__)

SETTINGS_FILE = "ai-settings.json"
SECRET_FILE = "ai-settings.secret"
MIN_INPUT_CHARS, MAX_INPUT_CHARS = 1_000, 2_000_000
FIELDS = {"provider", "base_url", "model", "max_input_chars", "api_key", "clear_key"}
_WRITE = threading.Lock()
_WARNED: set[str] = set()


def _warn_once(message: str) -> None:
    """Settings are read on every AI request; say each problem once, not every time."""
    if message not in _WARNED:
        _WARNED.add(message)
        log.warning(message)


class SettingsError(ValueError):
    """The settings can't be used; the message says what to change (and never repeats the key)."""


@dataclass(frozen=True)
class AIConfig:
    provider: str
    base_url: str
    model: str
    max_input_chars: int
    api_key: str | None = field(default=None, repr=False)
    from_env: frozenset[str] = frozenset()     # "base_url", "api_key", "model"
    saved: bool = False
    problem: str | None = None

    @property
    def preset(self) -> Preset:
        return PRESETS.get(self.provider, PRESETS["custom"])

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and (self.api_key or not self.preset.needs_key))


def data_folder(database: Path | str) -> Path:
    return Path(database).expanduser().parent


def origin(base_url: str) -> str:
    url = httpx.URL(base_url)
    return f"{url.scheme}://{url.host}:{url.port or (443 if url.scheme == 'https' else 80)}".lower()


def normalize_base_url(value: Any) -> str:
    """The address as it is stored: http(s), no credentials, query or fragment, no trailing slash."""
    if not isinstance(value, str) or not value.strip():
        raise SettingsError("Enter the provider's API address (base URL).")
    value = value.strip()
    if len(value) > 500:
        raise SettingsError("That address is too long.")
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, TypeError, ValueError):
        raise SettingsError("That address isn't a valid URL.") from None
    if url.scheme not in ("http", "https") or not url.host:
        raise SettingsError("The address must start with https:// (or http:// on your own network).")
    if url.userinfo:
        raise SettingsError("Take the name and password out of the address. The key goes in the key field.")
    if url.query or url.fragment:
        raise SettingsError("Take the ? or # part out of the address.")
    path = url.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return f"{url.scheme}://{url.netloc.decode('ascii')}{path}"


# ── sealing ──────────────────────────────────────────────────────────────────
# The primitive itself lives in companion/sealed.py, shared with feeds.py: same HMAC stream
# and tag, one label ("ai-settings") per caller for domain separation. These two keep their own
# names and their `secret` argument first with no `label` parameter, since this module only ever
# means the AI key -- companion/test_sealed.py proves a value sealed before this split still opens.

LABEL = "ai-settings"


def seal(secret: bytes, key: str, key_origin: str) -> str:
    return sealed.seal(secret, LABEL, key, key_origin)


def unseal(secret: bytes, sealed_value: str) -> tuple[str, str] | None:
    """(key, origin), or None when the sealed text or the secret doesn't match."""
    return sealed.unseal(secret, LABEL, sealed_value)


# ── files ────────────────────────────────────────────────────────────────────

def _private_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically, readable only by this user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    os.chmod(path, 0o600)


def _secret(folder: Path, create: bool) -> bytes | None:
    return sealed.secret(folder, LABEL, create=create)


def _read(folder: Path | None) -> dict[str, Any]:
    path = folder / SETTINGS_FILE if folder else None
    if not path or not path.exists():
        return {}
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once(f"{path} can't be read; AI settings start from the defaults until they are saved again.")
        return {}
    return data if isinstance(data, dict) else {}


def _stored_key(folder: Path | None, stored: dict[str, Any]) -> tuple[str, str] | None:
    sealed = stored.get("key")
    if not folder or not isinstance(sealed, str):
        return None
    secret = _secret(folder, create=False)
    opened = unseal(secret, sealed) if secret else None
    if opened is None:
        _warn_once(f"The saved AI key in {folder / SETTINGS_FILE} can't be opened (was {SECRET_FILE} "
                   "changed or lost?). Paste the key again in Settings → AI.")
    return opened


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ if env is None else env
    return {name: (env.get(f"LLM_{name.upper()}") or "").strip() for name in ("base_url", "api_key", "model")}


# ── settings ─────────────────────────────────────────────────────────────────

def load(folder: Path | None, env: Mapping[str, str] | None = None) -> AIConfig:
    """The settings in use: the saved file (if any) with the env vars on top."""
    stored = _read(folder)
    return _effective(stored, _stored_key(folder, stored), _env(env))


def _effective(stored: dict[str, Any], key: tuple[str, str] | None, env: dict[str, str]) -> AIConfig:
    provider = stored.get("provider") if stored.get("provider") in PRESETS else DEFAULT_PROVIDER
    base_url = stored.get("base_url") if isinstance(stored.get("base_url"), str) else PRESETS[provider].base_url
    model = stored.get("model") if isinstance(stored.get("model"), str) else ""
    from_env: set[str] = set()
    problem = None
    if env["base_url"]:
        try:
            base_url = normalize_base_url(env["base_url"])
            provider = preset_for_url(base_url).id
            from_env.add("base_url")
        except SettingsError as exc:
            problem = f"LLM_BASE_URL on the server can't be used: {exc}"
    if env["api_key"]:
        if "base_url" in from_env:
            key = (env["api_key"], origin(base_url))
            from_env.add("api_key")
        elif problem is None:
            problem = "LLM_API_KEY is set on the server, but LLM_BASE_URL isn't. Set both in .env, or neither."
    if env["model"]:
        model = env["model"]
        from_env.add("model")
    limit = stored.get("max_input_chars")
    if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_INPUT_CHARS <= limit <= MAX_INPUT_CHARS:
        limit = PRESETS[provider].max_input_chars
    api_key = key[0] if key and base_url and _same_origin(key[1], base_url) else None
    return AIConfig(provider, base_url, model, limit, api_key, frozenset(from_env), bool(stored), problem)


def _same_origin(key_origin: str, base_url: str) -> bool:
    try:
        return key_origin == origin(base_url)
    except (httpx.InvalidURL, ValueError):
        return False


def _changes(folder: Path | None, body: Any, env: Mapping[str, str] | None
             ) -> tuple[AIConfig, dict[str, Any], tuple[str, str] | None]:
    """Apply a PUT or Test body: (the settings it gives, the file content, the (key, origin) to seal)."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise SettingsError("Send the settings as a JSON object.")
    if set(body) - FIELDS:
        raise SettingsError("The settings contain a field this app doesn't know.")
    variables = _env(env)
    stored = _read(folder)
    key = _stored_key(folder, stored)
    current = _effective(stored, key, variables)
    locked = current.from_env

    provider = body.get("provider", current.provider)
    if "base_url" in locked:
        if provider != current.provider or body.get("base_url") not in (None, "", current.base_url):
            raise SettingsError("The address is set on the server with LLM_BASE_URL. Change it in .env.")
        provider = stored.get("provider") if stored.get("provider") in PRESETS else current.provider
        base_url = stored.get("base_url") if isinstance(stored.get("base_url"), str) else current.base_url
        same = True
    else:
        if provider not in PRESETS:
            raise SettingsError("Pick a provider: OpenAI, Groq, OpenRouter, Ollama or Custom.")
        same = provider == current.provider
        base_url = normalize_base_url(body.get("base_url") or (current.base_url if same else PRESETS[provider].base_url))

    model = body.get("model", current.model if same else "")
    if "model" in locked:
        if model not in (None, "", current.model):
            raise SettingsError("The model is set on the server with LLM_MODEL. Change it in .env.")
        model = stored.get("model") if isinstance(stored.get("model"), str) else ""
    model = "" if model is None else model
    if not isinstance(model, str) or len(model.strip()) > 200 or any(ord(c) < 32 for c in model):
        raise SettingsError("That model id doesn't look right. Pick one from the list.")

    limit = body.get("max_input_chars", current.max_input_chars if same else PRESETS[provider].max_input_chars)
    if isinstance(limit, float) and limit.is_integer():
        limit = int(limit)
    if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_INPUT_CHARS <= limit <= MAX_INPUT_CHARS:
        raise SettingsError(f"The input limit must be a whole number from {MIN_INPUT_CHARS:,} to {MAX_INPUT_CHARS:,}.")

    new_key = body.get("api_key")
    if new_key is not None and not isinstance(new_key, str):
        raise SettingsError("That key doesn't look right. Paste it again.")
    new_key = (new_key or "").strip() or None
    if new_key and "api_key" in locked:
        raise SettingsError("The key is set on the server with LLM_API_KEY. Change it in .env.")
    if new_key and (len(new_key) > 1000 or any(not 33 <= ord(c) <= 126 for c in new_key)):
        raise SettingsError("That key doesn't look right. Paste it again.")

    # A key is only ever sent to the origin it was saved for: the address in use after this save.
    target = origin(current.base_url if "base_url" in locked else base_url)
    if new_key:
        file_key: tuple[str, str] | None = (new_key, target)
    elif body.get("clear_key") is True:
        file_key = None
    else:
        file_key = key if key and key[1] == target else None
    content: dict[str, Any] = {"version": 1, "provider": provider, "base_url": base_url,
                               "model": model.strip(), "max_input_chars": limit}
    return _effective(content, file_key, variables), content, file_key


def draft(folder: Path | None, body: Any, env: Mapping[str, str] | None = None) -> AIConfig:
    """The settings a PUT with this body would give, without saving (for Test and the model list)."""
    return _changes(folder, body, env)[0]


def save(folder: Path, body: Any, env: Mapping[str, str] | None = None,
         resolver: Resolver | None = None) -> AIConfig:
    """Check and save a PUT body; returns the settings now in use."""
    with _WRITE:
        config, content, file_key = _changes(folder, body, env)
        if "base_url" not in config.from_env:
            problem = address_problem(content["base_url"], resolver)
            if problem:
                raise SettingsError(problem)
        if file_key:
            secret = _secret(folder, create=True)
            if secret is None:
                raise SettingsError(f"The key can't be saved: {folder / SECRET_FILE} can't be read.")
            content["key"] = seal(secret, *file_key)
        _private_write(folder / SETTINGS_FILE, (json.dumps(content, indent=1) + "\n").encode())
    log.info("AI settings saved: %s, model %s", config.provider, config.model or "(none)")
    return load(folder, env)


def public(config: AIConfig) -> dict[str, Any]:
    """The AI settings object. The key itself never appears, only its last 4 characters."""
    key = config.api_key
    return {"provider": config.provider, "base_url": config.base_url, "model": config.model,
            "max_input_chars": config.max_input_chars, "key_set": bool(key),
            "key_hint": key[-4:] if key and len(key) >= 16 else None,
            "configured": config.configured, "from_env": sorted(config.from_env), "problem": config.problem,
            "saved": config.saved, "presets": [preset.public() for preset in PRESETS.values()]}


def build_llm(config: AIConfig, *, transport: httpx.BaseTransport | None = None, resolver: Resolver | None = None,
              need_model: bool = True) -> OpenAICompatLLM | None:
    """The provider for these settings, or None when they aren't complete."""
    if not config.base_url or (config.preset.needs_key and not config.api_key) or (need_model and not config.model):
        return None
    preset = config.preset
    kind = OpenAICompatLLMWithSpeech if preset.speech_model else OpenAICompatLLM
    return kind(base_url=config.base_url, model=config.model, api_key=config.api_key,
                provider=config.provider, max_input_chars=config.max_input_chars,
                token_param=preset.token_param, speech_model=preset.speech_model,
                transport=transport, resolver=resolver)
