"""The OpenAI-compatible provider, offline: httpx.MockTransport and a fake DNS resolver."""
import json
import logging

import httpx
import pytest

from companion.llm import LLMError
from companion.providers import jsonschema, openai_compat
from companion.providers.openai_compat import OpenAICompatLLM, OpenAICompatLLMWithSpeech, extract_json

KEY = "gsk_TESTKEY_0123456789abcdefSECRET"
PUBLIC = "104.18.2.2"
OK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
             "additionalProperties": False}
HOSTS = {"api.groq.com": [PUBLIC], "metadata.internal": ["169.254.169.254"], "aws6.internal": ["fd00:ec2::254"],
         "nas.lan": ["192.168.1.20"], "mixed.example": [PUBLIC, "169.254.169.254"], "public.example": [PUBLIC]}


@pytest.fixture(autouse=True)
def fresh_mode_memory():
    openai_compat._WORKING_MODE.clear()
    yield
    openai_compat._WORKING_MODE.clear()


def resolver(host, port):
    if host not in HOSTS:
        raise OSError("no such host")
    return HOSTS[host]


def chat_reply(content, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                  "finish_reason": finish}]})


def provider(handler, *, base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-20b", waits=None, **kw):
    requests = []

    def record(request):
        body = request.content
        requests.append({"method": request.method, "url": str(request.url), "host": request.headers.get("host"),
                         "auth": request.headers.get("authorization"), "sni": request.extensions.get("sni_hostname"),
                         "json": json.loads(body) if body and request.headers.get("content-type") == "application/json" else None,
                         "raw": body})
        return handler(request)

    llm = OpenAICompatLLM(base_url=base_url, model=model, api_key=KEY, provider="groq",
                          token_param="max_completion_tokens", transport=httpx.MockTransport(record),
                          resolver=resolver, sleep=(waits.append if waits is not None else lambda s: None), **kw)
    return llm, requests


def ask(llm, schema=OK_SCHEMA):
    return llm.complete_json(system="Check the connection.", user="<evidence>text</evidence>", schema=schema,
                             max_tokens=50)


# ── JSON modes ───────────────────────────────────────────────────────────────

def test_the_schema_path_sends_json_schema_with_refs_inlined_and_validates_the_answer():
    schema = {"$defs": {"Claim": {"type": "object", "properties": {"text": {"type": "string"}},
                                  "required": ["text"], "additionalProperties": False}},
              "type": "object", "properties": {"claims": {"type": "array", "items": {"$ref": "#/$defs/Claim"}}},
              "required": ["claims"], "additionalProperties": False}
    llm, requests = provider(lambda r: chat_reply('{"claims": [{"text": "A prefix sets the size."}]}'))
    assert ask(llm, schema) == {"claims": [{"text": "A prefix sets the size."}]}
    sent = requests[0]["json"]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"]["properties"]["claims"]["items"]["required"] == ["text"]
    assert "$ref" not in json.dumps(sent["response_format"])
    assert sent["max_completion_tokens"] == 50 and sent["model"] == "openai/gpt-oss-20b"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert llm.mode == "json_schema"
    # pinned to the checked address, with the real host name for HTTP and TLS
    assert requests[0]["url"] == f"https://{PUBLIC}/openai/v1/chat/completions"
    assert requests[0]["host"] == "api.groq.com" and requests[0]["sni"] == "api.groq.com"
    assert requests[0]["auth"] == f"Bearer {KEY}"


def test_a_refused_json_schema_falls_back_to_json_mode_with_the_schema_in_the_prompt_and_remembers_it():
    def handler(request):
        body = json.loads(request.content)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "This model does not support response format "
                                                                  "`json_schema`.", "param": "response_format"}})
        return chat_reply('```json\n{"ok": true}\n```')
    llm, requests = provider(handler)
    assert ask(llm) == {"ok": True} and llm.mode == "json_object"
    assert [r["json"].get("response_format", {}).get("type") for r in requests] == ["json_schema", "json_object"]
    assert "JSON Schema" in requests[1]["json"]["messages"][0]["content"]
    assert '"required":["ok"]' in requests[1]["json"]["messages"][0]["content"]
    assert ask(llm) == {"ok": True}                       # the next call goes straight to JSON mode
    assert len(requests) == 3 and requests[2]["json"]["response_format"] == {"type": "json_object"}


