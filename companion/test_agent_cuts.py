"""A connected agent chooses passages using its own learner context, without an app model.

The HTTP boundary, original-audio export, skip barriers and shared planner are real. PodFetch
and speech services use the existing offline fixtures; this does not test semantic AI quality.
"""
import pytest

from companion.llm import get_llm
from companion.test_cuts import (
    EP, EP2, PID, LINES, TIMES, audio, native, needs_ffmpeg, offline, render, times, world,
)


def choice(first, last, why="Useful new reasoning", relevance=3):
    return {"start_id": str(first), "end_id": str(last), "why": why, "relevance": relevance}


def selection(client, episode_id=EP, keep=None, skip=None):
    transcript = client.get(f"/companion/episodes/{episode_id}/transcript").json()
    return {"episode_id": episode_id, "transcript_digest": transcript["digest"],
            "keep": keep if keep is not None else [choice(4, 5)], "skip": skip or []}


def submit(client, selections, **extra):
    return client.post("/companion/plans", json={
        "mode": "agent", "want": "Operational examples beyond material I already studied",
        "episode_ids": [s["episode_id"] for s in selections], "selections": selections, **extra})


def test_agent_uses_prior_learning_skips_without_calling_app_ai_and_keeps_an_audit(world):
    fake, app, client, _ = world
    fake.add()

    class MustNotCall:
        def complete_json(self, **kwargs):
            raise AssertionError("Agent planning must use the caller's choices, without another AI call")

    app.dependency_overrides[get_llm] = lambda: MustNotCall()
    picked = selection(client, keep=[choice(1, 5)], skip=[
        choice(3, 3, "Unneeded history"), choice(4, 4, "Already recalled: routing book, chapter 12")])
    response = submit(client, [picked])
    assert response.status_code == 201, response.text
    made = response.json()
    assert made["mode"] == "agent" and made["status"] == "ready"
    assert times(made["spans"]) == [(8, 16), (32, 40)]  # sponsor and known material both stay out
    script = client.get(f"/companion/plans/{made['id']}/script").json()
    skipped = [p for p in script["episodes"][0]["parts"] if p.get("reason") == "skip"]
    assert any("routing book, chapter 12" in p.get("why", "") for p in skipped)
    assert client.get(f"/companion/plans/{made['id']}").json()["spans"] == made["spans"]
    widened = client.patch(f"/companion/plans/{made['id']}", json={"context_seconds": 120}).json()
    assert all(s["end"] <= 16 or s["start"] >= 32 for s in widened["spans"])


@pytest.mark.parametrize("keep,skip", [([choice(999, 999)], []),
                                       ([choice(5, 2)], []), ([], [choice(999, 999)])])
def test_agent_unknown_or_backwards_ranges_are_refused_without_silently_dropping_them(world, keep, skip):
    fake, _app, client, _ = world
    fake.add()
    response = submit(client, [selection(client, keep=keep, skip=skip)])
    assert response.status_code == 422, response.text
    assert "segment id" in response.json()["detail"] or "backwards" in response.json()["detail"]


def test_agent_must_reread_a_transcript_that_changed_since_analysis(world):
    fake, _app, client, _ = world
    fake.add()
    picked = selection(client)
    fake.native[PID] = native(lines=[*LINES[:3], "A different sentence on the same timestamp.", *LINES[4:]])
    stale = submit(client, [picked])
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    fresh = selection(client)
    assert fresh["transcript_digest"] != picked["transcript_digest"]
    assert submit(client, [fresh]).status_code == 201


def test_agent_empty_choices_produce_an_empty_plan_instead_of_a_keyword_fallback(world):
    fake, _app, client, _ = world
    fake.add()
    made = submit(client, [selection(client, keep=[], skip=[choice(1, 15, "Already covered")])]).json()
    assert made["status"] == "empty" and made["spans"] == [] and made["kept_seconds"] == 0
    assert client.post(f"/companion/plans/{made['id']}/render").status_code == 422


def test_agent_plan_rejects_a_missing_transcript_instead_of_a_partial_success(world):
    fake, _app, client, _ = world
    fake.add(transcript="none")
    picked = {"episode_id": EP, "transcript_digest": "0" * 32, "keep": [choice(1, 1)]}
    response = submit(client, [picked])
    assert response.status_code == 409 and "timed transcript" in response.json()["detail"]


def test_agent_budget_is_shared_across_episodes_and_ranked_ranges(world):
    fake, _app, client, _ = world
    fake.add()
    fake.add(episode_id=EP2, podfetch_id="other", name="Another podcast")
    picks = [selection(client, keep=[choice(4, 4), choice(11, 11)]),
             selection(client, EP2, keep=[choice(5, 5)])]
    made = submit(client, picks, minutes=16 / 60).json()
    assert made["status"] == "ready" and made["kept_seconds"] <= 16
    assert {s["episode_id"] for s in made["spans"]} == {EP, EP2}
    assert any(o["reason"] == "over the time budget" for o in made["omitted"])


def test_agent_budget_accounts_for_whole_sentence_context(world):
    fake, _app, client, _ = world
    fake.add()
    fake.native[PID] = native(lines=["This sentence begins with", "new details and its conclusion.", "Another point."],
                              times=TIMES[:3])
    made = submit(client, [selection(client, keep=[choice(2, 2)])], minutes=10 / 60).json()
    assert made["kept_seconds"] <= 10
    assert made["spans"] == []  # the 8-second range becomes a 16-second whole sentence


def test_agent_selections_cannot_smuggle_timestamps_or_duplicate_episodes(world):
    fake, _app, client, _ = world
    fake.add()
    picked = selection(client)
    assert submit(client, [picked, picked]).status_code == 422
    picked["keep"][0]["start"] = 0
    assert submit(client, [picked]).status_code == 422


@needs_ffmpeg
def test_agent_plan_renders_real_source_audio_with_its_source_index(world):
    fake, _app, client, _ = world
    fake.add(transcript="generated")
    transcript = client.get(f"/companion/episodes/{EP}/transcript").json()
    ids = [s["id"] for s in transcript["segments"]]
    made = submit(client, [selection(client, keep=[choice(ids[3], ids[4])])]).json()
    job = render(client, made["id"])
    assert job["status"] == "done", job
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    assert cut["index"] and all(i["episode_id"] == EP for i in cut["index"])
    assert 0 < cut["duration"] < 120
    audio_response = client.get(f"/companion/cuts/{job['cut_id']}.mp3")
    assert audio_response.status_code == 200 and audio_response.headers["content-type"] == "audio/mpeg"
    assert len(audio_response.content) > 1000
