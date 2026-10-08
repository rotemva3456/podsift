"""Keep tool outputs short and readable: times as seconds plus m:ss, lists
capped with a "more" hint.
"""
from __future__ import annotations

from typing import Any


def clock(seconds: Any) -> str | None:
    """``754.2`` -> ``"12:34"``; ``4213`` -> ``"1:10:13"``. ``None`` stays ``None``."""
    if seconds is None:
        return None
    try:
        total = int(round(max(0.0, float(seconds))))
    except (TypeError, ValueError):
        return None
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def with_clocks(item: dict[str, Any], *fields: str) -> dict[str, Any]:
    """A shallow copy of ``item`` with a ``<field>_clock`` string next to each present time field."""
    out = dict(item)
    for field in fields:
        if field in out and out[field] is not None:
            out[f"{field}_clock"] = clock(out[field])
    return out


def cap(items: list[Any], limit: int) -> tuple[list[Any], int]:
    """The first ``limit`` items, and how many were left out."""
    limit = max(0, limit)
    return list(items[:limit]), max(0, len(items) - limit)


def clamp(value: int | None, default: int, maximum: int, *, minimum: int = 1) -> int:
    """A user-supplied limit, defaulted and kept inside ``[minimum, maximum]``."""
    if value is None:
        return default
    return max(minimum, min(int(value), maximum))