def test_a_provider_without_any_response_format_gets_the_schema_in_the_prompt():
    def handler(request):
        if "response_format" in json.loads(request.content):
            return httpx.Response(400, json={"error": {"message": "Unknown field response_format"}})
        return chat_reply('<think>Let me see.</think>Sure! {"ok": false}')
    llm, requests = provider(handler)
    assert ask(llm) == {"ok": False} and llm.mode == "prompt"
    assert "response_format" not in requests[-1]["json"]


@pytest.mark.parametrize("content, finish, message", [
    ("not json at all", "stop", openai_compat.NOT_JSON),
    ('{"ok": "yes"}', "stop", openai_compat.WRONG_SHAPE),
    ('{"ok": true, "extra": 1}', "stop", openai_compat.WRONG_SHAPE),
    ('{"ok": tr', "length", openai_compat.CUT_OFF),
])
def test_answers_that_are_not_the_schema_are_refused(content, finish, message):
    llm, _ = provider(lambda r: chat_reply(content, finish))
    with pytest.raises(LLMError, match=message.split(".")[0]):
        ask(llm)


def test_absurdly_nested_json_is_refused_not_a_crash():
    deep = "[" * 100_000 + "]" * 100_000
    llm, _ = provider(lambda r: chat_reply('{"ok": ' + deep + "}"))
    with pytest.raises(LLMError, match="valid JSON"):
        ask(llm)
    llm, _ = provider(lambda r: httpx.Response(200, text=deep))
    with pytest.raises(LLMError, match="OpenAI-compatible"):
        ask(llm)


def test_a_refusal_and_a_non_openai_reply_have_clear_messages():
    llm, _ = provider(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None,
                                                                                    "refusal": "No."}}]}))
    with pytest.raises(LLMError, match="declined"):
        ask(llm)
    llm, _ = provider(lambda r: httpx.Response(200, text="<html>Welcome to nginx</html>"))
    with pytest.raises(LLMError, match="OpenAI-compatible"):
        ask(llm)


# ── errors, retries and limits ───────────────────────────────────────────────

@pytest.mark.parametrize("status, body, message", [
    (401, {"error": {"message": f"Incorrect API key provided: {KEY}", "code": "invalid_api_key"}}, "The key was rejected."),
    (404, {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}}, "Model not found."),
    (413, {"error": {"message": "Request too large for model on input tokens per minute (ITPM)"}},
     "Too much text for this provider. Lower the input limit or pick another model."),
    (429, {"error": {"message": "Rate limit reached on tokens per minute (TPM)"}},
     "Too much text for this provider. Lower the input limit or pick another model."),
    (429, {"error": {"message": "Rate limit reached on tokens per day (TPD)"}}, "daily limit"),
    (400, {"error": {"message": "This model's maximum context length is 8192 tokens"}}, "Too much text"),
    (400, {"error": {"message": f"bad value near {KEY}"}}, "refused the request"),
    (302, {}, "redirects"),
    (500, {"error": {"message": KEY}}, "HTTP 500"),
])
def test_http_errors_become_fixed_messages_that_never_repeat_the_provider_text(status, body, message, caplog):
    caplog.set_level(logging.DEBUG)
    llm, requests = provider(lambda r: httpx.Response(status, json=body, headers={"retry-after": "1"}))
    with pytest.raises(LLMError) as raised:
        ask(llm)
    assert message in str(raised.value)
    assert KEY not in str(raised.value) and KEY not in caplog.text
    assert len(requests) == {429: 5, 500: 2}.get(status, 1)    # 429: 4 more tries; 5xx: one; else none


