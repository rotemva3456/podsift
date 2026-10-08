"""Native skipping through HTTP, with source identity and no external data requests."""
import httpx
import pytest
from fastapi.testclient import TestClient

from companion.server import create_app
from companion.test_cuts import EP, PID, FakePodFetch, native, offline
from companion.transcripts import Timing
from companion.routes.skipping import timing_status
from companion.engine import as_segments


@pytest.fixture
def native_world(tmp_path):
    fake = FakePodFetch()
    fake.add()
    app = create_app("http://podfetch.test", tmp_path / "skipping.db", transport=httpx.MockTransport(fake))
    return fake, TestClient(app)


def test_native_route_cites_the_current_transcript_and_never_requests_sponsorblock(native_world):
    fake, client = native_world
    response = client.get(f"/companion/episodes/{EP}/skip-segments")
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["source"] == "podsift-transcript"
    assert found["origin"] == "feed" and found["timed"] is True
    assert found["timing_status"] == "unverified" and not found["auto_skip_safe"]
    assert found["transcript_digest"]
    sponsor = [s for s in found["spans"] if s["category"] == "sponsor"]
    assert [(s["start"], s["end"]) for s in sponsor] == [(0, 8)]
    assert sponsor[0]["segment_ids"] == ["1"]
    assert "explicitly" in sponsor[0]["reason"]
    assert not any("sponsorblock" in path for _, path in fake.requests)


def test_modified_source_is_recomputed_with_a_new_digest(native_world):
    fake, client = native_world
    first = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    fake.native[PID]["segments"][0]["text"] = "Welcome to the show. Learn more about route selection."
    second = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    assert second["transcript_digest"] != first["transcript_digest"]
    assert not any(s["category"] == "sponsor" for s in second["spans"])


def test_missing_transcript_is_not_reported_as_a_completed_no_ads_scan(native_world):
    fake, client = native_world
    fake.native.clear()
    response = client.get(f"/companion/episodes/{EP}/skip-segments")
    assert response.status_code == 200
    assert response.json()["timed"] is False
    assert response.json()["spans"] == []
    assert response.json()["timing_status"] == "missing"
    assert not response.json()["auto_skip_safe"]


def test_native_spans_outside_the_current_audio_are_not_used(native_world):
    fake, client = native_world
    fake.native[PID] = native(["This episode is sponsored by Acme."], [(110, 130)])
    found = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    assert found["duration"] == 120
    assert found["spans"] == []
    assert found["timing_status"] == "mismatch"


def test_generated_audio_transcript_takes_priority_over_publisher_times(native_world):
    fake, client = native_world
    fake.add(transcript="generated")
    found = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    assert found["origin"] == "generated" and found["timed"]
    assert found["timing_status"] == "matched" and found["auto_skip_safe"]
    assert any(s["category"] == "sponsor" for s in found["spans"])


def test_episode_access_errors_are_preserved(native_world):
    fake, client = native_world
    fake.logins = {"Basic valid": "alice"}
    response = client.get(f"/companion/episodes/{EP}/skip-segments")
    assert response.status_code in (401, 403)


def test_an_earlier_valid_ad_cannot_hide_an_out_of_file_transcript(native_world):
    fake, client = native_world
    fake.native[PID] = native(["Sponsored by Acme.", "Ordinary closing content."], [(0, 8), (120, 180)])
    found = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    assert found["timing_status"] == "mismatch"
    assert not found["auto_skip_safe"] and found["spans"] == []


def test_generated_timing_without_an_audio_duration_is_manual(native_world):
    fake, client = native_world
    fake.add(transcript="generated")
    fake.episodes[EP]["total_time"] = 0
    found = client.get(f"/companion/episodes/{EP}/skip-segments").json()
    assert found["timing_status"] == "unverified" and not found["auto_skip_safe"]


def test_a_library_verified_against_its_old_copy_does_not_prove_the_current_file():
    timing = Timing({"media_verified": True}, as_segments([{"id": "ad", "start": 0, "end": 8,
                    "text": "Sponsored by Acme."}]), "library", {"duration_seconds": 120, "sha256": "old-file"})
    assert timing_status(timing, 120) == "unverified"
    assert timing_status(timing, 110) == "mismatch"


def test_preferences_persist_and_remain_separate_for_each_user(native_world):
    fake, client = native_world
    fake.logins = {"Basic alice": "alice", "Basic bob": "bob"}
    alice = {"Authorization": "Basic alice"}
    bob = {"Authorization": "Basic bob"}
    settings = {"manual_categories": ["sponsor", "filler"], "minimum_seconds": 5}
    response = client.put("/companion/settings/skipping", headers=alice, json=settings)
    assert response.status_code == 200, response.text
    assert client.get("/companion/settings/skipping", headers=alice).json() == settings
    assert client.get("/companion/settings/skipping", headers=bob).json() == {"manual_categories": [], "minimum_seconds": 0}
    assert client.get("/companion/settings/skipping").status_code in (401, 403)
    assert client.put("/companion/settings/skipping", json=settings).status_code in (401, 403)
    # Recreate the app against the same database: these preferences survive a restart.
    app = create_app("http://podfetch.test", client.app.state.settings.database, transport=httpx.MockTransport(fake))
    assert TestClient(app).get("/companion/settings/skipping", headers=alice).json() == settings


@pytest.mark.parametrize("settings", [
    {"manual_categories": ["unknown"]}, {"manual_categories": "sponsor"},
    {"minimum_seconds": -1}, {"minimum_seconds": 61}, {"minimum_seconds": "NaN"},
    {"minimum_seconds": True}, {"extra": True},
])
def test_invalid_preferences_are_rejected_without_overwriting_saved_choices(native_world, settings):
    _, client = native_world
    saved = {"manual_categories": ["intro"], "minimum_seconds": 10}
    assert client.put("/companion/settings/skipping", json=saved).status_code == 200
    assert client.put("/companion/settings/skipping", json=settings).status_code == 422
    assert client.get("/companion/settings/skipping").json() == saved
