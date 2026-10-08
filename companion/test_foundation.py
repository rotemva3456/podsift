"""The plug-in points: routes found by file, migrations, dependencies and the AI seam."""
import shutil
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import companion.routes.notes  # noqa: F401  (registers the notes migrations)
from companion import db as database
from companion import routes, server
from companion.llm import FakeLLM, LLMError, get_llm
from companion.server import create_app
from companion.test_server import EPISODE

OPERATOR_DB = Path(__file__).resolve().parents[1] / "runtime/notes.db"
# The notes table exactly as the app created it before migrations existed (2026-09-22).
TODAYS_SCHEMA = '''CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, title TEXT NOT NULL,
            position REAL NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)'''


def podfetch(request):
    if request.url.path == f"/api/v1/episodes/{EPISODE}":
        return httpx.Response(200, json={"podcastEpisode": {"id": EPISODE, "episode_id": EPISODE,
                                                            "name": "An episode", "url": "https://example.test/a.mp3",
                                                            "total_time": 120}})
    return httpx.Response(404)


def start(path):
    return TestClient(create_app("http://podfetch.test", path, transport=httpx.MockTransport(podfetch)))


def rows(path, sql):
    with sqlite3.connect(path) as db:
        return db.execute(sql).fetchall()


def snapshot(path):
    return (rows(path, "SELECT type, name, sql FROM sqlite_master ORDER BY name"),
            rows(path, "SELECT feature, version, checksum, applied_at FROM schema_migrations ORDER BY 1, 2"),
            rows(path, "SELECT * FROM notes ORDER BY id"))


def todays_file(path, count=3):
    with sqlite3.connect(path) as db:
        db.execute(TODAYS_SCHEMA)
        for n in range(count):
            db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)", (str(uuid4()), EPISODE, "An episode",
                       float(10 * n), f"Note {n}; with 'quotes'", f"2026-09-2{n}T10:00:00+00:00"))
    return path


def drop_module(monkeypatch, tmp_path, name, source):
    """Put a module into companion/routes for this test only (the package path grows by tmp_path)."""
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(routes, "__path__", [*routes.__path__, str(tmp_path)])
    monkeypatch.delitem(sys.modules, f"companion.routes.{name}", raising=False)


# ── routes ──────────────────────────────────────────────────────────────────

def test_a_router_module_dropped_into_routes_is_served(tmp_path, monkeypatch):
    drop_module(monkeypatch, tmp_path, "zz_probe", '''
from fastapi import APIRouter, Depends
from companion.deps import current_user
router = APIRouter()
@router.get("/companion/probe")
def probe(user: str = Depends(current_user)):
    return {"probe": "served", "user": user}
''')
    client = start(tmp_path / "companion.db")
    assert client.get("/companion/probe").json() == {"probe": "served", "user": "default"}
    assert client.get("/companion/health").json()["status"] == "ok"


@pytest.mark.parametrize("source, message", [
    ("x = 1", "must define router = APIRouter()"),
    ("from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/elsewhere')\ndef f(): return {}",
     "must start with /companion/"),
])
def test_a_broken_route_module_stops_the_start_with_a_clear_message(tmp_path, monkeypatch, source, message):
    drop_module(monkeypatch, tmp_path, "zz_broken", source)
    with pytest.raises(RuntimeError, match=message):
        start(tmp_path / "companion.db")


def test_the_existing_routes_moved_into_route_modules():
    assert {"answers", "health", "notes", "transcripts"} <= set(routes.include_all(FastAPI()))


# ── migrations ──────────────────────────────────────────────────────────────