def rate_limited(headers=None, message="Rate limit reached on tokens per minute (TPM)"):
    return httpx.Response(429, json={"error": {"message": message, "code": "rate_limit_exceeded"}},
                          headers=headers or {})


def test_several_429s_are_waited_out_then_the_answer_comes(caplog):
    """A long text in parts on a free tier: each part waits for the per-minute budget to refill."""
    caplog.set_level(logging.DEBUG)
    replies = iter([rate_limited({"retry-after": "5"}), rate_limited({"retry-after": "7"}),
                    rate_limited({"retry-after": "9"}), chat_reply('{"ok": true}')])
    waits = []
    llm, requests = provider(lambda r: next(replies), waits=waits)
    assert ask(llm) == {"ok": True}
    assert waits == [5.0, 7.0, 9.0] and len(requests) == 4
    assert "waiting 5.0 s" in caplog.text and KEY not in caplog.text and "<evidence>" not in caplog.text


def test_the_waits_stop_at_150_seconds_with_the_usual_message():
    waits = []
    llm, requests = provider(lambda r: rate_limited({"retry-after": "60"}), waits=waits)
    with pytest.raises(LLMError, match="Too much text for this provider. Lower the input limit or pick another model."):
        ask(llm)
    assert waits == [60.0, 60.0] and len(requests) == 3        # a third minute would pass 150 s
    waits.clear()
    llm, requests = provider(lambda r: rate_limited({"retry-after": "300"}), waits=waits)
    with pytest.raises(LLMError, match="Too much text"):
        ask(llm)
    assert waits == [] and len(requests) == 1                  # beyond the budget: fail at once


def test_without_retry_after_the_groq_reset_header_sets_the_wait():
    replies = iter([rate_limited({"x-ratelimit-remaining-tokens": "310", "x-ratelimit-reset-tokens": "7.66s",
                                  "x-ratelimit-reset-requests": "1m26.4s"}), chat_reply('{"ok": true}')])
    waits = []
    llm, requests = provider(lambda r: next(replies), waits=waits)
    assert ask(llm) == {"ok": True}
    assert waits == [pytest.approx(7.91)] and len(requests) == 2
    # Out of requests rather than tokens: the request budget's reset counts.
    replies = iter([rate_limited({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "1m26.4s",
                                  "x-ratelimit-reset-tokens": "2s"}), chat_reply('{"ok": true}')])
    waits.clear()
    llm, _ = provider(lambda r: next(replies), waits=waits)
    assert ask(llm) == {"ok": True} and waits == [pytest.approx(86.65)]


def test_without_any_hint_the_waits_double_then_stop():
    waits = []
    llm, requests = provider(lambda r: rate_limited(), waits=waits)
    with pytest.raises(LLMError, match="Too much text"):
        ask(llm)
    assert waits == [2.0, 4.0, 8.0, 16.0] and len(requests) == 5


def test_a_server_error_is_tried_only_once_more():
    waits = []
    llm, requests = provider(lambda r: httpx.Response(503, headers={"retry-after": "3"}), waits=waits)
    with pytest.raises(LLMError, match="HTTP 503"):
        ask(llm)
    assert waits == [3.0] and len(requests) == 2
    waits.clear()
    llm, requests = provider(lambda r: httpx.Response(502, headers={"retry-after": "120"}), waits=waits)
    with pytest.raises(LLMError, match="HTTP 502"):
        ask(llm)
    assert waits == [] and len(requests) == 1


