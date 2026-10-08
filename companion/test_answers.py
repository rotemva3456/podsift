import json

import httpx
import pytest
from fastapi.testclient import TestClient

from companion.answers import (AnswerInput, HTTPAnswerProvider, ProviderError,
                               answer_context, select_context)
from companion.server import create_app
from companion.test_server import EPISODE, fixture


def configured_client(tmp_path, reply):
    _, database, library, upstream = fixture(tmp_path)
    requests = []

    def gateway(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return reply(payload) if callable(reply) else httpx.Response(200, json=reply)

    provider = HTTPAnswerProvider('https://answer.example.test/generate', transport=httpx.MockTransport(gateway))
    return TestClient(create_app('http://podfetch.test', database, library, upstream, provider)), requests


def ask(client, **changes):
    return client.post(f'/companion/episodes/{EPISODE}/answer', json={
        'question': 'What is a prefix?', 'position': 30, **changes,
    })


def test_unconfigured_returns_real_context_without_fabricating_answer(tmp_path):
    client, *_ = fixture(tmp_path)
    assert client.get('/companion/answers/status').json() == {'configured': False}
    result = ask(client).json()
    assert result['status'] == 'not_configured'
    assert result['claims'] == []
    assert result['position'] == 30
    assert any(p['start'] == 20 and p['end'] == 45 for p in result['passages'])
    assert all(p['episode_id'] == EPISODE for p in result['passages'])


def test_gateway_receives_bounded_context_and_returns_only_source_linked_claims(tmp_path):
    client, requests = configured_client(tmp_path, {
        'status': 'answered', 'claims': [{'text': 'A prefix defines the subnet size.', 'citations': ['p2']}],
    })
    response = ask(client)
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'answered'
    assert result['claims'][0]['citations'] == ['p2']
    assert result['passages'][1]['text'] == 'A prefix defines its size.'
    assert client.get('/companion/answers/status').json() == {'configured': True}
    assert len(requests) == 1
    context = requests[0]['context']
    assert context['episode_id'] == EPISODE and context['position'] == 30
    assert set(context) == {'episode_id', 'title', 'position', 'question', 'transcript_timed', 'passages'}
    assert client.get('/companion/notes').json() == []


@pytest.mark.parametrize('reply', [
    {'status': 'answered', 'claims': [{'text': 'Uncited answer.', 'citations': []}]},
    {'status': 'answered', 'claims': [{'text': 'Wrong episode.', 'citations': ['another-episode-p1']}]},
    {'status': 'answered', 'claims': []},
    {'status': 'insufficient_evidence', 'claims': [{'text': 'Still answering.', 'citations': ['p1']}]},
    {'status': 'answered', 'claims': [{'text': 'Forged clock.', 'citations': ['p1'], 'start': 900}]},
    {'status': 'answered', 'claims': [{'text': '   ', 'citations': ['p1']}]},
    {'status': 'answered', 'claims': [{'text': 'Source.', 'citations': ['p1']}], 'passages': []},
])
def test_malformed_or_unlinked_provider_answers_are_not_displayable(tmp_path, reply):
    client, requests = configured_client(tmp_path, reply)
    result = ask(client)
    assert result.status_code == 502
    assert 'claims' not in result.json()
    assert len(requests) == 1


def test_insufficient_evidence_is_explicit(tmp_path):
    client, _ = configured_client(tmp_path, {'status': 'insufficient_evidence', 'claims': []})
    result = ask(client).json()
    assert result['status'] == 'insufficient_evidence'
    assert result['claims'] == [] and result['passages']


@pytest.mark.parametrize('changes', [
    {'position': -1}, {'position': 121}, {'position': 'NaN'}, {'question': '  '},
    {'question': 'x' * 1001}, {'episode_id': 'another-episode'}, {'passages': []},
])
def test_invalid_input_never_calls_provider(tmp_path, changes):
    client, requests = configured_client(tmp_path, {})
    assert ask(client, **changes).status_code == 422
    assert requests == []


def test_missing_timestamps_do_not_trigger_generation(tmp_path):
    client, requests = configured_client(tmp_path, {})
    (tmp_path / 'library/sample/one.timed.json').unlink()
    result = ask(client).json()
    assert result['status'] == 'no_timed_transcript'
    assert result['passages'] == []
    assert requests == []


def test_changed_media_rejects_question_before_provider(tmp_path):
    client, requests = configured_client(tmp_path, {})
    timed = tmp_path / 'library/sample/one.timed.json'
    doc = json.loads(timed.read_text())
    doc['media'] = {'source_url': 'https://example.test/replaced.mp3', 'duration_seconds': 120}
    timed.write_text(json.dumps(doc))
    assert ask(client).status_code == 409
    assert requests == []


def test_context_keeps_active_passage_and_distant_topic_with_strict_budget():
    segments = [{'start': i * 2, 'end': (i + 1) * 2, 'text': f'Nearby explanation {i} ' + 'x' * 1900}
                for i in range(300)]
    segments[-1]['text'] = 'VLAN tagging separates broadcast domains.'
    context = select_context({'episode_id': EPISODE, 'name': 'Example', 'total_time': 600},
                             {'segments': segments}, AnswerInput(question='Explain VLAN tagging', position=21))
    assert any(p.start == 20 for p in context.passages)
    assert any('VLAN tagging' in p.text for p in context.passages)
    assert len(context.passages) <= 8
    assert sum(len(p.text) for p in context.passages) <= 8000
    assert all(len(p.text) <= 1800 for p in context.passages)


def test_gap_is_not_falsely_reported_as_missing_transcript():
    context = select_context({'episode_id': EPISODE, 'name': 'Example', 'total_time': 600},
                             {'segments': [{'start': 0, 'end': 10, 'text': 'Introduction.'}]},
                             AnswerInput(question='What was just explained?', position=500))
    assert answer_context(context, None).status == 'insufficient_evidence'


def test_native_missing_end_uses_next_start_and_keeps_fractional_source_times():
    context = select_context({'episode_id': EPISODE, 'name': 'Native', 'total_time': 120}, {'segments': [
        {'start': 0.125, 'end': None, 'text': 'First.'},
        {'start': 20.875, 'end': None, 'text': 'Second.'},
        {'start': -1, 'end': 10, 'text': 'Invalid.'},
        {'start': 121, 'end': 122, 'text': 'Past duration.'},
    ]}, AnswerInput(question='First', position=10))
    assert context.passages[0].start == .125
    assert context.passages[0].end == 20.875
    assert all(p.start >= 0 and p.start < 120 for p in context.passages)


@pytest.mark.parametrize('mode', ['timeout', 'unavailable', 'oversized', 'redirect', 'invalid_json'])
def test_provider_failures_are_bounded_sanitized_and_not_retried(tmp_path, mode):
    def reply(payload):
        if mode == 'timeout':
            raise httpx.ReadTimeout('secret vendor detail')
        if mode == 'unavailable':
            return httpx.Response(401, text='secret vendor detail')
        if mode == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://example.test/leak'})
        return httpx.Response(200, text='x' * 33000 if mode == 'oversized' else 'secret vendor detail')
    client, requests = configured_client(tmp_path, reply)
    response = ask(client)
    assert response.status_code == 502
    assert 'secret' not in response.text
    assert len(requests) == 1


def test_provider_configuration_disallows_insecure_remote_and_embedded_credentials():
    for url in ['http://example.test/generate', 'https://user:pass@example.test/generate', 'file:///tmp/answer']:
        with pytest.raises(ValueError):
            HTTPAnswerProvider(url)
    HTTPAnswerProvider('http://127.0.0.1:19000/answer')
