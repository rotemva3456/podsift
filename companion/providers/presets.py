"""The providers people can pick in Settings → AI. Each one speaks the OpenAI chat API.

`max_input_chars` is a safe first input limit for the provider: Groq's free tier refuses a
request above about 7,000 input tokens per minute, and Ollama's default context is small.
`suggested_models` are picked in the UI only when the provider's own model list has them, so
a model that a provider retires is never pre-filled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    base_url: str                 # empty for "custom": the user types it
    needs_key: bool
    key_url: str | None           # where to get a key
    max_input_chars: int
    token_param: str              # the request field for the output limit
    speech_model: str | None      # model for POST /audio/transcriptions, when the provider has one
    suggested_models: tuple[str, ...] = ()

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "base_url": self.base_url, "needs_key": self.needs_key,
                "key_url": self.key_url, "max_input_chars": self.max_input_chars,
                "suggested_models": list(self.suggested_models)}


DEFAULT_MAX_INPUT_CHARS = 60_000

PRESETS: dict[str, Preset] = {preset.id: preset for preset in (
    Preset("openai", "OpenAI", "https://api.openai.com/v1", True, "https://platform.openai.com/api-keys",
           DEFAULT_MAX_INPUT_CHARS, "max_completion_tokens", "whisper-1",
           ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini")),
    Preset("groq", "Groq", "https://api.groq.com/openai/v1", True, "https://console.groq.com/keys",
           20_000, "max_completion_tokens", "whisper-large-v3-turbo",
           ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")),
    Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", True, "https://openrouter.ai/settings/keys",
           DEFAULT_MAX_INPUT_CHARS, "max_tokens", None,
           ("openai/gpt-5-mini", "google/gemini-2.5-flash", "openai/gpt-4o-mini")),
    Preset("ollama", "Ollama", "http://ollama:11434/v1", False, None, 12_000, "max_tokens", None),
    Preset("custom", "Custom", "", False, None, DEFAULT_MAX_INPUT_CHARS, "max_tokens", None),
)}

DEFAULT_PROVIDER = "groq"   # free tier, and the same key makes transcripts (TRANSCRIPTION_API_* in .env)


def preset_for_url(base_url: str) -> Preset:
    """The preset whose address this is (for LLM_BASE_URL), else Custom."""
    wanted = base_url.rstrip("/").lower()
    for preset in PRESETS.values():
        if preset.base_url and preset.base_url.lower() == wanted:
            return preset
    return PRESETS["custom"]
