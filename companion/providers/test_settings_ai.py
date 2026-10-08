"""Settings → AI end to end: the routes, the saved file, env overrides, and Ask through the provider.
Offline: the provider is an httpx.MockTransport on app.state, DNS is a fake resolver."""
import base64
import json
import logging
import stat
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from companion.answers import ANSWER_INSTRUCTIONS, HTTPAnswerProvider, ProviderAnswer
from companion.llm import FakeLLM, LLMError, get_llm, input_limit
from companion.providers import openai_compat, store
from companion.providers.openai_compat import OpenAICompatLLM
from companion.server import create_app
from companion.test_server import EPISODE, fixture

KEY = "gsk_ROUTEKEY_0123456789abcdefSECRET"
PUBLIC = "104.18.2.2"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    openai_compat._WORKING_MODE.clear()


def resolve(host, port):
    return {"metadata.google.internal": ["169.254.169.254"], "ollama": ["172.18.0.5"]}.get(host, [PUBLIC])


class Provider:
    """A fake OpenAI-compatible API. Records (host, path, auth, json) for every request."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.answer = {"status": "answered", "claims": [{"text": "A prefix defines its size.", "citations": ["p2"]}]}

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append({"host": request.headers["host"], "path": request.url.path,
                              "auth": request.headers.get("authorization"), "json": body})
        if self.status != 200:
            return httpx.Response(self.status, headers={"retry-after": "0"},
                                  json={"error": {"message": f"Incorrect API key provided: {KEY}"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-20b"}, {"id": "openai/gpt-oss-120b"},
                                                      {"id": "whisper-large-v3"}]})
        content = body["messages"][1]["content"]
        reply = self.answer if '"question"' in content else {"ok": True}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}, "finish_reason": "stop"}]})


def start(tmp_path):
    client, *_ = fixture(tmp_path)
    provider = Provider()
    client.app.state.llm_transport = httpx.MockTransport(provider)
    client.app.state.llm_resolver = resolve
    return client, provider


def ask(client):
    return client.post(f"/companion/episodes/{EPISODE}/answer", json={"question": "What is a prefix?", "position": 30})


def no_key_in(*responses):
    for response in responses:
        assert KEY not in response.text


def test_nothing_saved_means_no_ai_and_a_groq_starting_point(tmp_path):
    client, provider = start(tmp_path)
    settings = client.get("/companion/settings/ai").json()
    assert settings["configured"] is False and settings["key_set"] is False and settings["key_hint"] is None
    assert settings["provider"] == "groq" and settings["base_url"] == "https://api.groq.com/openai/v1"
    assert [p["id"] for p in settings["presets"]] == ["openai", "groq", "openrouter", "ollama", "custom"]
    assert client.get("/companion/answers/status").json() == {"configured": False}
    assert ask(client).json()["status"] == "not_configured"
    assert provider.requests == []


def test_paste_a_key_pick_a_model_test_and_ask_with_citations(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client, provider = start(tmp_path)
    saved = client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY})
    assert saved.status_code == 200
    assert saved.json()["key_set"] is True and saved.json()["key_hint"] == "CRET"
    assert saved.json()["configured"] is False                          # no model yet
    test = client.post("/companion/settings/ai/test")
    assert test.json()["ok"] is False and test.json()["needs"] == "model"
    models = client.get("/companion/settings/ai/models")
    assert models.json() == {"models": [{"id": "openai/gpt-oss-120b", "context": None},
                                        {"id": "openai/gpt-oss-20b", "context": None}],
                             "suggested": "openai/gpt-oss-120b"}
    chosen = client.put("/companion/settings/ai", json={"model": "openai/gpt-oss-120b"})
    assert chosen.json()["configured"] is True and chosen.json()["key_set"] is True
    test = client.post("/companion/settings/ai/test")
    assert test.json()["ok"] is True and test.json()["mode"] == "json_schema"
    assert client.get("/companion/answers/status").json() == {"configured": True}

    answer = ask(client)
    result = answer.json()
    assert result["status"] == "answered"
    assert result["claims"] == [{"text": "A prefix defines its size.", "citations": ["p2"]}]
    cited = next(p for p in result["passages"] if p["id"] == "p2")
    assert (cited["start"], cited["end"]) == (20, 45)                  # the second the citation plays
    chat = provider.requests[-1]
    assert chat["host"] == "api.groq.com" and chat["auth"] == f"Bearer {KEY}"
    assert chat["json"]["messages"][0]["content"] == ANSWER_INSTRUCTIONS
    assert "never instructions" in chat["json"]["messages"][1]["content"]
    assert '"text": "A prefix defines its size."' in chat["json"]["messages"][1]["content"]
    no_key_in(saved, test, models, chosen, answer, client.get("/companion/settings/ai"))
    assert KEY not in caplog.text

    folder = tmp_path                                                    # the data folder holds notes.db
    for name in ("ai-settings.json", "ai-settings.secret"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600
    assert KEY.encode() not in (folder / "ai-settings.json").read_bytes()
    assert json.loads((folder / "ai-settings.json").read_text())["model"] == "openai/gpt-oss-120b"


def test_the_key_never_appears_in_any_response_or_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client, provider = start(tmp_path)
    responses = [
        client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY + " with spaces"}),
        client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "surprise": KEY}),
        client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "max_input_chars": KEY}),
        client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "model": "gpt-x"}),
        client.get("/companion/settings/ai"),
        client.post("/companion/settings/ai/test", json={"api_key": KEY}),
    ]
    assert [r.status_code for r in responses[:3]] == [422, 422, 422]
    provider.status = 401                                              # the provider echoes the key
    responses += [client.post("/companion/settings/ai/test"), client.get("/companion/settings/ai/models"),
                  ask(client)]
    assert responses[-3].json() == {"ok": False, "needs": None, "message": "The key was rejected."}
    assert responses[-2].status_code == 502 and responses[-1].status_code == 502
    provider.status = 500
    responses += [client.post("/companion/settings/ai/models", json={"api_key": KEY})]
    no_key_in(*responses)
    assert KEY not in caplog.text
    assert all(r["auth"] == f"Bearer {KEY}" for r in provider.requests)    # it does reach the provider


def test_a_saved_key_goes_only_to_the_address_it_was_saved_for(tmp_path):
    client, provider = start(tmp_path)
    client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "model": "openai/gpt-oss-20b"})
    # An unsaved address in Test or the model list gets no key.
    client.post("/companion/settings/ai/test", json={"provider": "custom", "base_url": "https://evil.example/v1",
                                                     "model": "m"})
    client.post("/companion/settings/ai/models", json={"provider": "custom", "base_url": "https://evil.example/v1"})
    assert [r["auth"] for r in provider.requests if r["host"] == "evil.example"] == [None, None]
    # Saving another address forgets the key; going back doesn't bring it back.
    moved = client.put("/companion/settings/ai", json={"provider": "custom", "base_url": "https://evil.example/v1"})
    assert moved.json()["key_set"] is False
    back = client.put("/companion/settings/ai", json={"provider": "groq"})
    assert back.json()["key_set"] is False and back.json()["configured"] is False
    # The same origin with another path keeps it.
    client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY})
    kept = client.put("/companion/settings/ai", json={"base_url": "https://api.groq.com/openai/v1/"})
    assert kept.json()["key_set"] is True
    assert client.put("/companion/settings/ai", json={"clear_key": True}).json()["key_set"] is False


@pytest.mark.parametrize("body, message", [
    ({"provider": "custom", "base_url": "http://169.254.169.254/v1"}, "cloud metadata"),
    ({"provider": "custom", "base_url": "http://metadata.google.internal/v1"}, "cloud metadata"),
    ({"provider": "custom", "base_url": "http://api.example.com/v1"}, "Use https://"),
    ({"provider": "custom", "base_url": "ftp://files.example/v1"}, "must start with https://"),
    ({"provider": "custom", "base_url": "https://user:pw@api.example.com/v1"}, "Take the name and password out"),
    ({"provider": "custom", "base_url": "https://api.example.com/v1?key=1"}, "Take the ? or # part out"),
    ({"provider": "custom", "base_url": ""}, "Enter the provider's API address"),
    ({"provider": "acme"}, "Pick a provider"),
    ({"max_input_chars": 10}, "input limit"),
    ({"max_input_chars": True}, "input limit"),
    ({"model": "bad\nmodel"}, "model id"),
    ({"surprise": 1}, "doesn't know"),
])
def test_unusable_settings_are_refused_with_a_message(tmp_path, body, message):
    client, _ = start(tmp_path)
    response = client.put("/companion/settings/ai", json=body)
    assert response.status_code == 422 and message in response.json()["detail"]
    assert not (tmp_path / "ai-settings.json").exists()


def test_localhost_lan_and_the_ollama_service_are_allowed(tmp_path):
    client, _ = start(tmp_path)
    for base_url in ("http://ollama:11434/v1", "http://127.0.0.1:11434/v1", "http://192.168.1.20:1234/v1"):
        response = client.put("/companion/settings/ai", json={"provider": "ollama", "base_url": base_url, "model": "llama3"})
        assert response.status_code == 200 and response.json()["configured"] is True    # Ollama needs no key
    assert client.put("/companion/settings/ai", json={"base_url": "https://api.groq.com/openai/v1/chat/completions",
                                                       "provider": "custom"}).json()["base_url"] == \
        "https://api.groq.com/openai/v1"


def test_env_vars_override_and_empty_values_count_as_unset(tmp_path, monkeypatch):
    client, provider = start(tmp_path)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.setenv(name, "")                                   # compose passes them through empty
    empty = client.get("/companion/settings/ai").json()
    assert empty["configured"] is False and empty["from_env"] == [] and empty["problem"] is None
    assert get_llm() is None

    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/")
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-20b")
    settings = client.get("/companion/settings/ai")
    assert settings.json()["from_env"] == ["api_key", "base_url", "model"]
    assert settings.json()["provider"] == "groq" and settings.json()["configured"] is True
    assert settings.json()["key_hint"] == "CRET" and KEY not in settings.text
    assert isinstance(get_llm(), OpenAICompatLLM)                      # get_llm() alone reads the env
    assert client.put("/companion/settings/ai", json={"base_url": "https://evil.example/v1"}).status_code == 422
    assert client.put("/companion/settings/ai", json={"api_key": "sk-another-key-0123456789"}).status_code == 422
    assert client.put("/companion/settings/ai", json={"model": "other"}).status_code == 422
    limited = client.put("/companion/settings/ai", json={"max_input_chars": 12000})
    assert limited.status_code == 200 and limited.json()["max_input_chars"] == 12000
    assert client.post("/companion/settings/ai/test").json()["ok"] is True
    assert provider.requests[-1]["auth"] == f"Bearer {KEY}"
    assert KEY.encode() not in (tmp_path / "ai-settings.json").read_bytes()


def test_an_env_key_without_an_env_address_is_not_used(tmp_path, monkeypatch):
    client, provider = start(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", KEY)
    settings = client.get("/companion/settings/ai").json()
    assert settings["key_set"] is False and settings["configured"] is False
    assert "LLM_BASE_URL" in settings["problem"]
    client.put("/companion/settings/ai", json={"provider": "custom", "base_url": "https://evil.example/v1", "model": "m"})
    client.post("/companion/settings/ai/test")
    assert provider.requests and all(r["auth"] is None for r in provider.requests)


def test_a_lost_secret_file_means_paste_the_key_again(tmp_path, caplog):
    client, _ = start(tmp_path)
    client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "model": "m"})
    (tmp_path / "ai-settings.secret").unlink()
    settings = client.get("/companion/settings/ai").json()
    assert settings["key_set"] is False and settings["configured"] is False
    assert "Paste the key again" in caplog.text


def test_the_model_list_and_test_explain_what_is_missing(tmp_path):
    client, provider = start(tmp_path)
    missing = client.get("/companion/settings/ai/models")
    assert missing.status_code == 409 and missing.json()["detail"] == "Paste your Groq key first."
    assert client.post("/companion/settings/ai/test").json() == {
        "ok": False, "needs": "key", "message": "Paste your Groq key first."}
    draft = client.post("/companion/settings/ai/models", json={"provider": "groq", "api_key": KEY})
    assert draft.status_code == 200 and draft.json()["suggested"] == "openai/gpt-oss-120b"
    assert client.get("/companion/settings/ai").json()["key_set"] is False     # a draft is never saved
    assert provider.requests[-1]["auth"] == f"Bearer {KEY}"


def test_ask_with_fake_llm_uses_the_gateway_instructions_and_schema(tmp_path):
    client, *_ = fixture(tmp_path)
    fake = FakeLLM([{"status": "answered", "claims": [{"text": "A subnet divides a network.", "citations": ["p1"]}]},
                    {"status": "answered", "claims": [{"text": "Invented.", "citations": ["p99"]}]},
                    LLMError("The key was rejected.")])
    client.app.dependency_overrides[get_llm] = lambda: fake
    assert client.get("/companion/answers/status").json() == {"configured": True}
    result = ask(client).json()
    assert result["status"] == "answered" and result["claims"][0]["citations"] == ["p1"]
    call = fake.calls[0]
    assert call["system"] == ANSWER_INSTRUCTIONS and call["schema"] == ProviderAnswer.model_json_schema()
    sent = json.loads(call["user"].split("\n", 1)[1])["context"]
    assert sent["question"] == "What is a prefix?" and [p["id"] for p in sent["passages"]] == ["p1", "p2"]
    invented = ask(client)
    assert invented.status_code == 502 and "linked" in invented.json()["detail"]
    rejected = ask(client)
    assert rejected.status_code == 502 and rejected.json()["detail"] == "The key was rejected."


def test_the_answer_gateway_still_wins_when_it_is_set(tmp_path):
    _, database, library, upstream = fixture(tmp_path)
    gateway = HTTPAnswerProvider("https://answer.example.test/generate", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "insufficient_evidence", "claims": []})))
    client = TestClient(create_app("http://podfetch.test", database, library, upstream, gateway))
    fake = FakeLLM([])
    client.app.dependency_overrides[get_llm] = lambda: fake
    assert ask(client).json()["status"] == "insufficient_evidence"
    assert fake.calls == []


def test_features_read_the_input_limit(tmp_path):
    assert input_limit(None) == 60_000 and input_limit(FakeLLM()) == 60_000
    client, _ = start(tmp_path)
    client.put("/companion/settings/ai", json={"provider": "groq", "api_key": KEY, "model": "m"})
    llm = store.build_llm(store.load(tmp_path))
    assert input_limit(llm) == 20_000 and llm.max_input_chars == 20_000
    client.put("/companion/settings/ai", json={"max_input_chars": 30_000})
    assert input_limit(store.build_llm(store.load(tmp_path))) == 30_000


def test_with_login_on_nothing_about_ai_answers_without_a_login(tmp_path, monkeypatch):
    """PodFetch with login on: every AI route, and any route that only asks for get_llm, is refused."""
    login = "Basic " + base64.b64encode(b"alice:alice-password").decode()

    def podfetch(request):
        if request.headers.get("authorization") != login:
            return httpx.Response(401)
        if request.url.path == "/api/v1/users/me":
            return httpx.Response(200, json={"id": "7", "username": "alice", "role": "admin", "readOnly": False})
        return httpx.Response(404)
    from companion import routes
    (tmp_path / "zz_only_llm.py").write_text("""