def test_a_generation_that_fails_groqs_json_check_is_tried_once_more():
    failed = httpx.Response(400, json={"error": {"message": "Failed to generate JSON. Please adjust your prompt. "
                                                  "See 'failed_generation' for more details.",
                                       "type": "invalid_request_error", "code": "json_validate_failed",
                                       "failed_generation": '{"ok": tr'}})
    replies = iter([failed, chat_reply('{"ok": true}')])
    waits = []
    llm, requests = provider(lambda r: next(replies), waits=waits)
    assert ask(llm) == {"ok": True} and len(requests) == 2 and waits == []
    assert llm.mode == "json_schema" and openai_compat._WORKING_MODE.get((llm.base_url, llm.model), 0) == 0  # not downgraded
    llm, requests = provider(lambda r: failed)
    with pytest.raises(LLMError, match="wasn't valid JSON"):
        ask(llm)
    assert len(requests) == 2


def test_reset_durations_and_retry_after_dates_parse():
    assert [openai_compat._duration(v) for v in ("7.66s", "1m26.4s", "150ms", "2m", "12")] == \
        [7.66, pytest.approx(86.4), pytest.approx(0.15), 120.0, 12.0]
    assert [openai_compat._duration(v) for v in ("", "soon", "1x", "-3")] == [None, None, None, None]
    assert openai_compat._retry_after(httpx.Headers({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})) == 0.0
    assert openai_compat.rate_limit_wait(httpx.Headers({})) is None


def test_an_oversized_reply_is_refused():
    llm, _ = provider(lambda r: chat_reply("x" * (300 * 1024)))
    with pytest.raises(LLMError, match="larger than 256 KB"):
        ask(llm)
    llm, _ = provider(lambda r: httpx.Response(200, content=b"{}", headers={"content-length": str(10 ** 7)}))
    with pytest.raises(LLMError, match="larger than 256 KB"):
        ask(llm)


def test_timeouts_and_unreachable_servers_have_clear_messages():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)
    llm, _ = provider(slow)
    with pytest.raises(LLMError, match="longer than 60 seconds"):
        ask(llm)
    llm, _ = provider(down)
    with pytest.raises(LLMError, match="Couldn't reach the AI provider at api.groq.com"):
        ask(llm)


# ── where requests may go ────────────────────────────────────────────────────

@pytest.mark.parametrize("base_url, message", [
    ("http://169.254.169.254/v1", "cloud metadata"),
    ("https://metadata.internal/v1", "cloud metadata"),
    ("https://aws6.internal/v1", "cloud metadata"),
    ("http://[fd00:ec2::254]/v1", "cloud metadata"),
    ("https://mixed.example/v1", "cloud metadata"),          # every DNS answer must pass
    ("http://public.example/v1", "Use https://"),            # the key would cross the internet in the clear
    ("https://nowhere.example/v1", "Couldn't find the server"),
])
def test_blocked_addresses_are_never_contacted(base_url, message):
    llm, requests = provider(lambda r: chat_reply('{"ok": true}'), base_url=base_url)
    with pytest.raises(LLMError, match=message):
        ask(llm)
    assert requests == []


@pytest.mark.parametrize("base_url, pinned", [
    ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1/chat/completions"),
    ("http://nas.lan:11434/v1", "http://192.168.1.20:11434/v1/chat/completions"),
    ("http://[::1]:11434/v1", "http://[::1]:11434/v1/chat/completions"),
])
def test_localhost_and_the_lan_are_allowed_for_ollama(base_url, pinned):
    llm, requests = provider(lambda r: chat_reply('{"ok": true}'), base_url=base_url)
    assert ask(llm) == {"ok": True}
    assert requests[0]["url"] == pinned


def test_address_problem_names_the_reason_but_allows_a_server_that_isnt_up_yet():
    assert "cloud metadata" in openai_compat.address_problem("http://169.254.169.254/v1", resolver)
    assert openai_compat.address_problem("http://ollama:11434/v1", resolver) is None      # not resolvable yet
    assert openai_compat.address_problem("http://nas.lan/v1", resolver) is None


# ── models and speech ────────────────────────────────────────────────────────

