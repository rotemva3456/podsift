"""GET|PUT /companion/profile: the listener's own topics, used by the brief verdict prompt."""
import httpx
from fastapi.testclient import TestClient

from companion.db import connect
from companion.deps import current_user
from companion.routes.profile import topics_for
from companion.server import create_app

# PodFetch has no /api/v1/users/me: the companion treats that as "runs without login" (auth.py).
NO_AUTH = httpx.MockTransport(lambda request: httpx.Response(404))


def start(tmp_path):
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=NO_AUTH)
    return TestClient(app), app


def test_nothing_saved_yet_is_an_empty_list(tmp_path):
    client, _ = start(tmp_path)
    assert client.get("/companion/profile").json() == {"topics": []}


def test_put_saves_and_get_reads_it_back(tmp_path):
    client, _ = start(tmp_path)
    saved = client.put("/companion/profile", json={"topics": ["BGP", "home labs", "sourdough"]})
    assert saved.status_code == 200 and saved.json() == {"topics": ["BGP", "home labs", "sourdough"]}
    assert client.get("/companion/profile").json() == {"topics": ["BGP", "home labs", "sourdough"]}


def test_blank_and_duplicate_topics_are_dropped(tmp_path):
    client, _ = start(tmp_path)
    saved = client.put("/companion/profile", json={"topics": ["  Bash  ", "", "bash", "  ", "Bash Scripting"]})
    assert saved.json() == {"topics": ["Bash", "Bash Scripting"]}


def test_a_second_put_replaces_the_list(tmp_path):
    client, _ = start(tmp_path)
    client.put("/companion/profile", json={"topics": ["Networking"]})
    saved = client.put("/companion/profile", json={"topics": ["Cooking"]})
    assert saved.json() == {"topics": ["Cooking"]}
    assert client.get("/companion/profile").json() == {"topics": ["Cooking"]}


def test_more_than_twenty_topics_is_capped(tmp_path):
    client, _ = start(tmp_path)
    many = [f"topic {n}" for n in range(30)]
    saved = client.put("/companion/profile", json={"topics": many})
    assert len(saved.json()["topics"]) == 20 and saved.json()["topics"][0] == "topic 0"


def test_each_user_has_their_own_topics(tmp_path):
    client, app = start(tmp_path)
    app.dependency_overrides[current_user] = lambda: "alice"
    client.put("/companion/profile", json={"topics": ["Alice's topic"]})
    app.dependency_overrides[current_user] = lambda: "bob"
    assert client.get("/companion/profile").json() == {"topics": []}
    app.dependency_overrides[current_user] = lambda: "alice"
    assert client.get("/companion/profile").json() == {"topics": ["Alice's topic"]}


def test_topics_for_reads_directly_without_a_route(tmp_path):
    client, _ = start(tmp_path)
    client.put("/companion/profile", json={"topics": ["Bash"]})
    conn = connect(tmp_path / "companion.db")
    try:
        assert topics_for(conn, "default") == ["Bash"]
        assert topics_for(conn, "nobody") == []
    finally:
        conn.close()


class _NoPodfetch:
    """Enough of PodFetch for facts_for: no publisher chapters, and no podcast_id means no
    novelty lookup either, so this is never really called."""

    def get(self, *args, **kwargs):
        return None


def test_saved_topics_reach_the_brief_verdict_prompt(tmp_path):
    """companion/brief.py: facts_for picks up this listener's topics, and facts_text passes them
    to the model as evidence."""
    from companion import brief as briefs
    from companion.engine import as_segments

    client, _ = start(tmp_path)
    client.put("/companion/profile", json={"topics": ["Bash", "home labs"]})
    conn = connect(tmp_path / "companion.db")
    try:
        segments = as_segments([{"id": "s1", "start": 0, "end": 5, "text": "Hello."}])
        ctx = briefs.BriefContext(podfetch=_NoPodfetch(), transcripts=None, db=conn)
        facts = briefs.facts_for(ctx, {"name": "Episode"}, segments, "default")
        assert facts["topics"] == ["Bash", "home labs"]
        text = briefs.facts_text({"name": "Episode"}, "", facts, segments)
        assert "This listener picked these topics as their own interests: Bash, home labs." in text
    finally:
        conn.close()


def test_no_saved_topics_means_no_extra_prompt_line(tmp_path):
    from companion import brief as briefs
    from companion.engine import as_segments

    _client, _app = start(tmp_path)
    conn = connect(tmp_path / "companion.db")
    try:
        segments = as_segments([{"id": "s1", "start": 0, "end": 5, "text": "Hello."}])
        ctx = briefs.BriefContext(podfetch=_NoPodfetch(), transcripts=None, db=conn)
        facts = briefs.facts_for(ctx, {"name": "Episode"}, segments, "default")
        assert facts["topics"] == []
        assert "topics of interest" not in briefs.facts_text({"name": "Episode"}, "", facts, segments)
    finally:
        conn.close()
