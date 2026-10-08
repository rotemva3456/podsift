"""Saved-plan discovery is user scoped, source complete, stable, and offline."""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from companion import cuts, db as database
from companion.server import create_app

EP = "11111111-1111-1111-1111-111111111111"
OTHER_EP = "22222222-2222-2222-2222-222222222222"


def _record(plan_id: str, created_at: str, *, episodes=None, spans=None, omitted=None,
            needs_timing=None):
    return {
        "id": plan_id, "status": "ready", "mode": "agent", "want": plan_id, "skip": "",
        "minutes": None, "skip_ads": True, "context_seconds": 0.0,
        "spans": spans or [], "kept_seconds": 0.0, "source_seconds": 120.0,
        "omitted": omitted or [], "needs_timing": needs_timing or [],
        "created_at": created_at, "episodes": episodes or [], "lines": {},
    }


def _client(tmp_path, records):
    transport = httpx.MockTransport(lambda _request: httpx.Response(404))
    path = tmp_path / "companion.db"
    app = create_app("http://podfetch.test", path, transport=transport)
    with database.connect(path) as connection:
        for user, record in records:
            cuts.save_plan(connection, record, user)
    return TestClient(app)


def test_episode_filter_precedes_limit_and_includes_every_plan_source(tmp_path):
    records = [
        ("default", _record("unrelated-newest", "2026-10-04T10:06:00+00:00",
                            episodes=[{"episode_id": OTHER_EP}])),
        ("default", _record("timing", "2026-10-04T10:05:00+00:00",
                            needs_timing=[{"episode_id": EP, "reason": cuts.NO_TIMING}])),
        ("default", _record("omitted", "2026-10-04T10:04:00+00:00",
                            omitted=[{"episode_id": EP, "start": 0, "end": 10, "reason": "budget"}])),
        ("default", _record("disabled", "2026-10-04T10:03:00+00:00",
                            spans=[{"id": "s1", "episode_id": EP, "start": 10, "end": 20,
                                    "text": "source", "why": "chosen", "enabled": False}])),
        ("default", _record("episode", "2026-10-04T10:02:00+00:00",
                            episodes=[{"episode_id": EP, "title": "Episode", "duration": 120}])),
        ("alice", _record("alice-private", "2026-10-04T10:07:00+00:00",
                          episodes=[{"episode_id": EP}])),
    ]
    client = _client(tmp_path, records)

    response = client.get("/companion/plans", params={"episode_id": EP, "limit": 3})

    assert response.status_code == 200
    assert [plan["id"] for plan in response.json()] == ["timing", "omitted", "disabled"]
    assert "alice-private" not in [plan["id"] for plan in client.get("/companion/plans").json()]


def test_newest_order_is_stable_and_queries_are_validated(tmp_path):
    same_time = "2026-10-04T10:00:00+00:00"
    client = _client(tmp_path, [
        ("default", _record("a", same_time, episodes=[{"episode_id": EP}])),
        ("default", _record("b", same_time, episodes=[{"episode_id": EP}])),
        ("default", _record("old", "2026-10-03T10:00:00+00:00", episodes=[{"episode_id": EP}])),
    ])

    assert [p["id"] for p in client.get("/companion/plans", params={"limit": 2}).json()] == ["b", "a"]
    for params in ({"limit": 0}, {"limit": 51}, {"limit": "many"}, {"episode_id": "not-a-uuid"}):
        assert client.get("/companion/plans", params=params).status_code == 422


def test_effort_filter_precedes_limit_and_preserves_legacy_and_user_scope(tmp_path):
    old = _record("legacy", "2026-10-04T09:00:00", episodes=[{"episode_id": EP}])
    focus = {**old, "id": "focus", "learning_mode": "focus", "created_at": "2026-10-04T10:00:00"}
    chill = {**old, "id": "chill", "learning_mode": "chill", "created_at": "2026-10-04T11:00:00"}
    private = {**focus, "id": "private", "created_at": "2026-10-04T12:00:00"}
    client = _client(tmp_path, [("default", old), ("default", focus), ("default", chill), ("alice", private)])
    focused = client.get("/companion/plans", params={"episode_id": EP, "learning_mode": "focus", "limit": 1}).json()
    assert [p["id"] for p in focused] == ["focus"]
    balanced = client.get("/companion/plans", params={"learning_mode": "balanced"}).json()
    assert [p["id"] for p in balanced] == ["legacy"] and balanced[0]["learning_mode"] == "balanced"
    assert client.get("/companion/plans", params={"learning_mode": "unknown"}).status_code == 422
