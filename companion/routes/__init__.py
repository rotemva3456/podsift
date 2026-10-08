"""Every module here is a feature: ``router = APIRouter()`` with paths under ``/companion/``.

``include_all`` imports the modules in name order and includes each ``router``, so a new feature
is one new file and nobody edits ``server.py``. Modules whose names start with ``_`` or ``test_``
are skipped.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

PREFIX = "/companion/"


def include_all(app: FastAPI) -> list[str]:
    """Include every feature router; returns the module names in the order they were included."""
    names = sorted(module.name for module in pkgutil.iter_modules(__path__)
                   if not module.name.startswith(("_", "test_")))
    for name in names:
        module = importlib.import_module(f"{__name__}.{name}")
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise RuntimeError(f"companion/routes/{name}.py must define router = APIRouter().")
        for route in router.routes:
            path = getattr(route, "path", "")
            if isinstance(route, (APIRoute, APIWebSocketRoute)) and not path.startswith(PREFIX):
                raise RuntimeError(f"companion/routes/{name}.py: {path!r} must start with {PREFIX}.")
        app.include_router(router)
    return names
