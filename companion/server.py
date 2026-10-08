"""The companion app: settings, every feature router in ``companion/routes``, then migrations.

Features never edit this file; see ``routes/__init__.py`` (routes), ``deps.py`` (dependencies),
``db.py`` (tables) and ``llm.py`` (AI).
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI

from .answers import AnswerProvider, HTTPAnswerProvider
from .db import migrate
from .deps import Settings
from .routes import include_all
from .runtime import hosted_policy
from .transcripts import TranscriptLibrary

ROOT = Path(__file__).resolve().parents[1]


def create_app(backend: str, database: Path, library_root: Path | None = None,
               transport: httpx.BaseTransport | None = None,
               answer_provider: AnswerProvider | None = None) -> FastAPI:
    policy = hosted_policy()
    if policy is not None:
        policy.gateway_key()
    app = FastAPI(title="Podcast companion", version="0.1.0")
    app.state.settings = Settings(podfetch_url=backend, database=Path(database), library_root=library_root,
                                  transport=transport, answer_provider=answer_provider)
    app.state.transcript_library = TranscriptLibrary(library_root)
    include_all(app)
    migrate(database)  # after include_all: importing the routes registers their migrations
    return app


def database_path() -> Path:
    """``COMPANION_DB`` (``NOTES_DB`` is the older name), else runtime/notes.db. Relative paths start at the app folder."""
    value = os.getenv("COMPANION_DB") or os.getenv("NOTES_DB")
    path = Path(value).expanduser() if value else Path("runtime/notes.db")
    return path if path.is_absolute() else ROOT / path


def default_app():
    root = os.getenv("PODCAST_LIBRARY")
    answer_url = os.getenv("PODCAST_ANSWER_URL", "").strip()
    provider = HTTPAnswerProvider(answer_url, os.getenv("PODCAST_ANSWER_TOKEN")) if answer_url else None
    return create_app(os.getenv("PODFETCH_BACKEND", "http://127.0.0.1:18080"), database_path(),
                      Path(root) if root else None, answer_provider=provider)