def test_todays_file_upgrades_and_keeps_every_note(tmp_path):
    path = todays_file(tmp_path / "notes.db")
    before = rows(path, "SELECT id, episode_id, title, position, text, created_at FROM notes ORDER BY id")
    client = start(path)
    after = rows(path, "SELECT id, episode_id, title, position, text, created_at FROM notes ORDER BY id")
    assert after == before and len(after) == 3
    assert rows(path, "SELECT DISTINCT user_id FROM notes") == [("default",)]
    # Other features add their own migrations; the notes table got exactly these three (highlights added
    # kind/start/end/quote as version 3; every existing row defaults to kind="note").
    assert rows(path, "SELECT feature, version FROM schema_migrations WHERE feature = 'notes' ORDER BY 2") == [
        ("notes", 1), ("notes", 2), ("notes", 3)]
    listed = client.get(f"/companion/notes?episode_id={EPISODE}").json()
    assert sorted(note["id"] for note in listed) == sorted(row[0] for row in before)
    assert set(listed[0]) == {"id", "episode_id", "title", "position", "text", "created_at",
                              "kind", "start", "end", "quote"}
    assert {note["kind"] for note in listed} == {"note"}


@pytest.mark.skipif(not OPERATOR_DB.is_file(), reason="no runtime/notes.db on this machine")
def test_a_copy_of_the_operators_notes_db_upgrades_and_keeps_every_note(tmp_path):
    copy = tmp_path / "notes.db"
    shutil.copyfile(OPERATOR_DB, copy)
    count = rows(copy, "SELECT count(*) FROM notes")[0][0]
    start(copy)
    assert rows(copy, "SELECT count(*) FROM notes")[0][0] == count
    assert rows(copy, "SELECT count(*) FROM notes WHERE user_id = 'default'")[0][0] == count


def test_a_second_start_changes_nothing(tmp_path):
    path = todays_file(tmp_path / "notes.db")
    start(path)
    first = snapshot(path)
    start(path)
    assert snapshot(path) == first
    assert database.migrate(path) == []


def test_a_new_file_gets_the_same_schema_as_an_upgraded_one(tmp_path):
    start(tmp_path / "new.db")
    todays_file(tmp_path / "old.db", count=0)
    start(tmp_path / "old.db")
    columns = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info('notes')"
    assert rows(tmp_path / "new.db", columns) == rows(tmp_path / "old.db", columns)


def test_a_failing_migration_rolls_back_and_stops_the_start(tmp_path, monkeypatch):
    path = todays_file(tmp_path / "notes.db")
    monkeypatch.setitem(database._MIGRATIONS, ("zz-broken", 1),
                        "CREATE TABLE half_done (x TEXT); INSERT INTO no_such_table VALUES (1)")
    with pytest.raises(database.MigrationError, match=r"zz-broken v1 failed .*rolled back"):
        start(path)
    assert rows(path, "SELECT name FROM sqlite_master WHERE name = 'half_done'") == []
    assert ("zz-broken", 1) not in rows(path, "SELECT feature, version FROM schema_migrations")
    assert rows(path, "SELECT count(*) FROM notes")[0][0] == 3
    monkeypatch.delitem(database._MIGRATIONS, ("zz-broken", 1))
    start(path)  # fixed: the next start works


def test_migrations_apply_in_version_order_once_with_semicolons_in_strings(tmp_path, monkeypatch):
    monkeypatch.setitem(database._MIGRATIONS, ("zz-probe", 2), "INSERT INTO zz_probe VALUES ('two; still one statement')")
    monkeypatch.setitem(database._MIGRATIONS, ("zz-probe", 1), "CREATE TABLE zz_probe (value TEXT);")
    path = tmp_path / "companion.db"
    assert [m for m in database.migrate(path) if m[0] == "zz-probe"] == [("zz-probe", 1), ("zz-probe", 2)]
    assert database.migrate(path) == []
    assert rows(path, "SELECT value FROM zz_probe") == [("two; still one statement",)]


@pytest.mark.parametrize("args, message", [
    (("notes", 1, "CREATE TABLE other (x)"), "registered twice"),
    (("Bad Name", 1, "SELECT 1"), "lowercase"),
    (("zz-probe", 0, "SELECT 1"), "whole numbers"),
    (("zz-probe", 1, "BEGIN; CREATE TABLE t (x); COMMIT"), "leave BEGIN/COMMIT out"),
])
def test_register_migration_refuses_mistakes(args, message):
    with pytest.raises(ValueError, match=message):
        database.register_migration(*args)


