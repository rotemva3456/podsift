"""POST/GET /companion/notes: highlights ("Save last 30 s") alongside plain notes, kept
backward compatible with existing voice notes (today's fields only, no kind/start/end/quote)."""
import sqlite3
from uuid import uuid4

import pytest

from companion import db as database
from companion.routes.notes import list_notes
from companion.test_server import EPISODE, fixture


def highlight(**change) -> dict:
    # The fixture's transcript is [0-20 "A subnet divides a network.", 20-45 "A prefix defines
    # its size."]; the client's `quote` here is deliberately wrong so tests can tell it apart
    # from the one the server computes from the real transcript.
    note = {"id": str(uuid4()), "episode_id": EPISODE, "position": 10, "text": "A subnet divides a network.",
           "kind": "highlight", "start": 10, "end": 40, "quote": "the client's guess, not the server's"}
    note.update(change)
    return note


def test_a_note_from_an_older_client_still_defaults_to_kind_note(tmp_path):
    client, *_ = fixture(tmp_path)
    note = {"id": str(uuid4()), "episode_id": EPISODE, "position": 5, "text": "Just a note"}
    response = client.post("/companion/notes", json=note)
    assert response.status_code == 201
    saved = response.json()
    assert saved["kind"] == "note"
    assert (saved["start"], saved["end"], saved["quote"]) == (None, None, None)
    assert client.get(f"/companion/notes?episode_id={EPISODE}").json()[0]["kind"] == "note"


def test_a_highlight_saves_its_range_and_quote(tmp_path):
    client, *_ = fixture(tmp_path)
    response = client.post("/companion/notes", json=highlight())
    assert response.status_code == 201
    saved = response.json()
    # The server computed this from the real transcript for [10, 40); it ignored the client's quote.
    assert (saved["kind"], saved["start"], saved["end"], saved["quote"]) == \
        ("highlight", 10, 40, "…divides a network. A prefix defines its size.")
    only_highlights = client.get(f"/companion/notes?episode_id={EPISODE}&kind=highlight").json()
    assert len(only_highlights) == 1 and only_highlights[0]["id"] == saved["id"]
    assert client.get(f"/companion/notes?episode_id={EPISODE}&kind=note").json() == []


def test_a_highlight_falls_back_to_the_clients_quote_with_no_transcript(tmp_path):
    client, db_path, library, transport = fixture(tmp_path)
    # Point at an episode PodFetch knows nothing about a transcript for.
    import httpx
    from fastapi.testclient import TestClient

    from companion.server import create_app

    def blank(request):
        if "/api/v1/episodes/" in request.url.path:
            return httpx.Response(200, json={"podcastEpisode": {"id": EPISODE, "episode_id": EPISODE,
                "name": "No transcript", "url": "https://example.test/blank.mp3", "total_time": 120}})
        return httpx.Response(404)
    client = TestClient(create_app("http://podfetch.test", db_path, transport=httpx.MockTransport(blank)))
    saved = client.post("/companion/notes", json=highlight()).json()
    assert saved["quote"] == "the client's guess, not the server's"


@pytest.mark.parametrize("change", [{"start": None}, {"end": None}, {"quote": ""}, {"quote": "   "},
                                    {"start": 10, "end": 10}, {"start": 10, "end": 5}])
def test_a_highlight_needs_a_real_range_and_a_quote(tmp_path, change):
    client, *_ = fixture(tmp_path)
    assert client.post("/companion/notes", json=highlight(**change)).status_code == 422


def test_a_highlight_past_the_episode_end_is_rejected(tmp_path):
    client, *_ = fixture(tmp_path)  # the fixture episode is 120 seconds long
    assert client.post("/companion/notes", json=highlight(start=100, end=140)).status_code == 422


def test_resubmitting_the_same_highlight_id_is_idempotent(tmp_path):
    client, *_ = fixture(tmp_path)
    note = highlight()
    assert client.post("/companion/notes", json=note).status_code == 201
    assert client.post("/companion/notes", json=note).status_code == 201
    assert client.post("/companion/notes", json={**note, "end": 41}).status_code == 409
    assert len(client.get(f"/companion/notes?episode_id={EPISODE}").json()) == 1


def test_upgrading_todays_notes_db_defaults_every_row_to_kind_note(tmp_path):
    path = tmp_path / "notes.db"
    with sqlite3.connect(path) as raw:
        raw.execute("""CREATE TABLE notes (id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, title TEXT NOT NULL,
                       position REAL NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)""")
        raw.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)",
                   (str(uuid4()), EPISODE, "An episode", 12.0, "An old note", "2026-09-01T00:00:00+00:00"))
    database.migrate(path)
    db = database.connect(path)
    try:
        listed = list_notes(db, "default")
    finally:
        db.close()
    assert len(listed) == 1
    assert (listed[0]["kind"], listed[0]["start"], listed[0]["end"], listed[0]["quote"]) == ("note", None, None, None)


def test_list_notes_since_filters_by_created_at(tmp_path):
    client, db_path, *_ = fixture(tmp_path)
    old = highlight(id=str(uuid4()))
    assert client.post("/companion/notes", json=old).status_code == 201
    db = database.connect(db_path)
    try:
        db.execute("UPDATE notes SET created_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", old["id"]))
        db.commit()
        assert list_notes(db, "default", since="2026-01-01T00:00:00+00:00") == []
        assert len(list_notes(db, "default", since="1999-01-01T00:00:00+00:00")) == 1
    finally:
        db.close()