def test_the_model_list_keeps_only_models_that_can_chat():
    data = [{"id": "openai/gpt-oss-20b", "context_window": 131072}, {"id": "whisper-large-v3"},
            {"id": "canopylabs/orpheus-v1-english"}, {"id": "meta-llama/llama-prompt-guard-2-22m"},
            {"id": "text-embedding-3-small"}, {"id": "qwen/qwen3.8-27b", "context_length": 131072},
            {"id": "retired", "active": False}, {"id": ""}, "junk"]
    llm, requests = provider(lambda r: httpx.Response(200, json={"object": "list", "data": data}))
    assert llm.list_models() == [{"id": "openai/gpt-oss-20b", "context": 131072},
                                 {"id": "qwen/qwen3.8-27b", "context": 131072}]
    assert requests[0]["method"] == "GET" and requests[0]["url"].endswith("/openai/v1/models")


def test_transcribe_audio_exists_only_for_speech_providers_and_never_sends_opus(tmp_path):
    plain, _ = provider(lambda r: chat_reply("{}"))
    assert getattr(plain, "transcribe_audio", None) is None
    seen = []

    def handler(request):
        seen.append(request.content)
        return httpx.Response(200, json={"text": " Hello there. ", "duration": 15.0, "segments": [
            {"start": 0.0, "end": 2.5, "text": " Hello there."}, {"start": "bad"}]})
    llm = OpenAICompatLLMWithSpeech(base_url="https://api.groq.com/openai/v1", model="m", api_key=KEY,
                                    speech_model="whisper-large-v3-turbo", transport=httpx.MockTransport(handler),
                                    resolver=resolver)
    clip = tmp_path / "window-0.mp3"
    clip.write_bytes(b"ID3" + b"\0" * 64)
    assert llm.transcribe_audio(str(clip)) == {"text": "Hello there.", "duration": 15.0,
                                               "segments": [{"start": 0.0, "end": 2.5, "text": "Hello there."}]}
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in seen[0]
    assert b'name="response_format"\r\n\r\nverbose_json' in seen[0] and b'filename="window-0.mp3"' in seen[0]
    opus = tmp_path / "clip.mp3"
    opus.write_bytes(b"OggS" + b"\0" * 64)
    with pytest.raises(LLMError, match="mp3, wav or flac"):
        llm.transcribe_audio(str(opus))
    assert len(seen) == 1


def test_the_key_stays_out_of_repr_and_logs(caplog):
    caplog.set_level(logging.DEBUG)
    llm, _ = provider(lambda r: chat_reply('{"ok": true}'))
    ask(llm)
    assert KEY not in repr(llm) and KEY not in caplog.text


# ── helpers ──────────────────────────────────────────────────────────────────

def test_extract_json_finds_the_object():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('<think>{"no": 1}</think>\nAnswer: {"a": 1}') == {"a": 1}
    assert extract_json("[1, 2]") is None and extract_json("") is None


def test_the_schema_check_covers_what_pydantic_writes():
    from companion.answers import ProviderAnswer
    schema = ProviderAnswer.model_json_schema()
    good = {"status": "answered", "claims": [{"text": "A claim.", "citations": ["p1"]}]}
    assert jsonschema.errors(good, schema) == []
    assert jsonschema.errors({"status": "maybe", "claims": []}, schema)
    assert jsonschema.errors({"status": "answered", "claims": [{"text": "x", "citations": []}]}, schema)
    assert jsonschema.errors({"status": "answered", "claims": [], "passages": []}, schema)
    assert jsonschema.errors({"n": True}, {"properties": {"n": {"type": "integer"}}})      # a bool is no number
    assert jsonschema.errors({"n": None}, {"properties": {"n": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}) == []
    assert jsonschema.errors(5, {"type": "integer", "minimum": 0, "maximum": 3})
    recursive = {"$defs": {"N": {"type": "object", "properties": {"n": {"$ref": "#/$defs/N"}}}}, "$ref": "#/$defs/N"}
    with pytest.raises(jsonschema.SchemaError):
        jsonschema.inline_refs(recursive)
    assert jsonschema.errors({"n": {"n": {}}}, recursive) == []
