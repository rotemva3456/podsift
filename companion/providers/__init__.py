"""The user's own AI provider (Settings → AI). Features never import this package: they ask for
``llm: LLM | None = Depends(get_llm)`` from ``companion/llm.py``.

presets        the providers in the Settings → AI picker: OpenAI, Groq, OpenRouter, Ollama, Custom
openai_compat  OpenAICompatLLM: complete_json (the LLM protocol), list_models, transcribe
store          the saved settings (a 0600 file with the key sealed), env overrides, build_llm
jsonschema     the small JSON Schema check every answer passes
"""
