"""Effort-aware planning uses source IDs and keeps the learner's choice across reads.

Selection responses are offline fixtures; these checks do not measure model teaching quality.
"""
import pytest

from companion import cuts
from companion.llm import FakeLLM, get_llm
from companion.test_cuts import EP, PID, audio, native, offline, times, world
from companion.test_agent_cuts import choice, selection, submit


@pytest.mark.parametrize("effort,expected", [("focus", [(20, 60)]), ("chill", [(60, 90)])])
def test_ai_selects_different_source_passages_and_saves_effort(world, effort, expected):
    fake, app, client, _ = world
    fake.add()
    fake.native[PID] = native([
        "Recap: BGP chooses routes using path attributes.",
        "Work through this confusing route comparison and the tie breakers step by step.",
        "A familiar example explains route preference in simple everyday terms.",
        "The show ends here.",
    ], [(0, 20), (20, 60), (60, 90), (90, 120)])
    llm = FakeLLM([{"ranges": [{"start_id": "2" if effort == "focus" else "3",
                              "end_id": "2" if effort == "focus" else "3",
                              "why": "Demanding worked comparison" if effort == "focus" else "Easy familiar example",
                              "relevance": 3}]}])
    app.dependency_overrides[get_llm] = lambda: llm
    result = client.post("/companion/plans", json={"episode_ids": [EP], "want": "BGP, especially confusing tie breakers",
                                                 "mode": "ai", "learning_mode": effort, "minutes": 1})
    assert result.status_code == 201, result.text
    made = result.json()
    assert times(made["spans"]) == expected
    assert made["learning_mode"] == effort and made["kept_seconds"] <= 60
    assert len(llm.calls) == 1
    assert cuts.LEARNING_GUIDANCE[effort] in llm.calls[0]["user"]
    assert "separately from importance" in llm.calls[0]["system"]
    assert "confusing tie breakers" in llm.calls[0]["user"]
    assert client.get(f"/companion/plans/{made['id']}").json()["learning_mode"] == effort
    assert client.get(f"/companion/plans/{made['id']}/script").json()["learning_mode"] == effort
    patched = client.patch(f"/companion/plans/{made['id']}", json={"spans": [{"id": "s1", "enabled": False}]})
    assert patched.json()["learning_mode"] == effort and patched.json()["kept_seconds"] == 0


@pytest.mark.parametrize("effort", ["focus", "chill"])
def test_agent_keeps_explicit_selections_and_skips_without_app_ai(world, effort):
    fake, app, client, _ = world
    fake.add()

    class MustNotCall:
        def complete_json(self, **kwargs):
            raise AssertionError("Agent selections must not invoke an app model")

    app.dependency_overrides[get_llm] = lambda: MustNotCall()
    picked = selection(client, keep=[choice(2, 5)], skip=[choice(3, 3, "Save this confusing aside for another session")])
    response = submit(client, [picked], learning_mode=effort)
    assert response.status_code == 201, response.text
    made = response.json()
    assert made["learning_mode"] == effort
    assert all(s["end"] <= 16 or s["start"] >= 24 for s in made["spans"])
    assert client.get("/companion/plans", params={"learning_mode": effort}).json()[0]["id"] == made["id"]


@pytest.mark.parametrize("effort", ["focus", "chill", "unknown"])
def test_keyword_matching_cannot_claim_to_select_by_effort(world, effort):
    fake, _app, client, _ = world
    response = client.post("/companion/plans", json={"episode_ids": [EP], "want": "BGP",
                                                  "mode": "keyword", "learning_mode": effort})
    assert response.status_code == 422
    assert all(path == "/api/v1/users/me" for _method, path in fake.requests)


def test_chill_can_have_no_suitable_passages_and_ai_unavailability_is_explicit(world):
    fake, app, client, _ = world
    fake.add()
    body = {"episode_ids": [EP], "want": "BGP", "mode": "ai", "learning_mode": "chill"}
    assert client.post("/companion/plans", json=body).status_code == 409
    app.dependency_overrides[get_llm] = lambda: FakeLLM([{"ranges": []}])
    response = client.post("/companion/plans", json=body)
    assert response.status_code == 201
    assert response.json()["status"] == "empty" and response.json()["learning_mode"] == "chill"