def test_database_location_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("COMPANION_DB", raising=False)
    monkeypatch.delenv("NOTES_DB", raising=False)
    assert server.database_path() == server.ROOT / "runtime/notes.db"
    monkeypatch.setenv("NOTES_DB", str(tmp_path / "legacy.db"))
    assert server.database_path() == tmp_path / "legacy.db"
    monkeypatch.setenv("COMPANION_DB", "runtime/agents/P01/companion.db")
    assert server.database_path() == server.ROOT / "runtime/agents/P01/companion.db"


# ── dependencies ────────────────────────────────────────────────────────────

def test_notes_are_filtered_by_the_current_user(tmp_path):
    path = todays_file(tmp_path / "notes.db", count=1)
    client = start(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO notes (id, episode_id, title, position, text, created_at, user_id) "
                   "VALUES (?,?,?,?,?,?,?)", (str(uuid4()), EPISODE, "An episode", 5.0, "Someone else's",
                                              "2026-09-24T10:00:00+00:00", "someone-else"))
    listed = client.get("/companion/notes").json()
    assert [note["text"] for note in listed] == ["Note 0; with 'quotes'"]
    taken = {"id": rows(path, "SELECT id FROM notes WHERE user_id = 'someone-else'")[0][0],
             "episode_id": EPISODE, "position": 5, "text": "Someone else's"}
    assert client.post("/companion/notes", json=taken).status_code == 409


def test_get_db_commits_on_success_and_rolls_back_on_error(tmp_path, monkeypatch):
    drop_module(monkeypatch, tmp_path, "zz_writes", '''
from fastapi import APIRouter, Depends, HTTPException
from companion.deps import get_db
router = APIRouter()
@router.post("/companion/probe/{text}")
def write(text: str, fail: bool = False, db=Depends(get_db)):
    db.execute("INSERT INTO notes (id, episode_id, title, position, text, created_at) VALUES (?,?,?,?,?,?)",
               (text, "e", "t", 0, text, "2026-09-24"))
    if fail:
        raise HTTPException(400, "Refused on purpose.")
    return {"saved": text}
''')
    path = tmp_path / "companion.db"
    client = start(path)
    assert client.post("/companion/probe/kept").status_code == 200
    assert client.post("/companion/probe/dropped?fail=true").status_code == 400
    assert rows(path, "SELECT text FROM notes") == [("kept",)]


def test_get_llm_is_none_until_a_provider_is_set_and_fake_llm_replays_answers(tmp_path, monkeypatch):
    drop_module(monkeypatch, tmp_path, "zz_ai", '''
from fastapi import APIRouter, Depends
from companion.llm import get_llm
router = APIRouter()
@router.get("/companion/probe-ai")
def ask(llm=Depends(get_llm)):
    if llm is None:
        return {"ai": "Connect AI in Settings → AI"}
    return llm.complete_json(system="Answer with JSON.", user="<evidence>text</evidence>",
                             schema={"type": "object"}, max_tokens=50)
''')
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(podfetch))
    client = TestClient(app)
    assert get_llm() is None
    assert client.get("/companion/probe-ai").json() == {"ai": "Connect AI in Settings → AI"}
    fake = FakeLLM([{"verdict": "HEAR"}, LLMError("The key was rejected")])
    app.dependency_overrides[get_llm] = lambda: fake
    assert client.get("/companion/probe-ai").json() == {"verdict": "HEAR"}
    with pytest.raises(LLMError):
        client.get("/companion/probe-ai")
    assert [call["max_tokens"] for call in fake.calls] == [50, 50]
    with pytest.raises(AssertionError, match="no response left"):
        fake.complete_json(system="s", user="u", schema={})