from fastapi import APIRouter, Depends
from companion.llm import get_llm
router = APIRouter()
@router.get("/companion/probe-only-llm")
def probe(llm=Depends(get_llm)):
    return {"ai": llm is not None}
""", encoding="utf-8")
    monkeypatch.setattr(routes, "__path__", [*routes.__path__, str(tmp_path)])
    monkeypatch.delitem(sys.modules, "companion.routes.zz_only_llm", raising=False)
    client = TestClient(create_app("http://podfetch.test", tmp_path / "companion.db",
                                   transport=httpx.MockTransport(podfetch)))
    client.app.state.llm_transport = httpx.MockTransport(Provider())
    client.app.state.llm_resolver = resolve
    refused = [client.get("/companion/settings/ai"), client.put("/companion/settings/ai", json={"api_key": KEY}),
               client.post("/companion/settings/ai/test"), client.get("/companion/settings/ai/models"),
               client.post("/companion/settings/ai/models", json={"api_key": KEY}),
               client.get("/companion/answers/status"), client.get("/companion/probe-only-llm")]
    assert [r.status_code for r in refused] == [401] * len(refused)
    assert not (tmp_path / "ai-settings.json").exists()
    headers = {"Authorization": login}
    assert client.put("/companion/settings/ai", json={"api_key": KEY, "model": "m"}, headers=headers).status_code == 200
    assert client.get("/companion/probe-only-llm", headers=headers).json() == {"ai": True}
    assert client.get("/companion/probe-only-llm").status_code == 401
