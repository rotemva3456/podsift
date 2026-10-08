"""Optional deployment policy; self-hosted installs need only PodFetch login.

Managed-service policy is supplied separately. Requesting that mode without its
implementation must stop startup, never silently fall back to self-hosted auth.
"""
from __future__ import annotations

import importlib
import os
from types import ModuleType

from fastapi import Request


def hosted_enabled() -> bool:
    value = os.getenv("PODSIFT_HOSTED", "false").lower()
    if value not in {"true", "false"}:
        raise RuntimeError("PODSIFT_HOSTED must be true or false.")
    return value == "true"


def hosted_policy() -> ModuleType | None:
    if not hosted_enabled():
        return None
    try:
        return importlib.import_module(".hosted_security", __package__)
    except ModuleNotFoundError as exc:
        if exc.name != "companion.hosted_security":
            raise
        raise RuntimeError("Managed-service mode requires its separately installed runtime.") from exc


def require_operator(request: Request) -> None:
    policy = hosted_policy()
    if policy is not None:
        policy.require_operator(request)
